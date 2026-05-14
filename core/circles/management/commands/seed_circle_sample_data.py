import os
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.circles.models import Anchor, AnchorPage, Circle, CircleMembership, CirclePost, CircleRule
from core.users.models import User

SYSTEM_EMAIL = "circles-sample-admin@ziona.app"


CIRCLES = [
    {
        "key": "faith-work-purpose",
        "name": "Faith, Work & Purpose",
        "description": "A community where Christians discuss career, business, finding purpose in work while honoring God.",
        "image": "https://images.unsplash.com/photo-1509099836639-18ba1795216d",
        "profile_image": "https://images.unsplash.com/photo-1519491050282-cf00c82424b4",
        "members": 1247,
    },
    {
        "key": "prayer-intercession",
        "name": "Prayer & Intercession",
        "description": "Believers come together to pray for one another and share prayer requests.",
        "image": "https://images.unsplash.com/photo-1519491050282-cf00c82424b4",
        "profile_image": "https://i.pravatar.cc/150?img=9",
        "members": 892,
    },
    {
        "key": "youth-fellowship",
        "name": "Youth Fellowship",
        "description": "Young believers gathering to grow together in faith and build lasting friendships.",
        "image": "https://images.unsplash.com/photo-1529156069898-49953e39b3ac",
        "profile_image": "https://i.pravatar.cc/150?img=15",
        "members": 456,
    },
    {
        "key": "bible-study-group",
        "name": "Bible Study Group",
        "description": "Daily Bible reading and discussion for spiritual growth and deeper understanding of Scripture.",
        "image": "https://images.unsplash.com/photo-1546519638-68e109498ffc",
        "profile_image": "https://i.pravatar.cc/150?img=19",
        "members": 2341,
    },
    {
        "key": "worship-praise",
        "name": "Worship & Praise",
        "description": "Singing worship songs and sharing fellowship through music.",
        "image": "https://images.unsplash.com/photo-1476681248696-466c012a3f1f",
        "profile_image": "https://i.pravatar.cc/150?img=30",
        "members": 678,
    },
    {
        "key": "marriage-family",
        "name": "Marriage & Family",
        "description": "Building strong families through faith and fellowship.",
        "image": "https://images.unsplash.com/photo-1511632765486-a01980e01a18",
        "profile_image": "https://i.pravatar.cc/150?img=35",
        "members": 523,
    },
    {
        "key": "evangelism-missions",
        "name": "Evangelism & Missions",
        "description": "Sharing the gospel and supporting mission work around the world.",
        "image": "https://images.unsplash.com/photo-1438232992991-995b7058bbb3",
        "profile_image": "https://i.pravatar.cc/150?img=40",
        "members": 342,
    },
]


USERS = [
    ("Sarah Kim", "sarah.kim", "https://i.pravatar.cc/100?img=1"),
    ("Mike Ross", "mike.ross", "https://i.pravatar.cc/100?img=2"),
    ("Grace Lee", "grace.lee", "https://i.pravatar.cc/100?img=3"),
    ("James Chen", "james.chen", "https://i.pravatar.cc/100?img=4"),
    ("Rebecca Stone", "rebecca.stone", "https://i.pravatar.cc/100?img=9"),
    ("David Wilson", "david.wilson", "https://i.pravatar.cc/100?img=10"),
    ("Emma Davis", "emma.davis", "https://i.pravatar.cc/100?img=12"),
    ("Pastor Michael", "pastor.michael", "https://i.pravatar.cc/100?img=14"),
    ("Sarah Worship", "sarah.worship", "https://i.pravatar.cc/100?img=25"),
    ("John Family", "john.family", "https://i.pravatar.cc/100?img=30"),
    ("Mission Mark", "mission.mark", "https://i.pravatar.cc/100?img=35"),
]


