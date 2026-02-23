"""
Authentication URL configuration.

All auth endpoints are prefixed with /api/auth/
"""

from django.urls import path

from core.authentication.views import (
    GoogleOAuthView,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    TokenRefreshView,
    VerifyEmailView,
)

app_name = "authentication"

urlpatterns = [
    path("register", RegisterView.as_view(), name="register"),
    path("login", LoginView.as_view(), name="login"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("verify-email", VerifyEmailView.as_view(), name="verify-email"),
    path("password-reset", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "password-reset/confirm",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("google", GoogleOAuthView.as_view(), name="google-oauth"),
]
