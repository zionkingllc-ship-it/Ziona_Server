"""Static help-center content and lookup helpers for the mobile help flow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpArticle:
    slug: str
    title: str
    summary: str
    content: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpCategory:
    slug: str
    title: str
    description: str
    articles: tuple[HelpArticle, ...]


HELP_CATEGORIES: tuple[HelpCategory, ...] = (
    HelpCategory(
        slug="account-management",
        title="Account management",
        description="Sign in, recover access, and update your account details.",
        articles=(
            HelpArticle(
                slug="recover-account-access",
                title="Recover account access",
                summary="What to do when you cannot sign in or verify your account.",
                content=(
                    "If you cannot sign in, first confirm that your email address is correct. "
                    "For password accounts, use the password reset flow. For Google or Apple "
                    "sign-in, continue with the same provider you used to create the account. "
                    "If you are still blocked, contact support with the email tied to the account."
                ),
                keywords=("login", "sign in", "reset", "verify", "otp"),
            ),
            HelpArticle(
                slug="update-profile-details",
                title="Update profile details",
                summary="Change your name, username, bio, and profile photo.",
                content=(
                    "You can update your profile from Edit Profile. Name and username changes "
                    "follow cooldown rules, while bio and bio link can be updated anytime. "
                    "If your changes are not saving, make sure the new value passes validation "
                    "and try again after refreshing your session."
                ),
                keywords=("profile", "bio", "username", "name", "photo"),
            ),
        ),
    ),
    HelpCategory(
        slug="safety-and-security",
        title="Safety and security",
        description="Protect your account and report harmful content.",
        articles=(
            HelpArticle(
                slug="report-content-or-users",
                title="Report content or users",
                summary="How to report posts, comments, circles, and user accounts.",
                content=(
                    "Use the report action on the post, comment, circle item, or profile that "
                    "concerns you. Reports are reviewed by the moderation team. Reported content "
                    "should disappear for the reporting user once the report is accepted by the backend."
                ),
                keywords=("report", "moderation", "abuse", "safety"),
            ),
            HelpArticle(
                slug="secure-your-account",
                title="Secure your account",
                summary="Best practices for protecting your Ziona account.",
                content=(
                    "Use a strong password for email-based accounts, keep your email inbox secure, "
                    "and avoid sharing verification codes. If you believe your account has been "
                    "compromised, reset your password immediately or contact support."
                ),
                keywords=("security", "password", "compromised", "email", "account"),
            ),
        ),
    ),
    HelpCategory(
        slug="posts-and-circles",
        title="Posts and circles",
        description="Help with posting, media uploads, and circles.",
        articles=(
            HelpArticle(
                slug="upload-photos-and-videos",
                title="Upload photos and videos",
                summary="Supported media rules and why uploads may take time.",
                content=(
                    "Uploads use a signed upload URL and then background processing. A file can look "
                    "stuck near completion while the backend is optimizing it. Videos must stay within "
                    "the allowed size and duration limits, and a post can include at most one video."
                ),
                keywords=("upload", "video", "photo", "media", "processing"),
            ),
            HelpArticle(
                slug="post-in-circles",
                title="Post in circles",
                summary="Create and manage circle posts with text, images, and video.",
                content=(
                    "Circle posts now follow the same media contract as feed posts. Upload media first, "
                    "confirm the upload, wait for the media to be ready, and then create the circle post "
                    "with the returned media IDs."
                ),
                keywords=("circles", "circle post", "media", "anchor"),
            ),
        ),
    ),
)


def list_help_categories(search: str = "") -> list[HelpCategory]:
    """Return help categories, optionally filtered by category/article text."""
    needle = (search or "").strip().lower()
    if not needle:
        return list(HELP_CATEGORIES)

    matched: list[HelpCategory] = []
    for category in HELP_CATEGORIES:
        haystacks = [
            category.title.lower(),
            category.description.lower(),
            *[
                " ".join(
                    [
                        article.title.lower(),
                        article.summary.lower(),
                        article.content.lower(),
                        " ".join(keyword.lower() for keyword in article.keywords),
                    ]
                )
                for article in category.articles
            ],
        ]
        if any(needle in haystack for haystack in haystacks):
            matched.append(category)
    return matched


def get_help_category(slug: str) -> HelpCategory | None:
    """Fetch a single help category by slug."""
    normalized = (slug or "").strip().lower()
    for category in HELP_CATEGORIES:
        if category.slug == normalized:
            return category
    return None


def list_help_articles(category_slug: str | None = None, search: str = "") -> list[HelpArticle]:
    """Return help articles filtered by category and free-text search."""
    needle = (search or "").strip().lower()
    categories = HELP_CATEGORIES
    if category_slug:
        category = get_help_category(category_slug)
        if not category:
            return []
        categories = (category,)

    articles: list[HelpArticle] = []
    for category in categories:
        for article in category.articles:
            if needle:
                haystack = " ".join(
                    [
                        article.title.lower(),
                        article.summary.lower(),
                        article.content.lower(),
                        " ".join(keyword.lower() for keyword in article.keywords),
                        category.title.lower(),
                    ]
                )
                if needle not in haystack:
                    continue
            articles.append(article)
    return articles


def get_help_article(slug: str) -> tuple[HelpCategory, HelpArticle] | None:
    """Return the category/article tuple for a given article slug."""
    normalized = (slug or "").strip().lower()
    for category in HELP_CATEGORIES:
        for article in category.articles:
            if article.slug == normalized:
                return category, article
    return None
