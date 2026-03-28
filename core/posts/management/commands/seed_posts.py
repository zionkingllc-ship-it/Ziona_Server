import logging
import uuid

from django.core.management.base import BaseCommand

from core.categories.models import Category
from core.media.models import MediaFile
from core.posts.models import Post, PostMedia, PostType
from core.users.models import User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Creates seed posts for testing the feed (Image, Video, Text, Bible)."

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
            category = Category.objects.create(id=uuid.uuid4(), name="Prayer", slug="prayer")
            self.stdout.write(f"Created category: {category.name}")
        else:
            category = Category.objects.first()  # fallback if slug prayer not there but another is

        # Create IMAGE Post
        image_post = Post.objects.create(
            user=user,
            post_type=PostType.IMAGE,
            caption="Looking forward to a blessed week! ",
            category=category,
            media_count=1,
        )
        # Create MediaFile and PostMedia
        media_file_1 = MediaFile.objects.create(
            user=user,
            file_name="all.png",
            file_type="image/png",
            media_type="image",
            file_size=1024,
            width=800,
            height=600,
            storage_path="categories/images/all.png",
            status="ready",
        )
        image_post.media_files.add(media_file_1)
        PostMedia.objects.create(
            post=image_post,
            media_url="https://storage.googleapis.com/ziona-media-dev/categories/images/all.png",
            media_type="image",
            order=0,
            width=800,
            height=600,
        )

        # Create VIDEO Post
        video_post = Post.objects.create(
            user=user,
            post_type=PostType.VIDEO,
            caption="Short sermon excerpt on Faith.",
            category=category,
            media_count=1,
        )
        media_file_2 = MediaFile.objects.create(
            user=user,
            file_name="ForBiggerBlazes.mp4",
            file_type="video/mp4",
            media_type="video",
            file_size=50000,
            duration=30.0,
            storage_path="sample/ForBiggerBlazes.mp4",
            status="ready",
        )
        video_post.media_files.add(media_file_2)
        PostMedia.objects.create(
            post=video_post,
            media_url="http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
            media_type="video",
            order=0,
            duration=30,
        )

        # Create TEXT Post
        Post.objects.create(
            user=user,
            post_type=PostType.TEXT,
            caption="Faith is taking the first step even when you don't see the whole staircase.",
            category=category,
            media_count=0,
        )

        # Create BIBLE Post
        Post.objects.create(
            user=user,
            post_type=PostType.TEXT,
            caption="Daily Scripture Reading",
            category=category,
            scripture_book="Genesis",
            scripture_chapter=1,
            scripture_verse_start=1,
            scripture_verse_end=3,
            scripture_translation="KJV",
            media_count=0,
        )

        self.stdout.write(
            self.style.SUCCESS("Successfully seeded 4 posts: IMAGE, VIDEO, TEXT, and BIBLE!")
        )
