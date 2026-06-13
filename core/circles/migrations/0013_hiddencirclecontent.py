import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("circles", "0012_circlepost_media_files"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HiddenCircleContent",
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
                    "target_type",
                    models.CharField(
                        choices=[
                            ("anchor", "Anchor"),
                            ("response", "Anchor Response"),
                            ("circle", "Circle"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("target_id", models.UUIDField(db_index=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hidden_circle_content",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "hidden_circle_content",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="hiddencirclecontent",
            constraint=models.UniqueConstraint(
                fields=("user", "target_type", "target_id"),
                name="uq_hidden_circle_content_user_target",
            ),
        ),
        migrations.AddIndex(
            model_name="hiddencirclecontent",
            index=models.Index(
                fields=["user", "created_at"],
                name="idx_hidden_circle_user_time",
            ),
        ),
        migrations.AddIndex(
            model_name="hiddencirclecontent",
            index=models.Index(
                fields=["user", "target_type", "target_id"],
                name="idx_hidden_circle_lookup",
            ),
        ),
    ]
