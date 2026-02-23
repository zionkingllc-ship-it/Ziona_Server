from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """Custom manager for User model with email as identifier.

    Filters out soft-deleted users by default.
    """

    def get_queryset(self):
        """Return only non-deleted users."""
        return super().get_queryset().filter(deleted_at__isnull=True)

    def create_user(
        self,
        email: str,
        username: str | None = None,
        password: str | None = None,
        **extra_fields,
    ):
        """Create and save a regular user.

        Args:
            email: User's email address.
            username: Display name (optional, set later in onboarding).
            password: Plain text password (will be hashed).
            **extra_fields: Additional user fields.

        Returns:
            Created User instance.

        Raises:
            ValueError: If email is not provided.
        """
        if not email:
            raise ValueError("Email is required")

        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields,
    ):
        """Create and save a superuser.

        Args:
            email: Superuser's email.
            password: Plain text password (optional for Django's createsuperuser).
            **extra_fields: Additional fields (can include username).

        Returns:
            Created superuser User instance.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")
        extra_fields.setdefault("is_email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        username = extra_fields.pop("username", None)
        if not username:
            base = email.split("@")[0].replace(".", "_").replace("+", "_")
            username = base

            counter = 1
            while self.model.all_objects.filter(username=username).exists():
                username = f"{base}_{counter}"
                counter += 1

        return self.create_user(email, username, password, **extra_fields)
