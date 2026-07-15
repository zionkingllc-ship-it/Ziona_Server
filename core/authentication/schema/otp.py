"""Auth GraphQL mutations — otp.

Split from the former core/authentication/schema.py (no contract change).
"""

import strawberry

from core.authentication.schema.types import (  # noqa: F401
    AddPasswordPayload,
    AuthPayload,
    ChangePasswordPayload,
    GoogleOAuthPayload,
    OTPPayload,
    PasswordResetRequestPayload,
    RegisterPayload,
    VerifyOTPPayload,
    _token_metadata_kwargs,
)
from core.shared.request_utils import get_client_ip
from core.users.schema import AuthenticatedUserType


@strawberry.type
class OTPMutations:
    @strawberry.mutation(
        description=(
            "Send a purpose-scoped OTP via the unified OTP service. "
            "Use this for generic OTP flows; use register/login/verifyEmail/resendVerificationOtp "
            "for the password signup verification path."
        )
    )
    def send_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        purpose: str,
    ) -> OTPPayload:
        """
        Send a one-time password (OTP) code via email.

        This is a unified router for purpose-scoped OTP flows such as password reset and
        account actions. Password signup verification has a dedicated GraphQL flow:
        ``register`` -> ``verifyEmail`` or ``resendVerificationOtp``.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's active email
        - purpose (String, required) - Which flow requested the OTP ("registration", "email_verification", "password_reset")
        **Returns:** OTPPayload with execution expiry time blocks
        **Errors:** INVALID_EMAIL, TOO_MANY_REQUESTS, INVALID_PURPOSE
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.unified_send_otp(
                email=email,
                purpose=purpose,
                ip_address=get_client_ip(request),
            )
            return OTPPayload(
                success=True,
                message=result["message"],
                expires_in=result["expires_in"],
                purpose=result["purpose"],
                resend_after=result["resend_after"],
            )
        except AuthenticationError as e:
            return OTPPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Verify a purpose-scoped OTP through the unified OTP service. "
            "For OTPs issued by password register/login, use verifyEmail instead."
        )
    )
    def verify_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        code: str,
        purpose: str,
    ) -> VerifyOTPPayload:
        """
        Verify an active purpose-scoped OTP code and execute the desired sequence block.

        This mutation is backed by ``OTPService.unified_verify_otp``. It is appropriate
        for unified OTP purposes such as ``password_reset`` and explicit
        ``email_verification`` sends through ``sendOtp``. For OTPs created by the
        password ``register`` / unverified ``login`` flow, use ``verifyEmail`` instead
        because those codes are namespaced under the legacy ``verify`` purpose.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's targeted email endpoint
        - code (String, required) - The 6-digit numerical entry value
        - purpose (String, required) - Must match the string sent exactly ("registration", "email_verification", "password_reset")
        **Returns:** VerifyOTPPayload linking standard properties dynamically
        **Errors:** INVALID_OTP, OTP_EXPIRED, USER_NOT_FOUND, MAX_ATTEMPTS_EXCEEDED
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.unified_verify_otp(
                email=email,
                code=code,
                purpose=purpose,
                ip_address=get_client_ip(request),
            )

            payload = VerifyOTPPayload(
                success=True,
                message=result.get("message"),
                reset_token=result.get("reset_token"),
            )

            if "user" in result:
                payload.user = AuthenticatedUserType.from_model(result["user"])
            if "access_token" in result:
                payload.access_token = result["access_token"]
            if "refresh_token" in result:
                payload.refresh_token = result["refresh_token"]
            for field_name, value in _token_metadata_kwargs(result).items():
                setattr(payload, field_name, value)

            return payload
        except AuthenticationError as e:
            return VerifyOTPPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )
