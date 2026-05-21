from django.db import migrations
from django.db.models import F


def backfill_anchor_typed_media(apps, schema_editor):
    Anchor = apps.get_model("circles", "Anchor")

    Anchor.objects.filter(
        anchor_type__in=["image", "image_text"],
        media_url__gt="",
        anchor_image="",
    ).update(anchor_image=F("media_url"))

    Anchor.objects.filter(
        anchor_type="video",
        media_url__gt="",
        anchor_video="",
    ).update(anchor_video=F("media_url"))


class Migration(migrations.Migration):
    dependencies = [
        ("circles", "0010_mobile_dev_features"),
    ]

    operations = [
        migrations.RunPython(backfill_anchor_typed_media, migrations.RunPython.noop),
    ]
