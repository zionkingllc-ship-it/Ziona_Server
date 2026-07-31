"""Adopt the implicit CirclePost.media_files M2M table as an explicit through
model and add a `position` column so multi-image circle posts preserve order.

Mirrors posts/0005: `circle_posts_media_files` already exists (created in 0012 as
the auto M2M join table) with production rows, so we adopt it via
SeparateDatabaseAndState (no recreate), add `position`, then backfill positions
per post in the current display order (MediaFile.created_at).
"""

import datetime

import django.db.models.deletion
from django.db import migrations, models

_EPOCH = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def backfill_circle_post_media_positions(apps, schema_editor):
    CirclePostMediaThrough = apps.get_model("circles", "CirclePostMediaThrough")
    MediaFile = apps.get_model("media", "MediaFile")

    created_at_by_media = dict(MediaFile.objects.values_list("id", "created_at"))

    rows_by_post: dict = {}
    for row in CirclePostMediaThrough.objects.values("id", "circlepost_id", "mediafile_id"):
        rows_by_post.setdefault(row["circlepost_id"], []).append(row)

    for rows in rows_by_post.values():
        rows.sort(
            key=lambda r: (created_at_by_media.get(r["mediafile_id"]) or _EPOCH, str(r["id"]))
        )
        for position, row in enumerate(rows):
            CirclePostMediaThrough.objects.filter(id=row["id"]).update(position=position)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0002_mediafile_processing_error_fields"),
        ("circles", "0014_alter_circlereport_target_type_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="CirclePostMediaThrough",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "circlepost",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="media_through",
                                to="circles.circlepost",
                            ),
                        ),
                        (
                            "mediafile",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="circle_post_media_through",
                                to="media.mediafile",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "circle_posts_media_files",
                        "unique_together": {("circlepost", "mediafile")},
                    },
                ),
                migrations.AlterField(
                    model_name="circlepost",
                    name="media_files",
                    field=models.ManyToManyField(
                        blank=True,
                        related_name="circle_posts",
                        through="circles.CirclePostMediaThrough",
                        to="media.mediafile",
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name="circlepostmediathrough",
            name="position",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(backfill_circle_post_media_positions, migrations.RunPython.noop),
    ]
