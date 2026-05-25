"""
Authentication URL configuration.

All auth endpoints are prefixed with /api/auth/
"""

from django.urls import path

from core.authentication.otp_views import (
    UnifiedSendOTPView,
    UnifiedVerifyOTPView,
)
from core.authentication.views import (
    CheckEmailView,
    DeactivateAccountView,
    DeleteAccountView,
    FinalizeUsernameView,
    GoogleOAuthView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    ResendOTPView,
    SuggestUsernamesView,
    TokenRefreshView,
    VerifyEmailView,
)

app_name = "authentication"

urlpatterns = [
    path("otp/send", UnifiedSendOTPView.as_view(), name="otp-send"),
    path("otp/verify", UnifiedVerifyOTPView.as_view(), name="otp-verify"),
    path("register", RegisterView.as_view(), name="register"),
    path("login", LoginView.as_view(), name="login"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("verify-email", VerifyEmailView.as_view(), name="verify-email"),
    path("resend-otp", ResendOTPView.as_view(), name="resend-otp"),
    path("suggest-usernames", SuggestUsernamesView.as_view(), name="suggest-usernames"),
    path("password-reset", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "password-reset/confirm",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("google", GoogleOAuthView.as_view(), name="google-oauth"),
    path("me", MeView.as_view(), name="me"),
    path("deactivate", DeactivateAccountView.as_view(), name="deactivate-account"),
    path("delete-account", DeleteAccountView.as_view(), name="delete-account"),
    path("check-email", CheckEmailView.as_view(), name="check-email"),
    path("finalize-username", FinalizeUsernameView.as_view(), name="finalize_username"),
]
