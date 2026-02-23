from django.contrib import admin
from django.urls import path, include

from config.graphql_schema import schema
from config.swagger import swagger_ui, openapi_schema
from strawberry.django.views import GraphQLView

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
]