CIRCLE_RULES = {
    "faith-work-purpose": [
        (
            1,
            "Honor God in Your Work",
            "Let your work reflect your faith. Use your professional life as a platform to glorify God and serve others with excellence.",
        ),
        (
            2,
            "Be Supportive",
            "Encourage fellow members facing career challenges. Share wisdom, pray for one another, and celebrate successes together.",
        ),
        (
            3,
            "Keep It Professional Yet Faithful",
            "Balance professional advice with biblical principles. Topics can range from business ethics to finding purpose in mundane tasks.",
        ),
        (
            4,
            "No Complaints Only Solutions",
            "While venting is allowed, focus on constructive discussion and faith-based solutions to workplace challenges.",
        ),
        (
            5,
            "Respect Different Callings",
            "Not everyone has the same career path. Respect diverse vocations - homemakers, entrepreneurs, employees all have value.",
        ),
    ],
    "prayer-intercession": [
        (
            1,
            "Pray with Faith",
            "Lift your prayers with belief that God hears and answers. Approach Him with reverence and expectation.",
        ),
        (
            2,
            "Pray for Others",
            "This circle is for intercessory prayer. Lift up fellow members' needs as well as your own.",
        ),
        (
            3,
            "Be Confidential",
            "Respect privacy. What's shared in prayer should stay confidential.",
        ),
        (
            4,
            "No Gossip",
            "Prayer requests are not gossip fodder. Use them to bring needs before God, not to spread rumors.",
        ),
        (
            5,
            "Give Thanks",
            "Don't just ask - also give thanks for answered prayers and God's faithfulness.",
        ),
    ],
}


ACTIVE_ANCHORS = {
    "faith-work-purpose": {
        "anchor_type": "text",
        "title": "Reflection of the Week",
        "content": "Work as unto the Lord",
        "scripture_book": "Colossians",
        "scripture_chapter": 3,
        "scripture_verse_start": 23,
        "scripture_text": "Whatever you do, work at it with all your heart, as working for the Lord, not for men. Since you know that you will receive an inheritance from the Lord as your reward.",
        "anchor_text": "Lord, help me to see my work as an act of worship unto you. When I feel discouraged or overwhelmed, remind me that you are with me. Give me patience with colleagues and joy in serving. May my labor be a testimony of your love in the workplace.",
        "background_image": "https://images.unsplash.com/photo-1509099836639-18ba1795216d",
        "prayed_count": 62,
        "anchor_liked_count": 234,
    },
    "prayer-intercession": {
        "anchor_type": "text",
        "title": "Today's Prayer Focus",
        "content": "Trust in the Lord with all your heart and lean not on your own understanding.",
        "scripture_book": "Proverbs",
        "scripture_chapter": 3,
        "scripture_verse_start": 5,
        "scripture_verse_end": 6,
        "scripture_text": "Trust in the Lord with all your heart and lean not on your own understanding; in all your ways submit to him, and he will make your paths straight.",
        "anchor_text": "Heavenly Father, we come before you today with hearts full of trust. Help us to lean not on our own understanding but to submit all our ways to you. Direct our paths and make them straight according to your will. We pray for wisdom in every decision we face today. Help us to remember that you are sovereign over all circumstances and that you have good plans for us. Teach us to trust you even when we cannot see the outcome. Give us the faith to believe that you are working all things together for our good. In Jesus name, Amen.",
        "background_colors": ["#A8D5A2", "#EDEDED"],
        "prayed_count": 76,
        "anchor_liked_count": 156,
    },
    "youth-fellowship": {
        "anchor_type": "text",
        "title": "Youth Camp Theme",
        "content": "Rooted in Love",
        "scripture_book": "Ephesians",
        "scripture_chapter": 3,
        "scripture_verse_start": 17,
        "scripture_verse_end": 19,
        "scripture_text": "That Christ may dwell in your hearts through faith, that you, being rooted and grounded in love, may be able to comprehend with all the saints what is the width and length, height and depth, and to know Christ's love which surpasses knowledge, that you may be filled with all the fullness of God.",
        "anchor_text": "Father, root us deeply in your love so that we may understand the width, length, height, and depth of your love. May our youth group be a place where everyone feels welcomed and valued. Help us to grow together in faith and to love one another unconditionally.",
        "background_colors": ["#E8F4FD", "#FFF5E6"],
        "prayed_count": 85,
        "anchor_liked_count": 89,
    },
    "bible-study-group": {
        "anchor_type": "text",
        "title": "This Week's Study",
        "content": " Romans 8:28 - 'And we know that all things work together for good to those who love God.'",
        "scripture_book": "Romans",
        "scripture_chapter": 8,
        "scripture_verse_start": 28,
        "scripture_text": "And we know that in all things God works for the good of those who love him, who have been called according to his purpose.",
        "background_colors": ["#F5E6D3", "#FFF8F0"],
        "prayed_count": 82,
        "anchor_liked_count": 412,
    },
    "worship-praise": {
        "anchor_type": "image_text",
        "title": "Sunday Worship",
        "anchor_image": "https://images.unsplash.com/photo-1476681248696-466c012a3f1f",
        "anchor_image_text": "Let the word of Christ dwell in you richly, teaching and admonishing one another in all wisdom, and singing psalms and hymns and spiritual songs, with gratitude in your hearts to God. - Colossians 3:16",
        "anchor_text": "Lord, fill our hearts with gratitude as we worship you. Help us to let your word dwell richly within us. May our songs be pleasing to you and our gatherings glorify your name.",
        "background_colors": ["#F5E6D3", "#FFF8F0"],
        "prayed_count": 87,
        "anchor_liked_count": 178,
    },
    "marriage-family": {
        "anchor_type": "text",
        "title": "Love in Action",
        "content": "Above all, love each other deeply, because love covers a multitude of sins.",
        "scripture_book": "1 Peter",
        "scripture_chapter": 4,
        "scripture_verse_start": 8,
        "scripture_text": "Above all, love each other deeply, because love covers a multitude of sins. Be hospitable to one another without grumbling.",
        "anchor_text": "Lord, help our marriage/family to love each other more deeply each day. Teach us to be patient and kind with one another, bearing with one another's weaknesses. Help us to cover each other's sins with love rather than keeping record of wrongs. Make our home a place of hospitality and warmth where others feel welcome. May our love for each other be a reflection of your love for us. Help us to serve one another in humility, putting each other's needs before our own. In Jesus name, Amen.",
        "background_colors": ["#FCE4EC", "#F8BBD9"],
        "prayed_count": 63,
        "anchor_liked_count": 234,
    },
    "evangelism-missions": {
        "anchor_type": "video",
        "title": "Mission Trip Recap",
        "anchor_video": "https://storage.googleapis.com/ziona-media-dev/uploads/9232f97e-a63f-42a5-a7fe-eec5d153c89b/videos/efda4aab-a7a6-4816-827b-8221161cbfd0.mp4",
        "background_colors": ["#E8F4FD", "#FFF5E6"],
        "prayed_count": 65,
        "anchor_liked_count": 156,
    },
}


