import os

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from django.views.static import serve
from strawberry.django.views import GraphQLView

from config.graphql_schema import schema
from config.swagger import openapi_schema, swagger_ui
from core.donations.webhooks import stripe_webhook
from core.posts.views import (
    android_asset_links,
    apple_app_site_association,
    share_preview,
)


def build_urlpatterns():
    """Build routes from environment flags so prod can disable public surfaces cleanly."""
    patterns = [
        path("health/", include("health_check.urls")),
        path(
            "graphql/",
            csrf_exempt(GraphQLView.as_view(schema=schema)),
            name="graphql",
        ),
        path(
            "graphql",
            csrf_exempt(GraphQLView.as_view(schema=schema)),
            name="graphql_no_slash",
        ),
        path("api/auth/", include("core.authentication.urls")),
        path("api/payments/", include("core.donations.urls")),
        path(
            ".well-known/apple-app-site-association",
            apple_app_site_association,
            name="apple-app-site-association",
        ),
        path(
            ".well-known/assetlinks.json",
            android_asset_links,
            name="android-asset-links",
        ),
        path("post/<str:post_id>/", share_preview, name="share-preview"),
        path("api/webhooks/stripe/", stripe_webhook, name="stripe-webhook"),
    ]

    if settings.ENABLE_DJANGO_ADMIN:
        patterns.insert(0, path("admin/", admin.site.urls))

    if settings.ENABLE_PUBLIC_API_DOCS:
        patterns.extend(
            [
                path("docs/", swagger_ui, name="swagger-ui"),
                path("api/schema/", openapi_schema, name="openapi-schema"),
            ]
        )

    if settings.ENABLE_GRAPHQL_STATIC_DOCS:
        patterns.extend(
            [
                path(
                    "graphql-docs/",
                    lambda r: serve(
                        r,
                        "index.html",
                        document_root=os.path.join(settings.BASE_DIR, "docs", "graphql-docs"),
                    ),
                    name="graphql-docs-index",
                ),
                path(
                    "graphql-docs/<path:path>",
                    serve,
                    {"document_root": os.path.join(settings.BASE_DIR, "docs", "graphql-docs")},
                ),
            ]
        )

    return patterns


urlpatterns = build_urlpatterns()


def custom_404_handler(request, exception=None):
    """Return standardized JSON response for 404 errors."""
    return JsonResponse(
        {
            "success": False,
            "error": {
                "code": "NOT_FOUND",
                "message": "Endpoint not found",
                "path": request.path,
            },
        },
        status=404,
    )


def custom_500_handler(request):
    """Return standardized JSON response for 500 server errors."""
    return JsonResponse(
        {
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
            },
        },
        status=500,
    )


def custom_400_handler(request, exception=None):
    """Return standardized JSON response for 400 bad requests.

    Django raises SuspiciousOperation/DisallowedHost/BadRequest at its core
    handler layer — before GlobalExceptionMiddleware runs — and would otherwise
    render an HTML error page. API clients parse JSON, so an HTML body breaks
    them (the "Unexpected character: <" failures). Returning JSON here keeps
    every rejection parseable and debuggable. request.path is safe to read even
    for DisallowedHost (it comes from PATH_INFO, not the Host header).
    """
    return JsonResponse(
        {
            "success": False,
            "error": {
                "code": "BAD_REQUEST",
                "message": "Bad request",
                "path": request.path,
            },
        },
        status=400,
    )


handler400 = "config.urls.custom_400_handler"
handler404 = "config.urls.custom_404_handler"
handler500 = "config.urls.custom_500_handler"
