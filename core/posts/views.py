"""
Deep linking views and share preview endpoints.

Handles:
- /.well-known/apple-app-site-association (iOS Universal Links)
- /.well-known/assetlinks.json (Android App Links)
- /post/<post_id>/ (Share preview with OG meta tags)
"""


from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control

from core.posts.models import Post


@cache_control(max_age=86400)
def apple_app_site_association(request: HttpRequest) -> JsonResponse:
    """Serve the Apple App Site Association file for iOS Universal Links."""
    data = {
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appID": "TEAMID.com.ziona.app",
                    "paths": ["/post/*"],
                }
            ],
        },
    }
    return JsonResponse(data)


@cache_control(max_age=86400)
def android_asset_links(request: HttpRequest) -> JsonResponse:
    """Serve the Android Asset Links file for App Links."""
    data = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.ziona.app",
                "sha256_cert_fingerprints": [],
            },
        }
    ]
    return JsonResponse(data, safe=False)


def share_preview(request: HttpRequest, post_id: str) -> HttpResponse:
    """Render a share preview page with Open Graph meta tags.

    This page is what crawlers (Facebook, Twitter, iMessage, etc.)
    will see when a user shares a Ziona post link.
    """
    post = (
        Post.objects.select_related("user")
        .prefetch_related("post_media")
        .filter(id=post_id, deleted_at__isnull=True)
        .first()
    )

    if not post:
        return HttpResponse("Post not found", status=404)

    media = post.post_media.first()
    preview_image = None
    if media:
        preview_image = media.thumbnail_url or media.media_url

    context = {
        "post": post,
        "author": post.user,
        "preview_image": preview_image,
        "caption": post.caption or "Check out this post on Ziona!",
        "post_url": f"https://ziona.app/post/{post_id}",
        "app_name": "Ziona",
    }

    return render(request, "share_preview.html", context)
