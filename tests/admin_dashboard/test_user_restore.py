"""Undoing an admin's Delete, and finishing a deleted account off.

Delete is a soft delete: the account is flagged, hidden and signed out, but its
data is intact. restoreUser brings it back; permanentlyDeleteUser can now also
anonymize an already soft-deleted account, so its owner can sign up afresh.
"""

import json
import uuid
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from core.admin_dashboard.models import AdminAuditLog, ModerationAction
from core.admin_dashboard.user_services import UserManagementService
from core.follows.models import Follow
from core.posts.models import Post
from core.shared.exceptions import AdminError, ErrorCode
from core.users.models import User, UserStatus

PASSWORD = "Church12345!"  # pragma: allowlist secret


@pytest.fixture
def admin(create_admin_user):
    return create_admin_user()


@pytest.fixture
def church(create_user):
    user = create_user(email="church@example.com", username="jesushousebaltimore")
    user.set_password(PASSWORD)
    user.is_email_verified = True
    user.save(update_fields=["password", "is_email_verified"])
    fan = create_user(email="fan@example.com", username="fan")
    Follow.objects.create(follower=fan, following=user)
    for index in range(2):
        Post.objects.create(user=user, post_type="text", caption=f"sermon {index}")
    return user


@pytest.fixture(autouse=True)
def _no_storage(monkeypatch):
    """Permanent deletion removes media from GCS; keep tests off the network."""
    monkeypatch.setattr("core.users.account_lifecycle.delete_user_gcs_objects", lambda user: 0)


def _soft_delete(user, admin):
    UserManagementService.delete_user(str(user.id), admin)
    user.refresh_from_db()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restore_brings_back_the_account_its_posts_follows_and_login(church, admin):
    from core.authentication.services import AuthService

    _soft_delete(church, admin)
    assert not User.objects.filter(id=church.id).exists()
    assert Post.objects.filter(user_id=church.id).count() == 0

    result = UserManagementService.restore_user(str(church.id), admin, reason="Deleted by mistake")

    assert result["user"]["account_state"] == "active"
    assert User.objects.filter(id=church.id).exists()
    assert Post.objects.filter(user_id=church.id).count() == 2
    assert Follow.objects.filter(following=church).count() == 1
    assert AuthService.login("church@example.com", PASSWORD)["access_token"]


def test_restore_is_audited(church, admin):
    _soft_delete(church, admin)

    UserManagementService.restore_user(str(church.id), admin, reason="Deleted by mistake")

    log = AdminAuditLog.objects.get(action="USER_RESTORED", target_id=str(church.id))
    assert log.admin_user_id == admin.id
    assert log.details["reason"] == "Deleted by mistake"
    assert "restored_from_deleted_at" in log.details
    assert ModerationAction.objects.filter(user=church, reason="Deleted by mistake").exists()


def test_restore_keeps_the_moderation_status_it_had(church, admin):
    """A suspension is not lifted by undoing a delete."""
    from core.authentication.services import AuthService
    from core.authentication.validators import AuthenticationError

    UserManagementService.suspend_user(str(church.id), "Spam", admin)
    _soft_delete(church, admin)

    UserManagementService.restore_user(str(church.id), admin)

    church.refresh_from_db()
    assert church.status == UserStatus.SUSPENDED
    with pytest.raises(AuthenticationError) as raised:
        AuthService.login("church@example.com", PASSWORD)
    assert raised.value.code == "ACCOUNT_SUSPENDED"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("live", ErrorCode.VALIDATION_ERROR),
        ("restored_twice", ErrorCode.VALIDATION_ERROR),
        ("permanently_deleted", ErrorCode.USER_NOT_RESTORABLE),
        ("unknown", ErrorCode.USER_NOT_FOUND),
        ("malformed", ErrorCode.USER_NOT_FOUND),
    ],
)
def test_restore_refuses_anything_but_a_soft_deleted_account(church, admin, setup, code):
    user_id = str(church.id)
    if setup == "restored_twice":
        _soft_delete(church, admin)
        UserManagementService.restore_user(user_id, admin)
    elif setup == "permanently_deleted":
        UserManagementService.permanently_delete_user(
            user_id, "Policy", "church@example.com", True, admin
        )
    elif setup == "unknown":
        user_id = str(uuid.uuid4())
    elif setup == "malformed":
        user_id = "not-a-uuid"

    with pytest.raises(AdminError) as raised:
        UserManagementService.restore_user(user_id, admin)

    assert raised.value.code == code


def test_restore_leaves_a_users_own_pending_deletion_alone(church, admin):
    """A user-requested deletion is cancelled by the user, not undone by an admin."""
    from core.users.models import UserLifecycleState

    User.all_objects.filter(id=church.id).update(
        lifecycle_state=UserLifecycleState.PENDING_DELETION
    )

    with pytest.raises(AdminError) as raised:
        UserManagementService.restore_user(str(church.id), admin)

    assert raised.value.code == ErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# What the dashboard is told it can do
# ---------------------------------------------------------------------------


