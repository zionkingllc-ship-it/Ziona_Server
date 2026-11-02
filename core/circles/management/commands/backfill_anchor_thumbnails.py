"""Backfill missing anchor thumbnails from processed media or generated copies."""

from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from core.circles.models import Anchor
from core.media.tasks import generate_anchor_thumbnail
from core.media.thumbnails import (
    anchor_thumbnail_candidates,
    can_generate_anchor_thumbnail,
    enqueue_anchor_thumbnail,
    resolve_media_thumbnail_url,
)

MEDIA_ANCHOR_TYPES = ("image", "image_text", "video")


class Command(BaseCommand):
    help = "Resolve or generate thumbnails for media anchors that do not have one."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--sync", action="store_true")

    def handle(self, *args, **options):
        limit = max(options["limit"], 0)
        dry_run = options["dry_run"]
        sync = options["sync"]

        anchors = (
            Anchor.objects.filter(anchor_type__in=MEDIA_ANCHOR_TYPES)
            .filter(
                Q(anchor_thumbnail="")
                | Q(anchor_thumbnail=F("media_url"))
                | Q(anchor_thumbnail=F("anchor_image"))
                | Q(anchor_thumbnail=F("anchor_video"))
            )
            .exclude(media_url="", anchor_image="", anchor_video="")
            .order_by("created_at")[:limit]
        )

        resolved = 0
        queued = 0
        skipped = 0
        for anchor in anchors.iterator():
            candidates = anchor_thumbnail_candidates(
                anchor.anchor_type,
                media_url=anchor.media_url,
                anchor_image=anchor.anchor_image,
                anchor_video=anchor.anchor_video,
            )
            thumbnail_url = resolve_media_thumbnail_url(*candidates)
            if thumbnail_url:
                resolved += 1
                if not dry_run:
                    Anchor.objects.filter(
                        id=anchor.id,
                        anchor_thumbnail=anchor.anchor_thumbnail,
                    ).update(
                        anchor_thumbnail=thumbnail_url,
                        updated_at=timezone.now(),
                    )
                continue

            if not can_generate_anchor_thumbnail(
                anchor.anchor_type,
                media_url=anchor.media_url,
                anchor_image=anchor.anchor_image,
            ):
                skipped += 1
                continue

            queued += 1
            if not dry_run:
                if sync:
                    generate_anchor_thumbnail(str(anchor.id))
                else:
                    enqueue_anchor_thumbnail(str(anchor.id))

        suffix = " (dry run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"anchor thumbnails: resolved={resolved} queued={queued} skipped={skipped}{suffix}"
            )
        )
