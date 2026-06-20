from datetime import timedelta

import pytest
from django.utils import timezone

from core.users.models import (
    AccountDeletionRequest,
    AccountDeletionStatus,
    User,
    UserLifecycleState,
)
from core.users.tasks import purge_due_account_deletions


@pytest.mark.django_db
def test_due_account_deletion_is_purged_and_anonymized(create_user, monkeypatch):
    user = create_user(email="purge@example.com", username="purgeuser")
    user.lifecycle_state = UserLifecycleState.PENDING_DELETION
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])
    deletion_request = AccountDeletionRequest.objects.create(
        user=user,
        status=AccountDeletionStatus.PENDING,
        requested_at=timezone.now() - timedelta(days=31),
        scheduled_for=timezone.now() - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "core.users.account_lifecycle.delete_user_gcs_objects",
        lambda _user: 2,
    )

    result = purge_due_account_deletions.run()

    assert result == {"claimed": 1, "completed": 1, "failed": 0}
    deletion_request.refresh_from_db()
    assert deletion_request.status == AccountDeletionStatus.COMPLETED
    assert deletion_request.completed_at is not None
    tombstone = User.all_objects.get(id=user.id)
    assert tombstone.lifecycle_state == UserLifecycleState.DELETED
    assert tombstone.deleted_at is not None
    assert tombstone.email.startswith("deleted-")


@pytest.mark.django_db
def test_purge_failure_is_retryable_without_restoring_visibility(create_user, monkeypatch):
    user = create_user(email="retry-purge@example.com", username="retrypurge")
    user.lifecycle_state = UserLifecycleState.PENDING_DELETION
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])
    deletion_request = AccountDeletionRequest.objects.create(
        user=user,
        status=AccountDeletionStatus.PENDING,
        requested_at=timezone.now() - timedelta(days=31),
        scheduled_for=timezone.now() - timedelta(minutes=5),
    )

    def fail_storage(_user):
        raise ConnectionError("temporary storage outage")

    monkeypatch.setattr(
        "core.users.account_lifecycle.delete_user_gcs_objects",
        fail_storage,
    )

    result = purge_due_account_deletions.run()

    assert result == {"claimed": 1, "completed": 0, "failed": 1}
    deletion_request.refresh_from_db()
    assert deletion_request.status == AccountDeletionStatus.FAILED
    assert deletion_request.retry_count == 1
    assert deletion_request.failure_code == "CONNECTIONERROR"
    retained_user = User.all_objects.get(id=user.id)
    assert retained_user.lifecycle_state == UserLifecycleState.PENDING_DELETION
    assert not User.objects.filter(id=user.id).exists()
