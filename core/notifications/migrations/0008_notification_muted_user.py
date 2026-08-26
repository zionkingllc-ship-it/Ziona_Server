import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0007_add_new_follower_notification_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationMutedUser",
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
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "muted_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="muted_by_notification_users",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_mutes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "notification_muted_users",
            },
        ),
        migrations.AddIndex(
            model_name="notificationmuteduser",
            index=models.Index(fields=["user", "muted_user"], name="idx_notif_mute_pair"),
        ),
        migrations.AddIndex(
            model_name="notificationmuteduser",
            index=models.Index(fields=["muted_user"], name="idx_notif_mute_muted_user"),
        ),
        migrations.AddConstraint(
            model_name="notificationmuteduser",
            constraint=models.UniqueConstraint(
                fields=("user", "muted_user"), name="uq_notification_muted_user_pair"
            ),
        ),
        migrations.AddConstraint(
            model_name="notificationmuteduser",
            constraint=models.CheckConstraint(
                check=~models.Q(("user", models.F("muted_user"))),
                name="ck_notification_mute_no_self",
            ),
        ),
    ]
