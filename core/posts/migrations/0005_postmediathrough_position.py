"""Adopt the implicit Post.media_files M2M table as an explicit through model
and add a `position` column so multi-image posts preserve the selected order.

The table `posts_media_files` already exists (created in 0002 as the auto M2M
join table) and holds production rows, so we must NOT recreate it:
- SeparateDatabaseAndState adopts the existing table as `PostMediaThrough`
  (state only, no DDL) and repoints the M2M to `through=`.
- AddField physically adds the `position` column (default 0).
- RunPython backfills positions per post in the current display order
  (MediaFile.created_at), so existing posts do not visually reshuffle.
"""

import datetime

import django.db.models.deletion
from django.db import migrations, models

_EPOCH = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def backfill_post_media_positions(apps, schema_editor):
    PostMediaThrough = apps.get_model("posts", "PostMediaThrough")
    MediaFile = apps.get_model("media", "MediaFile")

    created_at_by_media = dict(MediaFile.objects.values_list("id", "created_at"))

    rows_by_post: dict = {}
    for row in PostMediaThrough.objects.values("id", "post_id", "mediafile_id"):
        rows_by_post.setdefault(row["post_id"], []).append(row)

    for rows in rows_by_post.values():
        rows.sort(
            key=lambda r: (created_at_by_media.get(r["mediafile_id"]) or _EPOCH, str(r["id"]))
        )
        for position, row in enumerate(rows):
            PostMediaThrough.objects.filter(id=row["id"]).update(position=position)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0002_mediafile_processing_error_fields"),
        ("posts", "0004_rename_scripture_version_to_translation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="PostMediaThrough",
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
                            "post",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="media_through",
                                to="posts.post",
                            ),
                        ),
                        (
                            "mediafile",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="post_media_through",
                                to="media.mediafile",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "posts_media_files",
                        "unique_together": {("post", "mediafile")},
                    },
                ),
                migrations.AlterField(
                    model_name="post",
                    name="media_files",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text="Attached media files via uploadMedia",
                        related_name="posts",
                        through="posts.PostMediaThrough",
                        to="media.mediafile",
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name="postmediathrough",
            name="position",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(backfill_post_media_positions, migrations.RunPython.noop),
    ]
