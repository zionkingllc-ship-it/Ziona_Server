"""Authentication REST views package.

Re-exports every view class of the former core/authentication/views.py module
so urls.py, otp_views.py, and test patch targets keep working unchanged.
"""

from core.authentication.views.base import BaseAuthView
from core.authentication.views.lifecycle import (
    CancelAccountDeletionView,
    DeactivateAccountView,
    DeleteAccountView,
    FinalizeUsernameView,
    MeView,
    ReactivateAccountView,
)
from core.authentication.views.oauth import AppleNonceView, AppleOAuthView, GoogleOAuthView
from core.authentication.views.password import (
    ChangePasswordView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    ResendOTPView,
    SuggestUsernamesView,
    VerifyEmailView,
)
from core.authentication.views.session import (
    CheckEmailView,
    LoginView,
    LogoutView,
    RegisterView,
    TokenRefreshView,
)

__all__ = [
    "AppleNonceView",
    "AppleOAuthView",
    "BaseAuthView",
    "CancelAccountDeletionView",
    "ChangePasswordView",
    "CheckEmailView",
    "DeactivateAccountView",
    "DeleteAccountView",
    "FinalizeUsernameView",
    "GoogleOAuthView",
    "LoginView",
    "LogoutView",
    "MeView",
    "PasswordResetConfirmView",
    "PasswordResetRequestView",
    "ReactivateAccountView",
    "RegisterView",
    "ResendOTPView",
    "SuggestUsernamesView",
    "TokenRefreshView",
    "VerifyEmailView",
]
