import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.shared.models import AllObjectsManager
from core.users.managers import UserManager


class UserRole(models.TextChoices):
    """User roles for role-based access control."""

    USER = "user", "User"
    ADMIN = "admin", "Admin"


class UserStatus(models.TextChoices):
    """Admin-managed moderation status for user accounts."""

    ACTIVE = "active", "Active"
    WARNED = "warned", "Warned"
    SUSPENDED = "suspended", "Suspended"


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with email-based authentication.

    Uses UUID primary key, supports soft-deletion, and stores
    sensitive fields (DOB) encrypted.

    Attributes:
        id: UUID primary key.
        email: Unique email address (primary login identifier).
        username: Unique display name (3-30 chars).
        full_name: User's full name.
        bio: Short biography (max 500 chars).
        avatar_url: URL to avatar image (signed URL).
        role: User role (user or admin).
        is_email_verified: Whether email has been verified.
        encrypted_dob: Fernet-encrypted date of birth.
        location: Manually entered location string.
        is_active: Whether account is active.
        is_staff: Whether user can access admin.
        created_at: Account creation timestamp.
        updated_at: Last modification timestamp.
        deleted_at: Soft-delete timestamp.
        last_login_ip: IP of most recent login.
        auth_provider: How user registered (email or google).
        firebase_uid: Firebase UID for Google OAuth users.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    email = models.EmailField(
        unique=True,
        max_length=255,
        db_index=True,
    )
    username = models.CharField(
        unique=True,
        max_length=30,
        db_index=True,
        null=True,
        blank=True,
    )
    full_name = models.CharField(max_length=150, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)

    role = models.CharField(
        max_length=10,
        choices=UserRole.choices,
        default=UserRole.USER,
        db_index=True,
    )

    is_email_verified = models.BooleanField(default=False)
    needs_username_selection = models.BooleanField(default=False)
    hide_like_count = models.BooleanField(default=False)

    encrypted_dob = models.BinaryField(null=True, blank=True)

    location = models.CharField(max_length=100, blank=True)

    status = models.CharField(
        max_length=20,
        choices=UserStatus.choices,
        default=UserStatus.ACTIVE,
        db_index=True,
        help_text="Admin-managed moderation status. Separate from Django is_active.",
    )
    warned_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_name_change = models.DateTimeField(null=True, blank=True)
    last_username_change = models.DateTimeField(null=True, blank=True)

    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    auth_provider = models.CharField(
        max_length=20,
        choices=[("email", "Email"), ("google", "Google")],
        default="email",
    )
    firebase_uid = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    social_auth_provider = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        choices=[
            (None, "Email/Password"),
            ("google", "Google"),
            ("facebook", "Facebook"),
            ("apple", "Apple"),
        ],
        help_text="How the user originally registered. Null = email/password",
    )
    google_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    objects = UserManager()
    all_objects = AllObjectsManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"], name="idx_user_email"),
            models.Index(fields=["username"], name="idx_user_username"),
            models.Index(fields=["firebase_uid"], name="idx_user_firebase_uid"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.username} ({self.email})"

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == UserRole.ADMIN

    def soft_delete(self) -> None:
        """Soft delete the user account."""
        self.deleted_at = timezone.now()
        self.is_active = False
        self.save(update_fields=["deleted_at", "is_active", "updated_at"])

    def restore(self) -> None:
        """Restore a soft-deleted user account."""
        self.deleted_at = None
        self.is_active = True
        self.save(update_fields=["deleted_at", "is_active", "updated_at"])


class InterestCategory(models.TextChoices):
    """Faith-based interest categories for onboarding."""

    LOVE = "love", "Love"
    TRUST = "trust", "Trust"
    WORSHIP = "worship", "Worship"
    PATIENCE = "patience", "Patience"
    PRAYER = "prayer", "Prayer"


class UserInterest(models.Model):
    """A user's selected faith interest for feed personalization.

    Created during onboarding when users choose their interests.

    Attributes:
        id: UUID primary key.
        user: The user who selected this interest.
        interest: The selected interest category.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="interests",
    )
    interest = models.CharField(
        max_length=50,
        choices=InterestCategory.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_interests"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "interest"],
                name="uq_user_interest",
            ),
        ]
        indexes = [
            models.Index(fields=["user"], name="idx_userinterest_user"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.user_id} → {self.interest}"
