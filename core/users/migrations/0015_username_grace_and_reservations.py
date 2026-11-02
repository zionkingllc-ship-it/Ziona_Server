"""Introduce bounded username grace edits and case-insensitive ownership."""

from datetime import timedelta

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def prepare_existing_users(apps, schema_editor):
    User = apps.get_model("users", "User")
    duplicates = (
        User._base_manager.exclude(username__isnull=True)
        .annotate(normalized=Lower("username"))
        .values("normalized")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            "Case-insensitive duplicate usernames exist. Resolve them before applying "
            "users.0015; this migration will not rename accounts automatically."
        )

    # Historical edits in the first 48 hours should not start a 30-day lock.
    for user in (
        User._base_manager.exclude(last_username_change__isnull=True)
        .only("id", "created_at", "last_username_change")
        .iterator(chunk_size=500)
    ):
        if user.created_at <= user.last_username_change < user.created_at + timedelta(hours=48):
            User._base_manager.filter(pk=user.pk).update(
                grace_username_changes=1, last_username_change=None
            )


class Migration(migrations.Migration):
    dependencies = [("users", "0014_trim_full_names")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="grace_username_changes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="UsernameReservation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("username", models.CharField(max_length=30, unique=True)),
                ("owner_id", models.UUIDField(db_index=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
            options={"db_table": "username_reservations"},
        ),
        migrations.RunPython(prepare_existing_users, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(Lower("username"), name="unique_user_username_ci"),
        ),
    ]
