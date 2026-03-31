import logging
import uuid

from django.core.management.base import BaseCommand

from core.categories.models import Category
from core.media.models import MediaFile
from core.posts.models import Post, PostMedia, PostType
from core.users.models import User

logger = logging.getLogger(__name__)

# Fixed UUIDs so the script is fully idempotent — re-running never creates duplicates.
SEED_POST_IDS = {
    "carousel": uuid.UUID("aaaaaaaa-0001-4000-a000-000000000001"),
    "video": uuid.UUID("aaaaaaaa-0002-4000-a000-000000000002"),
    "bible": uuid.UUID("aaaaaaaa-0003-4000-a000-000000000003"),
    "text_bible": uuid.UUID("aaaaaaaa-0004-4000-a000-000000000004"),
}

SEED_MEDIA_IDS = {
    "nature_1": uuid.UUID("bbbbbbbb-0001-4000-b000-000000000001"),
    "nature_2": uuid.UUID("bbbbbbbb-0002-4000-b000-000000000002"),
    "nature_3": uuid.UUID("bbbbbbbb-0003-4000-b000-000000000003"),
    "video": uuid.UUID("bbbbbbbb-0004-4000-b000-000000000004"),
}


class Command(BaseCommand):
    help = "Creates seed posts for testing the feed (Image, Video, Text, Bible). Safe to run multiple times."

    def handle(self, *args, **options):
        # 1. Ensure user exists
        user = User.objects.first()
        if not user:
            user = User.objects.create_user(
                email="seed_user@example.com",
                username="seed_user",
                password="password123",
                first_name="Seed",
                last_name="User",
                is_active=True,
            )
            self.stdout.write(f"Created new user: {user.username}")

        # 2. Ensure category exists
        category = Category.objects.filter(slug="prayer").first()
        if not category:
            category = Category.objects.first()
        if not category:
            self.stdout.write(self.style.ERROR("No categories found. Run migrations first."))
            return

        created_count = 0

        # ---------- CAROUSEL POST (Multiple Images) ----------
        if not Post.objects.filter(id=SEED_POST_IDS["carousel"]).exists():
            carousel_post = Post.objects.create(
                id=SEED_POST_IDS["carousel"],
                user=user,
                post_type=PostType.IMAGE,
                caption="Explore these beautiful verses in nature! 🌿✨ #Carousel #Nature",
                category=category,
                media_count=3,
            )

            for i in range(1, 4):
                media_file, _ = MediaFile.objects.get_or_create(
                    id=SEED_MEDIA_IDS[f"nature_{i}"],
                    defaults={
                        "user": user,
                        "file_name": f"nature_{i}.png",
                        "file_type": "image/png",
                        "media_type": "image",
                        "file_size": 1024 * i,
                        "width": 1080,
                        "height": 1080,
                        "storage_path": f"categories/images/nature_{i}.png",
                        "status": "ready",
                    },
                )
                carousel_post.media_files.add(media_file)
                PostMedia.objects.get_or_create(
                    post=carousel_post,
                    order=i - 1,
                    defaults={
                        "media_url": f"https://picsum.photos/1080/1080?random={i}",
                        "media_type": "image",
                        "width": 1080,
                        "height": 1080,
                    },
                )
            created_count += 1
            self.stdout.write("  ✓ Created CAROUSEL post")
        else:
            self.stdout.write("  — CAROUSEL post already exists, skipping")

        # ---------- VIDEO POST ----------
        if not Post.objects.filter(id=SEED_POST_IDS["video"]).exists():
            video_post = Post.objects.create(
                id=SEED_POST_IDS["video"],
                user=user,
                post_type=PostType.VIDEO,
                caption="Short sermon excerpt on Faith and Persistence. 🔥",
                category=category,
                media_count=1,
            )
            media_file_video, _ = MediaFile.objects.get_or_create(
                id=SEED_MEDIA_IDS["video"],
                defaults={
                    "user": user,
                    "file_name": "ForBiggerBlazes.mp4",
                    "file_type": "video/mp4",
                    "media_type": "video",
                    "file_size": 50000,
                    "duration": 30.0,
                    "storage_path": "sample/ForBiggerBlazes.mp4",
                    "status": "ready",
                },
            )
            video_post.media_files.add(media_file_video)
            PostMedia.objects.get_or_create(
                post=video_post,
                order=0,
                defaults={
                    "media_url": "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
                    "media_type": "video",
                    "duration": 30,
                },
            )
            created_count += 1
            self.stdout.write("  ✓ Created VIDEO post")
        else:
            self.stdout.write("  — VIDEO post already exists, skipping")

        # ---------- BIBLE-ONLY POST ----------
        if not Post.objects.filter(id=SEED_POST_IDS["bible"]).exists():
            Post.objects.create(
                id=SEED_POST_IDS["bible"],
                user=user,
                post_type=PostType.TEXT,
                caption=None,
                category=category,
                scripture_book="Psalm",
                scripture_chapter=23,
                scripture_verse_start=1,
                scripture_verse_end=6,
                scripture_translation="KJV",
                media_count=0,
            )
            created_count += 1
            self.stdout.write("  ✓ Created BIBLE post")
        else:
            self.stdout.write("  — BIBLE post already exists, skipping")

        # ---------- TEXT + BIBLE POST ----------
        if not Post.objects.filter(id=SEED_POST_IDS["text_bible"]).exists():
            Post.objects.create(
                id=SEED_POST_IDS["text_bible"],
                user=user,
                post_type=PostType.TEXT,
                caption="A message of hope for your week. God is with us.",
                category=category,
                scripture_book="Joshua",
                scripture_chapter=1,
                scripture_verse_start=9,
                scripture_verse_end=9,
                scripture_translation="KJV",
                media_count=0,
            )
            created_count += 1
            self.stdout.write("  ✓ Created TEXT+BIBLE post")
        else:
            self.stdout.write("  — TEXT+BIBLE post already exists, skipping")

        if created_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f"Successfully seeded {created_count} new post(s)!")
            )
        else:
            self.stdout.write(self.style.SUCCESS("All seed posts already exist. Nothing to do."))
