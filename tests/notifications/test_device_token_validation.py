"""Undeliverable device tokens must be rejected at registration.

Registration used to accept any string. Expo and raw-APNs tokens were stored,
FCM rejected them with INVALID_ARGUMENT, and that code deactivates the token —
so every later event silently short-circuited on "no active tokens" and push
died for that user with no error the client ever saw.
"""

import json

import pytest
from django.test import Client

from core.notifications.firebase import _CREDENTIAL_MISMATCH_CODES
from core.notifications.models import DeviceToken
from core.notifications.services import _classify_token, register_device_token

# A realistic FCM registration token: "<instance-id>:APA91b<...>", ~150 chars.
VALID_FCM_TOKEN = "d0vBYDr2KUxCvggoD9DPvO:APA91b" + ("x" * 120)
EXPO_TOKEN = "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
APNS_RAW_TOKEN = "a" * 64  # 64 hex chars — an Apple device token, not an FCM one


@pytest.fixture
def user(create_user):
    return create_user(email="tokens@example.com", username="tokenuser")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("token", "expected_kind"),
    [(EXPO_TOKEN, "expo"), (APNS_RAW_TOKEN, "apns_raw")],
)
def test_undeliverable_tokens_are_rejected_and_not_stored(user, token, expected_kind):
    assert _classify_token(token) == expected_kind

    with pytest.raises(ValueError, match="INVALID_DEVICE_TOKEN"):
        register_device_token(user.id, token, "ios")

    # Critical: nothing persisted, so it can never be deactivated later and
    # silently suppress push.
    assert not DeviceToken.objects.filter(token=token).exists()


@pytest.mark.django_db
def test_valid_fcm_token_still_registers(user):
    register_device_token(user.id, VALID_FCM_TOKEN, "android")

    stored = DeviceToken.objects.get(token=VALID_FCM_TOKEN)
    assert stored.user_id == user.id
    assert stored.platform == "android"
    assert stored.is_active is True


@pytest.mark.django_db
def test_rejecting_a_bad_token_does_not_disturb_existing_good_tokens(user):
    register_device_token(user.id, VALID_FCM_TOKEN, "android")

    with pytest.raises(ValueError):
        register_device_token(user.id, EXPO_TOKEN, "ios")

    assert DeviceToken.objects.filter(user=user, is_active=True).count() == 1
    assert DeviceToken.objects.get(user=user).token == VALID_FCM_TOKEN


@pytest.mark.django_db
def test_credential_mismatch_codes_are_not_in_the_deactivation_list():
    """A mismatch means the SERVER is misconfigured — the token is still good.

    Deactivating on mismatch would wipe every valid token in the environment.
    """
    from core.notifications import firebase

    source = firebase.send_fcm_notification.__doc__ or ""
    assert source is not None  # keeps the import meaningful

    deactivating_codes = {
        "NOT_FOUND",
        "INVALID_ARGUMENT",
        "messaging/invalid-registration-token",
        "messaging/registration-token-not-registered",
    }
    assert not (_CREDENTIAL_MISMATCH_CODES & deactivating_codes)


@pytest.mark.django_db
def test_register_device_token_mutation_returns_error_payload(authenticated_user):
    """The client gets a readable code, not an unstructured GraphQL error."""
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    def register(token):
        response = client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    mutation ($token: String!, $platform: String!) {
                      registerDeviceToken(token: $token, platform: $platform) {
                        success
                        error
                      }
                    }
                    """,
                    "variables": {"token": token, "platform": "ios"},
                }
            ),
            content_type="application/json",
        )
        body = json.loads(response.content)
        assert "errors" not in body, body.get("errors")
        return body["data"]["registerDeviceToken"]

    rejected = register(EXPO_TOKEN)
    assert rejected["success"] is False
    assert rejected["error"] == "INVALID_DEVICE_TOKEN"

    accepted = register(VALID_FCM_TOKEN)
    assert accepted["success"] is True
    assert accepted["error"] is None
