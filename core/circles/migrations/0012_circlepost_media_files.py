import mimetypes
import os
from urllib.parse import urlparse

from django.db import migrations, models

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}


def _infer_media_type(url: str) -> str:
    extension = os.path.splitext(urlparse(url).path)[1].lower()
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "image"


def _infer_file_name(url: str) -> str:
    file_name = os.path.basename(urlparse(url).path)
    return file_name or "legacy-media"


def _infer_file_type(url: str, media_type: str) -> str:
    guessed_type, _ = mimetypes.guess_type(url)
    if guessed_type:
        return guessed_type
    return "video/mp4" if media_type == "video" else "image/jpeg"


def backfill_circle_post_media_files(apps, schema_editor):
    CirclePost = apps.get_model("circles", "CirclePost")
    MediaFile = apps.get_model("media", "MediaFile")

    for post in CirclePost.objects.exclude(image_url="", media_url="").iterator():
        legacy_urls = []
        for url in (post.image_url, post.media_url):
            normalized_url = (url or "").strip()
            if normalized_url and normalized_url not in legacy_urls:
                legacy_urls.append(normalized_url)

        for url in legacy_urls:
            if post.media_files.filter(storage_path=url).exists():
                continue

            media_type = _infer_media_type(url)
            media_file = MediaFile.objects.create(
                user_id=post.user_id,
                file_name=_infer_file_name(url),
                file_type=_infer_file_type(url, media_type),
                file_size=0,
                media_type=media_type,
                storage_path=url,
                status="ready",
            )
            post.media_files.add(media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("circles", "0011_backfill_anchor_typed_media"),
    ]

    operations = [
        migrations.AddField(
            model_name="circlepost",
            name="media_files",
            field=models.ManyToManyField(
                blank=True, related_name="circle_posts", to="media.mediafile"
            ),
        ),
        migrations.RunPython(backfill_circle_post_media_files, migrations.RunPython.noop),
    ]