def _actions_for(user_id) -> list[str]:
    result = UserManagementService.list_users(status_filter="deleted", page_size=50)
    rows = {row["id"]: row for row in result["users"]}
    return rows[str(user_id)]["available_actions"]


def test_soft_deleted_accounts_offer_restore_and_permanent_delete(church, admin):
    _soft_delete(church, admin)

    assert _actions_for(church.id) == ["restore", "permanently_delete"]


def test_permanently_deleted_accounts_offer_nothing(church, admin):
    UserManagementService.permanently_delete_user(
        str(church.id), "Policy", "church@example.com", True, admin
    )

    assert _actions_for(church.id) == []


# ---------------------------------------------------------------------------
# Permanently deleting an account that is already soft-deleted
# ---------------------------------------------------------------------------


def test_a_soft_deleted_account_can_be_permanently_deleted(church, admin):
    from core.users.account_lifecycle import is_anonymized

    _soft_delete(church, admin)

    UserManagementService.permanently_delete_user(
        str(church.id), "Owner asked for removal", "church@example.com", True, admin
    )

    tombstone = User.all_objects.get(id=church.id)
    assert is_anonymized(tombstone)
    assert AdminAuditLog.objects.filter(
        action="USER_PERMANENTLY_DELETED", target_id=str(church.id)
    ).exists()


def test_after_permanent_deletion_the_owner_signs_up_as_new(create_user, admin, settings):
    """The PM's ask: remove the account completely so the person can start over.

    Anonymization frees the email and detaches the Google/Apple identity, so the
    same sign-in creates a new account instead of hitting the deleted one.
    """
    settings.GOOGLE_CLIENT_IDS = ["test-client.apps.googleusercontent.com"]
    person = create_user(email="ria@example.com", username="user_a5867bcd")
    User.all_objects.filter(id=person.id).update(
        google_id="google-ria", social_auth_provider="google"
    )
    _soft_delete(person, admin)
    UserManagementService.permanently_delete_user(
        str(person.id), "Start over", "ria@example.com", True, admin
    )

    with patch("google.oauth2.id_token.verify_oauth2_token") as verify:
        verify.return_value = {
            "aud": "test-client.apps.googleusercontent.com",
            "email": "ria@example.com",
            "sub": "google-ria",
            "email_verified": True,
        }
        response = Client().post(
            reverse("authentication:google-oauth"),
            data=json.dumps({"id_token": "token"}),
            content_type="application/json",
        )

    body = response.json()
    assert response.status_code == 200, body
    assert body["data"]["isNewUser"] is True
    assert User.objects.get(email="ria@example.com").id != person.id


def test_permanent_delete_still_refuses_states_it_refused_before(church, admin):
    """Only admin soft-deletes were added; deactivated/pending accounts stay out of reach."""
    from core.users.models import UserLifecycleState

    for state in (UserLifecycleState.DEACTIVATED, UserLifecycleState.PENDING_DELETION):
        User.all_objects.filter(id=church.id).update(lifecycle_state=state)
        with pytest.raises(AdminError) as raised:
            UserManagementService.permanently_delete_user(
                str(church.id), "x", "church@example.com", True, admin
            )
        assert raised.value.code == ErrorCode.USER_NOT_FOUND


def test_permanent_delete_of_a_soft_deleted_account_still_needs_the_email(church, admin):
    _soft_delete(church, admin)

    with pytest.raises(AdminError) as raised:
        UserManagementService.permanently_delete_user(
            str(church.id), "x", "wrong@example.com", True, admin
        )

    assert raised.value.code == ErrorCode.VALIDATION_ERROR
    assert User.all_objects.get(id=church.id).email == "church@example.com"


# ---------------------------------------------------------------------------
# GraphQL: the mutation the dashboard calls
# ---------------------------------------------------------------------------

RESTORE = """
mutation Restore($userId: String!, $reason: String!) {
  restoreUser(userId: $userId, reason: $reason) {
    success
    user { id accountState availableActions }
    error { code message }
  }
}
"""


def _graphql(token: str, user_id: str) -> dict:
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {"query": RESTORE, "variables": {"userId": user_id, "reason": "Deleted by mistake"}}
        ),
        content_type="application/json",
    )
    return response.json()


def test_restore_user_mutation(church, authenticated_admin):
    _soft_delete(church, authenticated_admin["user"])

    body = _graphql(authenticated_admin["access_token"], str(church.id))

    payload = body["data"]["restoreUser"]
    assert payload["success"] is True, body
    assert payload["user"]["accountState"] == "active"
    assert "restore" not in payload["user"]["availableActions"]


def test_restore_user_mutation_reports_why_it_refused(church, authenticated_admin):
    body = _graphql(authenticated_admin["access_token"], str(church.id))  # not deleted

    payload = body["data"]["restoreUser"]
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("caller", ["regular_user", "anonymous"])
def test_restore_user_mutation_is_admin_only(church, admin, authenticated_user, caller):
    _soft_delete(church, admin)
    token = authenticated_user["access_token"] if caller == "regular_user" else ""

    body = _graphql(token, str(church.id))

    assert body["data"] is None
    assert body["errors"][0]["message"] == "Admin access required"
    assert not User.objects.filter(id=church.id).exists()