PAST_ANCHORS = [
    {
        "title": "Yesterday's Prayer",
        "anchor_type": "text",
        "content": "Prayer for wisdom",
        "anchor_verse": "Trust in the Lord with all your heart - Proverbs 3:5",
        "anchor_text": "Father, grant me wisdom for today.",
        "background_colors": ["#A8D5A2", "#EDEDED"],
        "days_ago": 1,
        "prayed_count": 65,
        "anchor_liked_count": 156,
    },
    {
        "title": "2 Days Ago",
        "anchor_type": "image",
        "content": "Image anchor",
        "anchor_image": "https://images.unsplash.com/photo-1519491050282-cf00c82424b4",
        "days_ago": 2,
        "prayed_count": 63,
        "anchor_liked_count": 89,
    },
    {
        "title": "3 Days Ago",
        "anchor_type": "video",
        "content": "Video anchor",
        "anchor_video": "https://storage.googleapis.com/ziona-media-dev/uploads/9232f97e-a63f-42a5-a7fe-eec5d153c89b/videos/efda4aab-a7a6-4816-827b-8221161cbfd0.mp4",
        "days_ago": 3,
        "prayed_count": 84,
        "anchor_liked_count": 234,
    },
    {
        "title": "4 Days Ago - Gradient",
        "anchor_type": "text",
        "content": "A prayer for guidance and direction",
        "anchor_verse": "Your word is a lamp to my feet - Psalm 119:105",
        "anchor_text": "Lord, shine your light on my path.",
        "background_colors": ["#4A90A4", "#1A4A5E"],
        "days_ago": 4,
        "prayed_count": 14,
        "anchor_liked_count": 312,
    },
    {
        "title": "5 Days Ago",
        "anchor_type": "image_text",
        "content": "Image with text",
        "anchor_image": "https://images.unsplash.com/photo-1476681248696-466c012a3f1f",
        "anchor_image_text": "Let everything that has breath praise the Lord - Psalm 150:6",
        "days_ago": 5,
        "prayed_count": 61,
        "anchor_liked_count": 178,
    },
    {
        "title": "6 Days Ago — Bible Verse",
        "anchor_type": "bible_verse",
        "content": "For I know the plans I have for you, declares the Lord.",
        "scripture_book": "Jeremiah",
        "scripture_chapter": 29,
        "scripture_verse_start": 11,
        "scripture_verse_end": 11,
        "scripture_translation": "NIV",
        "scripture_text": "For I know the plans I have for you, declares the Lord, plans to prosper you and not to harm you, plans to give you hope and a future.",
        "anchor_verse": "Jeremiah 29:11 (NIV)",
        "anchor_text": "Lord, remind us today that your plans for us are good. When we feel uncertain or afraid, help us to rest in the knowledge that you hold our future. Give us hope and courage to trust you fully.",
        "background_colors": ["#EDE7F6", "#D1C4E9"],
        "days_ago": 6,
        "prayed_count": 94,
        "anchor_liked_count": 267,
    },
    {
        "title": "7 Days Ago — Devotional",
        "anchor_type": "devotional",
        "content": "A 3-part devotional on walking in faith.",
        "anchor_text": "May this devotional strengthen your walk with God today.",
        "background_colors": ["#E8F5E9", "#C8E6C9"],
        "days_ago": 7,
        "prayed_count": 72,
        "anchor_liked_count": 145,
        "pages": [
            {
                "title": "Day 1: The Call",
                "content": "Abraham did not know where he was going, yet he went. Faith is not the absence of uncertainty — it is taking the next step in obedience when God calls. Today, identify one area where God is asking you to step out in trust.",
            },
            {
                "title": "Day 2: The Cost",
                "content": "Discipleship has a cost. Jesus said 'take up your cross daily'. This is not a call to misery but to surrender — releasing our grip on what we control so God can fill our hands with what He has planned.",
            },
            {
                "title": "Day 3: The Promise",
                "content": "God's faithfulness is not contingent on our performance. He who began a good work in you will carry it on to completion. Rest today in the certainty that God finishes what He starts — and He started something in you.",
            },
        ],
    },
]


