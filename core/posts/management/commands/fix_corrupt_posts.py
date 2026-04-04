import logging

from django.core.management.base import BaseCommand

from core.posts.models import Post, PostType

logger = logging.getLogger("core.posts")


class Command(BaseCommand):
    """
    Management command to fix data corruption in the posts table.

    Identifies posts marked as 'image' or 'video' that have no associated
    media records in the 'post_media' table. Remediates them by converting
    their type to 'text'.
    """

    help = "Find and fix posts marked as media but lacking media file records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Verify corruption without performing any database writes.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run")

        # 1. Identify Corrupt Posts
        # Strategy: post_type is image/video, but related 'post_media' set is empty.
        # Note: We filter for rows where the reverse relation 'post_media' is null.
        corrupt_posts = Post.objects.filter(
            post_type__in=[PostType.IMAGE, PostType.VIDEO], post_media__isnull=True
        ).distinct()

        count = corrupt_posts.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No corrupt posts found. Database is healthy!"))
            return

        self.stdout.write(self.style.WARNING(f"Found {count} corrupt posts."))

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: No changes will be applied."))
            for post in corrupt_posts[:50]:  # Limit output for display
                self.stdout.write(
                    f"  - [CORRUPT] ID: {post.id} | Type: {post.post_type} | Author: {post.user_id}"
                )
            if count > 50:
                self.stdout.write(f"  ... and {count - 50} more.")
            return

        # 2. Remediate
        # Strategy: Convert to TEXT posts so they display safely as captions
        # without crashing the GraphQL media resolution logic.
        updated_count = corrupt_posts.update(post_type=PostType.TEXT)

        self.stdout.write(
            self.style.SUCCESS(f"Successfully remediated {updated_count} posts to 'TEXT' type.")
        )
        logger.info("corrupt_posts_remediated", extra={"count": updated_count})
