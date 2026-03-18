# Circle Error Codes
CIRCLE_ERROR_CODES = {
    # Circle errors
    "CIRCLE_NOT_FOUND": "Circle does not exist or has been deleted",
    "CIRCLE_INACTIVE": "This Circle is no longer active",
    "ALREADY_MEMBER": "You are already a member of this Circle",
    "NOT_MEMBER": "You are not a member of this Circle",
    "CANNOT_LEAVE_LAST_ADMIN": "Cannot leave Circle as the only admin",
    # Anchor errors
    "ANCHOR_NOT_FOUND": "Anchor does not exist",
    "ANCHOR_EXPIRED": "This anchor has expired (24-hour window passed)",
    "ANCHOR_NOT_ACTIVE_YET": "Anchor is scheduled for future publication",
    "NO_ACTIVE_ANCHOR": "No active anchor found for this Circle",
    "INVALID_ANCHOR_TYPE": "Anchor type must be bible_verse, devotional, image, or video",
    "MISSING_SCRIPTURE_REFERENCE": "Bible verse anchors require scripture reference",
    "CANNOT_SCHEDULE_PAST": "Cannot schedule an anchor in the past",
    "SCHEDULE_TOO_FAR": "Cannot schedule an anchor more than 30 days in advance",
    "OVERLAPPING_ANCHOR": "An active anchor already exists for this time window",
    # Response errors
    "INVALID_RESPONSE_TYPE": "Response type must be pray_for_me, encouraged_me, or made_me_think",
    "RESPONSE_CONTENT_TOO_SHORT": "Response must be at least 10 characters",
    "RESPONSE_CONTENT_TOO_LONG": "Response cannot exceed 2000 characters",
    "THREADING_DEPTH_EXCEEDED": "Cannot reply to a reply. Maximum 2 levels allowed",
    "PARENT_RESPONSE_NOT_FOUND": "Parent response does not exist",
    # Reaction errors
    "INVALID_REACTION_TYPE": "Reaction type must be amen, encouraged, or thoughtful",
    "RESPONSE_NOT_FOUND": "Response does not exist",
    # Media errors
    "VIDEO_TOO_SHORT": "Video must be at least 15 seconds",
    "VIDEO_TOO_LONG": "Video must be no longer than 30 seconds",
    "INVALID_MEDIA_TYPE": "Media type must be image or video",
    # Permission errors
    "NOT_CIRCLE_ADMIN": "Only Circle admins can perform this action",
    "CANNOT_CREATE_ANCHOR": "You do not have permission to create anchors",
    "CANNOT_REPORT_OWN_CONTENT": "You cannot report your own content",
    # Moderation errors
    "CONTENT_ALREADY_REPORTED": "You have already reported this content",
    "INVALID_REPORT_REASON": "Invalid report reason provided",
}

# Response Type Prompts (for GraphQL schema / frontend)
RESPONSE_TYPE_PROMPTS = {
    "pray_for_me": "Did this touch something personal? Tell us how we can pray",
    "encouraged_me": "Did this strengthen you today? Tell us what stood out",
    "made_me_think": "What has stayed with you? Share it below",
}

# Default Circle Rules (9 rules)
CIRCLE_RULES = [
    {
        "rule_number": 1,
        "title": "Be kind",
        "description": "Treat every member with kindness and respect. Disagreements are allowed, but personal attacks, insults, or harsh language are not.",
    },
    {
        "rule_number": 2,
        "title": "Keep It Faith-Centered",
        "description": "Posts and discussions should align with the purpose of this circle—encouraging faith, prayer, reflection, and spiritual growth.",
    },
    {
        "rule_number": 3,
        "title": "No Hate or Harmful Speech",
        "description": "Discrimination, hate speech, bullying, or harassment of any kind will not be tolerated.",
    },
    {
        "rule_number": 4,
        "title": "Protect Privacy",
        "description": "Do not share personal or private information about yourself or others without permission.",
    },
    {
        "rule_number": 5,
        "title": "Be Genuine",
        "description": "Share authentically. Avoid misleading content, false teachings, or spam.",
    },
    {
        "rule_number": 6,
        "title": "No Promotion or Advertising",
        "description": "This circle is for community and encouragement. Promotional content, selling, or self-advertising is not allowed unless approved by moderators.",
    },
    {
        "rule_number": 7,
        "title": "Encourage, Don't Judge",
        "description": "Many members may be in different stages of faith. Offer encouragement rather than criticism.",
    },
    {
        "rule_number": 8,
        "title": "Follow Platform Rules",
        "description": "All activity must follow the overall community guidelines of the platform.",
    },
    {
        "rule_number": 9,
        "title": "Report Harmful Content",
        "description": "If you see content that violates these guidelines, please report it so moderators can review it.",
    },
]

# Default Circles for Seeding (6 circles)
DEFAULT_CIRCLES = [
    {
        "name": "Christianity and Life Struggles",
        "description": "A safe and supportive community for believers facing personal struggles such as anxiety, depression, addiction, or temptation.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/christianity-life-struggles.jpg",
    },
    {
        "name": "Prayer & Intercession",
        "description": "This circle is for believers to come together to pray for one another. Members can share prayer requests, intercede for others, and witness answered prayers.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/prayer-intercession.jpg",
    },
    {
        "name": "Faith, Work & Purpose",
        "description": "A community where Christians discuss career, business, finances and finding purpose in work while honoring God.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/faith-work-purpose.jpg",
    },
    {
        "name": "New Believers",
        "description": "A supportive community for those who have recently given their lives to Christ and want guidance in growing spiritually.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/new-believers.jpg",
    },
    {
        "name": "Bible Study & Learning",
        "description": "Dedicated to studying scripture, asking questions and growing in biblical understanding together.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/bible-study.jpg",
    },
    {
        "name": "Marriage & Relationships",
        "description": "A faith-centered community for discussing Christian relationships, dating, marriage, and family.",
        "cover_image": "https://storage.googleapis.com/ziona-assets/circles/marriage-relationships.jpg",
    },
]