POSTS = [
    (
        "faith-work-purpose",
        "sarah.kim",
        2,
        "God is so good! I just got a promotion at work! All glory to Him! 🙏",
        "",
        24,
        5,
        71,
        18,
    ),
    (
        "faith-work-purpose",
        "mike.ross",
        4,
        "Anyone looking for a job prayer? I've been searching for 3 months now. Please keep me in your prayers.",
        "https://images.unsplash.com/photo-1519491050282-cf00c82424b4",
        18,
        12,
        49,
        12,
    ),
    (
        "faith-work-purpose",
        "grace.lee",
        6,
        "Today's devotional: 'Whatever you do, work at it with your whole being as for the Lord...' Colossians 3:23",
        "",
        45,
        8,
        5,
        32,
    ),
    (
        "faith-work-purpose",
        "james.chen",
        24,
        "Thank you all for the prayers! I got the job! God is faithful!",
        "https://images.unsplash.com/photo-1519389950473-63b5a5b7f5e3",
        67,
        23,
        36,
        54,
    ),
    (
        "prayer-intercession",
        "rebecca.stone",
        1,
        "Praying for my sick grandmother. God is our healer!",
        "https://images.unsplash.com/photo-1509099836639-18ba1795216d",
        12,
        3,
        0,
        0,
    ),
    (
        "prayer-intercession",
        "david.wilson",
        3,
        "Our prayer meeting this Sunday was amazing. God's presence was so strong!",
        "",
        28,
        7,
        0,
        0,
    ),
    (
        "youth-fellowship",
        "emma.davis",
        5,
        "Youth camp was life-changing! Can't wait for next year! 🔥",
        "",
        56,
        15,
        0,
        0,
    ),
    (
        "bible-study-group",
        "pastor.michael",
        24,
        "Great discussion on Romans 8 yesterday! The promise that all things work together for good gives me so much hope.",
        "",
        34,
        9,
        0,
        0,
    ),
    (
        "worship-praise",
        "sarah.worship",
        24,
        "What an amazing worship session! God's presence was so tangible!",
        "",
        45,
        8,
        0,
        0,
    ),
    (
        "marriage-family",
        "john.family",
        48,
        "Family devotion time is the best part of our day!",
        "",
        32,
        6,
        0,
        0,
    ),
    (
        "evangelism-missions",
        "mission.mark",
        120,
        "Our mission trip was incredible! God is doing amazing work!",
        "",
        67,
        12,
        0,
        0,
    ),
]


