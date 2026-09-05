"""
Deep linking views and share preview endpoints.

Handles:
- /.well-known/apple-app-site-association (iOS Universal Links)
- /.well-known/assetlinks.json (Android App Links)
- /post/<post_id>/ (Share preview with OG meta tags)
- /profile/<user_id>/ (Profile share preview with OG meta tags)
"""

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control

from core.media.ordering import (
    POST_MEDIA_POSITION,
    order_media_by_position,
    ordered_post_media_prefetch,
)
from core.posts.models import Post
from core.shared.utils import build_post_share_url, build_profile_share_url, parse_uuid

# URL paths handed to the app when a Universal Link is tapped, as
# (path, human-readable note) pairs. A path needs no server route to be listed
# here — the file only tells iOS which URLs belong to the app. `/viewer/*` is
# intentionally app-only: it has no Django view, so it has no web fallback and no
# Open Graph preview for crawlers.
_UNIVERSAL_LINK_PATHS = (
    ("/post/*", "Open shared post URLs in the Ziona app"),
    ("/profile/*", "Open shared profile URLs in the Ziona app"),
    ("/viewer/*", "Open viewer URLs in the Ziona app (app-only, no web page)"),
)


def _ios_app_id() -> str:
    """Build the Universal Links appID as ``<TeamID>.<bundle id>``.

    Falls back to a visible ``TEAMID`` placeholder until APPLE_TEAM_ID is set so
    a missing Team ID is obvious rather than silently invalid.
    """
    team_id = settings.APPLE_TEAM_ID or "TEAMID"
    bundle_id = getattr(settings, "APPLE_BUNDLE_ID", "") or "com.zionking.ziona"
    return f"{team_id}.{bundle_id}"


@cache_control(max_age=86400)
def apple_app_site_association(request: HttpRequest) -> JsonResponse:
    """Serve the Apple App Site Association file for iOS Universal Links.

    Must be reachable at https://<share domain>/.well-known/apple-app-site-association
    with no file extension and Content-Type application/json (both satisfied here).

    Uses the modern ``appIDs``/``components`` shape (iOS 13+) rather than the
    legacy ``appID``/``paths`` one, so an ``exclude`` rule can opt individual URLs
    out of opening the app.
    """
    components = [
        {
            "#": "no_universal_links",
            "exclude": True,
            "comment": "Matches any URL whose fragment begins with no_universal_links",
        },
    ]
    components += [{"/": path, "comment": note} for path, note in _UNIVERSAL_LINK_PATHS]
    data = {
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appIDs": [_ios_app_id()],
                    "components": components,
                }
            ],
        },
    }
    return JsonResponse(data)


@cache_control(max_age=86400)
def android_asset_links(request: HttpRequest) -> JsonResponse:
    """Serve the Android Asset Links file for App Links.

    Must be reachable at https://<share domain>/.well-known/assetlinks.json.
    Values come from settings so the package/fingerprints can differ per env and
    support both the Play App Signing key and the upload key.
    """
    data = [
        {
            "relation": [
                "delegate_permission/common.handle_all_urls",
                "delegate_permission/common.get_login_creds",
            ],
            "target": {
                "namespace": "android_app",
                "package_name": settings.ANDROID_APP_PACKAGE_NAME,
                "sha256_cert_fingerprints": list(settings.ANDROID_SHA256_CERT_FINGERPRINTS),
            },
        }
    ]
    return JsonResponse(data, safe=False)


def share_preview(request: HttpRequest, post_id: str) -> HttpResponse:
    """Render a share preview page with Open Graph meta tags.

    This page is what crawlers (Facebook, Twitter, iMessage, etc.)
    will see when a user shares a Ziona post link.
    """
    if parse_uuid(post_id) is None:
        return HttpResponse("Post not found", status=404)

    post = (
        Post.objects.select_related("user")
        .prefetch_related(ordered_post_media_prefetch(), "post_media")
        .filter(id=post_id, deleted_at__isnull=True)
        .first()
    )

    if not post:
        return HttpResponse("Post not found", status=404)

    # Try media_files first (new path), fallback to post_media (legacy).
    # Order by selected position so the OG preview uses the first chosen image.
    media = (
        order_media_by_position(post.media_files, POST_MEDIA_POSITION).first()
        or post.post_media.first()
    )
    preview_image = None
    if media:
        preview_image = (
            getattr(media, "thumbnail_url", None)
            or getattr(media, "url", None)
            or getattr(media, "media_url", None)
        )

    context = {
        "post": post,
        "author": post.user,
        "preview_image": preview_image,
        "caption": post.caption or "Check out this post on Ziona!",
        "post_url": build_post_share_url(settings.APP_SHARE_BASE_URL, post_id),
        "app_name": "Ziona",
        # Store fallbacks for the app-not-installed case.
        "ios_app_store_url": settings.IOS_APP_STORE_URL,
        "android_play_store_url": settings.ANDROID_PLAY_STORE_URL,
    }

    return render(request, "share_preview.html", context)


def profile_share_preview(request: HttpRequest, user_id: str) -> HttpResponse:
    """Render a public preview page for a shared Ziona profile."""
    if parse_uuid(user_id) is None:
        return HttpResponse("Profile not found", status=404)

    from core.users.models import User

    user = User.objects.filter(id=user_id, deleted_at__isnull=True).first()
    if not user:
        return HttpResponse("Profile not found", status=404)

    display_name = user.full_name or user.username or "Ziona creator"
    description = user.bio or f"View {display_name}'s profile on Ziona."
    context = {
        "profile_user": user,
        "display_name": display_name,
        "description": description,
        "profile_url": build_profile_share_url(settings.APP_SHARE_BASE_URL, user_id),
        "preview_image": user.avatar_url or None,
        "app_name": "Ziona",
        "ios_app_store_url": settings.IOS_APP_STORE_URL,
        "android_play_store_url": settings.ANDROID_PLAY_STORE_URL,
    }

    return render(request, "profile_share_preview.html", context)
