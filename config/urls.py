from django.contrib import admin
from django.urls import include, path
from strawberry.django.views import GraphQLView

from config.graphql_schema import schema
from config.swagger import openapi_schema, swagger_ui
from core.posts.views import (
    android_asset_links,
    apple_app_site_association,
    share_preview,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("health_check.urls")),
    path(
        "graphql/",
        GraphQLView.as_view(schema=schema),
        name="graphql",
    ),
    path("api/auth/", include("core.authentication.urls")),
    path("docs/", swagger_ui, name="swagger-ui"),
    path("api/schema/", openapi_schema, name="openapi-schema"),
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
]