class Command(BaseCommand):
    help = "Seed staging/dev with mobile-ready sample Circles, Anchors, and Circle posts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running outside dev/staging. Use only with an intentional sample-data target.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self._ensure_safe_environment(force=options["force"])

        self.stdout.write("Seeding mobile Circles sample data...")
        admin = self._get_or_create_sample_admin()
        users = self._get_or_create_sample_users()
        circles = self._upsert_circles(admin)
        self._upsert_circle_rules(circles)
        self._upsert_memberships(circles, admin, users)
        self._upsert_anchors(circles, admin)
        self._upsert_posts(circles, users)

        self.stdout.write(self.style.SUCCESS("Mobile Circles sample data is ready."))

    def _ensure_safe_environment(self, force: bool) -> None:
        settings_module = os.environ.get("DJANGO_SETTINGS_MODULE") or getattr(
            settings, "SETTINGS_MODULE", ""
        )
        allowed_modules = (".dev", ".staging", ".test")
        allowed = force or settings.DEBUG or settings_module.endswith(allowed_modules)
        if not allowed:
            raise CommandError(
                "Refusing to seed sample data outside dev/staging. Re-run with --force only for an intentional sample-data target."
            )

    def _get_or_create_sample_admin(self) -> User:
        admin, created = User.all_objects.get_or_create(
            email=SYSTEM_EMAIL,
            defaults={
                "username": "circles_sample_admin",
                "full_name": "Ziona Sample Admin",
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
                "is_email_verified": True,
            },
        )
        if created:
            admin.set_unusable_password()
            admin.save(update_fields=["password"])
        return admin

    def _get_or_create_sample_users(self) -> dict[str, User]:
        users = {}
        for full_name, username, avatar_url in USERS:
            email = f"{username}@sample.ziona.app"
            user, created = User.all_objects.get_or_create(
                email=email,
                defaults={
                    "username": username.replace(".", "_"),
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                    "is_active": True,
                    "is_email_verified": True,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])

            update_fields = ["full_name", "avatar_url", "is_active", "is_email_verified"]
            user.full_name = full_name
            user.avatar_url = avatar_url
            user.is_active = True
            user.is_email_verified = True
            desired_username = username.replace(".", "_")
            username_taken = (
                User.all_objects.filter(username=desired_username).exclude(pk=user.pk).exists()
            )
            if not username_taken:
                user.username = desired_username
                update_fields.append("username")
            user.save(update_fields=update_fields)
            users[username] = user
        return users

    def _upsert_circles(self, admin: User) -> dict[str, Circle]:
        circles = {}
        for data in CIRCLES:
            circle, _ = Circle.objects.update_or_create(
                name=data["name"],
                defaults={
                    "description": data["description"],
                    "cover_image": data["image"],
                    "banner_image": data["image"],
                    "profile_image_url": data["profile_image"],
                    "display_member_count": data["members"],
                    "created_by": admin,
                    "is_active": True,
                    "status": "active",
                    "deleted_at": None,
                },
            )
            circles[data["key"]] = circle
        return circles

    def _upsert_circle_rules(self, circles: dict[str, Circle]) -> None:
        for circle_key, rules in CIRCLE_RULES.items():
            circle = circles[circle_key]
            for rule_number, title, description in rules:
                CircleRule.objects.update_or_create(
                    circle=circle,
                    rule_number=rule_number,
                    defaults={
                        "title": title,
                        "description": description,
                        "is_default": False,
                    },
                )

    def _upsert_memberships(
        self,
        circles: dict[str, Circle],
        admin: User,
        users: dict[str, User],
    ) -> None:
        for circle in circles.values():
            CircleMembership.objects.get_or_create(
                circle=circle,
                user=admin,
                defaults={"role": "admin"},
            )

        post_members = {username for _, username, *_ in POSTS}
        for username in post_members:
            user = users[username]
            for circle_key, post_username, *_ in POSTS:
                if post_username == username:
                    CircleMembership.objects.get_or_create(
                        circle=circles[circle_key],
                        user=user,
                        defaults={"role": "member"},
                    )

    def _upsert_anchors(self, circles: dict[str, Circle], admin: User) -> None:
        now = timezone.now()
        for circle_key, data in ACTIVE_ANCHORS.items():
            published_at = now - timedelta(minutes=5)
            self._upsert_anchor(
                circle=circles[circle_key],
                admin=admin,
                data=data,
                published_at=published_at,
                expires_at=published_at + timedelta(hours=24),
            )

        faith_circle = circles["faith-work-purpose"]
        for data in PAST_ANCHORS:
            published_at = now - timedelta(days=data["days_ago"], minutes=5)
            self._upsert_anchor(
                circle=faith_circle,
                admin=admin,
                data=data,
                published_at=published_at,
                expires_at=published_at + timedelta(hours=24),
            )

    def _upsert_anchor(
        self,
        circle: Circle,
        admin: User,
        data: dict,
        published_at,
        expires_at,
    ) -> Anchor:
        defaults = {
            "created_by": admin,
            "anchor_type": data["anchor_type"],
            "content": data.get("content", ""),
            "scripture_book": data.get("scripture_book", ""),
            "scripture_chapter": data.get("scripture_chapter"),
            "scripture_verse_start": data.get("scripture_verse_start"),
            "scripture_verse_end": data.get("scripture_verse_end"),
            "scripture_translation": data.get("scripture_translation", "KJV"),
            "scripture_text": data.get("scripture_text", ""),
            "media_url": data.get("media_url", ""),
            "anchor_image": data.get("anchor_image", ""),
            "anchor_video": data.get("anchor_video", ""),
            "anchor_thumbnail": data.get("anchor_thumbnail", ""),
            "background_colors": data.get("background_colors", []),
            "background_image": data.get("background_image", ""),
            "anchor_text": data.get("anchor_text", ""),
            "anchor_verse": data.get("anchor_verse", ""),
            "anchor_image_text": data.get("anchor_image_text", ""),
            "prayed_count": data.get("prayed_count", 0),
            "anchor_liked_count": data.get("anchor_liked_count", 0),
            "anchor_status": "posted",
            "published_at": published_at,
            "posted_at": published_at,
            "expires_at": expires_at,
            "deleted_at": None,
        }
        anchor, _ = Anchor.objects.update_or_create(
            circle=circle,
            title=data["title"],
            defaults=defaults,
        )

        # Seed AnchorPage entries for devotional anchors
        if data["anchor_type"] == "devotional" and data.get("pages"):
            for idx, page_data in enumerate(data["pages"], start=1):
                AnchorPage.objects.update_or_create(
                    anchor=anchor,
                    page_number=idx,
                    defaults={
                        "title": page_data.get("title", ""),
                        "content": page_data.get("content", ""),
                        "media_url": page_data.get("media_url", ""),
                    },
                )

        return anchor

    def _upsert_posts(self, circles: dict[str, Circle], users: dict[str, User]) -> None:
        now = timezone.now()
        for (
            circle_key,
            username,
            hours_ago,
            text,
            image_url,
            likes_count,
            comments_count,
            prayed_count,
            anchor_liked_count,
        ) in POSTS:
            post, _ = CirclePost.objects.update_or_create(
                circle=circles[circle_key],
                user=users[username],
                text=text,
                defaults={
                    "image_url": image_url,
                    "likes_count": likes_count,
                    "comments_count": comments_count,
                    "prayed_count": prayed_count,
                    "anchor_liked_count": anchor_liked_count,
                    "deleted_at": None,
                },
            )
            post.created_at = now - timedelta(hours=hours_ago)
            post.save(update_fields=["created_at"])
