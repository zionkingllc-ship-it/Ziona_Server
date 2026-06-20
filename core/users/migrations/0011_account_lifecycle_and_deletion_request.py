import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0010_user_token_invalid_before"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="lifecycle_state",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("deactivated", "Deactivated"),
                    ("pending_deletion", "Pending deletion"),
                    ("deleted", "Deleted"),
                ],
                db_index=True,
                default="active",
                help_text="User-controlled lifecycle state, separate from moderation status.",
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name="AccountDeletionRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("cancelled", "Cancelled"),
                            ("purging", "Purging"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("requested_at", models.DateTimeField()),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("retry_count", models.PositiveIntegerField(default=0)),
                ("failure_code", models.CharField(blank=True, default="", max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="account_deletion_request",
                        to="users.user",
                    ),
                ),
            ],
            options={"db_table": "account_deletion_requests"},
        ),
        migrations.AddIndex(
            model_name="accountdeletionrequest",
            index=models.Index(
                fields=["status", "scheduled_for"],
                name="idx_deletion_status_due",
            ),
        ),
    ]
