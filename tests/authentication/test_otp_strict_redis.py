import pytest

from core.authentication.otp_service import OTPService
from core.authentication.validators import AuthenticationError


def _redis_down(_alias):
    raise RuntimeError("redis down")


def test_unified_send_otp_fails_closed_when_redis_is_required(create_user, settings, monkeypatch):
    user = create_user(email="otp-send@example.com", is_email_verified=False)
    settings.AUTH_STRICT_REDIS = True
    monkeypatch.setattr("django_redis.get_redis_connection", _redis_down)

    with pytest.raises(AuthenticationError) as exc_info:
        OTPService.unified_send_otp(user.email, "email_verification")

    assert exc_info.value.code == "OTP_SERVICE_UNAVAILABLE"


def test_resend_verification_otp_fails_closed_when_redis_is_required(
    create_user, settings, monkeypatch
):
    user = create_user(email="otp-resend@example.com", is_email_verified=False)
    settings.AUTH_STRICT_REDIS = True
    monkeypatch.setattr("django_redis.get_redis_connection", _redis_down)

    with pytest.raises(AuthenticationError) as exc_info:
        OTPService.resend_verification_otp(user.email)

    assert exc_info.value.code == "OTP_SERVICE_UNAVAILABLE"


def test_unified_verify_otp_fails_closed_when_redis_is_required(create_user, settings, monkeypatch):
    user = create_user(email="otp-verify@example.com", is_email_verified=True)
    settings.AUTH_STRICT_REDIS = True
    monkeypatch.setattr("django_redis.get_redis_connection", _redis_down)

    with pytest.raises(AuthenticationError) as exc_info:
        OTPService.unified_verify_otp(
            email=user.email,
            code="123456",
            purpose="account_deletion",
        )

    assert exc_info.value.code == "OTP_SERVICE_UNAVAILABLE"
