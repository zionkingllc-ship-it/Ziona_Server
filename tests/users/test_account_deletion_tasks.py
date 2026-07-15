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


def _pending_request(user):
    return AccountDeletionRequest.objects.create(
        user=user,
        status=AccountDeletionStatus.PENDING,
        requested_at=timezone.now() - timedelta(days=31),
        scheduled_for=timezone.now() - timedelta(hours=1),
    )


@pytest.mark.django_db
def test_purge_scrubs_donor_pii_but_keeps_financial_record(create_user, monkeypatch):
    from core.donations.models import Donation, DonationType, SupporterIdentity

    user = create_user(email="donor@example.com", username="donoruser")
    user.lifecycle_state = UserLifecycleState.PENDING_DELETION
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])
    identity = SupporterIdentity.objects.create(
        user=user,
        normalized_email="donor@example.com",
        contact_email="donor@example.com",
        display_name="Real Donor Name",
    )
    donation = Donation.objects.create(
        user=user,
        supporter_identity=identity,
        donor_name="Real Donor Name",
        donor_email="donor@example.com",
        amount=1500,
        type=DonationType.ONE_TIME,
        stripe_payment_intent_id="pi_keepme_123",
    )
    _pending_request(user)
    monkeypatch.setattr("core.users.account_lifecycle.delete_user_gcs_objects", lambda _u: 0)

    assert purge_due_account_deletions.run()["completed"] == 1

    identity.refresh_from_db()
    donation.refresh_from_db()
    # PII scrubbed
    assert identity.normalized_email.startswith("deleted-")
    assert identity.contact_email == ""
    assert identity.display_name == ""
    assert donation.donor_name == ""
    assert donation.donor_email == ""
    # financial record kept intact
    assert donation.amount == 1500
    assert donation.type == DonationType.ONE_TIME
    assert donation.stripe_payment_intent_id == "pi_keepme_123"


@pytest.mark.django_db
def test_purge_retains_moderation_history(create_user, monkeypatch):
    from core.admin_dashboard.models import ModerationAction, ModerationActionType

    user = create_user(email="modhist@example.com", username="modhistuser")
    user.lifecycle_state = UserLifecycleState.PENDING_DELETION
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])
    action = ModerationAction.objects.create(
        user=user, action_type=ModerationActionType.SUSPENDED, reason="policy violation"
    )
    _pending_request(user)
    monkeypatch.setattr("core.users.account_lifecycle.delete_user_gcs_objects", lambda _u: 0)

    purge_due_account_deletions.run()

    # Retained (points at the now-anonymized user) — no longer hard-deleted.
    assert ModerationAction.objects.filter(id=action.id).exists()


@pytest.mark.django_db
def test_stale_purging_reclaim_increments_retry_count(create_user):
    from core.users.tasks import _claim_due_requests

    user = create_user(email="stale@example.com", username="staleuser")
    req = AccountDeletionRequest.objects.create(
        user=user,
        status=AccountDeletionStatus.PURGING,
        requested_at=timezone.now() - timedelta(days=31),
        scheduled_for=timezone.now() - timedelta(hours=2),
        retry_count=0,
    )
    # Backdate updated_at (auto_now) so it counts as a stale/crashed PURGING row.
    AccountDeletionRequest.objects.filter(id=req.id).update(
        updated_at=timezone.now() - timedelta(hours=2)
    )

    claimed = _claim_due_requests(20)

    assert str(req.id) in claimed
    req.refresh_from_db()
    assert req.retry_count == 1  # a crash-loop now counts toward the cap


@pytest.mark.django_db
def test_stale_purging_excluded_once_retries_exhausted(create_user, settings):
    from core.users.tasks import _claim_due_requests

    user = create_user(email="exhausted@example.com", username="exhausteduser")
    req = AccountDeletionRequest.objects.create(
        user=user,
        status=AccountDeletionStatus.PURGING,
        requested_at=timezone.now() - timedelta(days=31),
        scheduled_for=timezone.now() - timedelta(hours=2),
        retry_count=settings.ACCOUNT_DELETION_PURGE_MAX_RETRIES,
    )
    AccountDeletionRequest.objects.filter(id=req.id).update(
        updated_at=timezone.now() - timedelta(hours=2)
    )

    # A crash-looping purge that hit the cap is not reclaimed again (no infinite loop).
    assert str(req.id) not in _claim_due_requests(20)
