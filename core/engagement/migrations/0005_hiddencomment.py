import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("engagement", "0004_bookmarkfolder_thumbnail_url"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HiddenComment",
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
                    "comment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hidden_by_users",
                        to="engagement.comment",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hidden_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "hidden_comments",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="hiddencomment",
            constraint=models.UniqueConstraint(
                fields=("user", "comment"),
                name="uq_hidden_user_comment",
            ),
        ),
        migrations.AddIndex(
            model_name="hiddencomment",
            index=models.Index(
                fields=["user", "created_at"],
                name="idx_hidden_comment_user_time",
            ),
        ),
    ]
