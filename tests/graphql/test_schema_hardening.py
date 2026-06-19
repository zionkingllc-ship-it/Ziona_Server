from django.test import override_settings

from config.graphql_schema import build_schema
from config.settings.base import validate_non_debug_runtime_settings
from config.urls import build_urlpatterns
from core.users.schema import AuthenticatedUserType, UserType


def _routes():
    return {pattern.pattern._route for pattern in build_urlpatterns()}


def test_public_user_type_excludes_sensitive_fields():
    public_fields = set(UserType.__annotations__)
    private_fields = set(AuthenticatedUserType.__annotations__)

    assert "email" not in public_fields
    assert "role" not in public_fields
    assert "is_email_verified" not in public_fields

    assert {"email", "role", "is_email_verified"}.issubset(private_fields)


@override_settings(
    ENABLE_DJANGO_ADMIN=False,
    ENABLE_PUBLIC_API_DOCS=False,
    ENABLE_GRAPHQL_STATIC_DOCS=False,
)
def test_production_style_routes_disable_admin_and_docs():
    routes = _routes()

    assert "admin/" not in routes
    assert "docs/" not in routes
    assert "api/schema/" not in routes
    assert "graphql-docs/" not in routes
    assert "health/" in routes
    assert "graphql/" in routes


@override_settings(
    ENABLE_DJANGO_ADMIN=True,
    ENABLE_PUBLIC_API_DOCS=True,
    ENABLE_GRAPHQL_STATIC_DOCS=True,
)
def test_dev_style_routes_keep_admin_and_docs_enabled():
    routes = _routes()

    assert "admin/" in routes
    assert "docs/" in routes
    assert "api/schema/" in routes
    assert "graphql-docs/" in routes


@override_settings(
    GRAPHQL_MAX_DEPTH=2,
    GRAPHQL_MAX_ALIASES=10,
    GRAPHQL_MAX_COMPLEXITY=1000,
    GRAPHQL_INTROSPECTION_ENABLED=True,
)
def test_graphql_depth_limit_is_enforced():
    schema = build_schema()

    result = schema.execute_sync("{ feed(limit: 1) { posts { author { username } } } }")

    assert result.errors
    assert "maximum operation depth" in result.errors[0].message


@override_settings(
    GRAPHQL_MAX_DEPTH=10,
    GRAPHQL_MAX_ALIASES=10,
    GRAPHQL_MAX_COMPLEXITY=2,
    GRAPHQL_INTROSPECTION_ENABLED=True,
)
def test_graphql_complexity_limit_is_enforced():
    schema = build_schema()

    result = schema.execute_sync("{ a: health b: health c: health d: health }")

    assert result.errors
    assert "Query complexity 4 exceeds the allowed maximum of 2." in result.errors[0].message


@override_settings(
    GRAPHQL_MAX_DEPTH=10,
    GRAPHQL_MAX_ALIASES=2,
    GRAPHQL_MAX_COMPLEXITY=1000,
    GRAPHQL_INTROSPECTION_ENABLED=True,
)
def test_graphql_alias_limit_is_enforced():
    schema = build_schema()

    result = schema.execute_sync("{ a: health b: health c: health }")

    assert result.errors
    assert "aliases found" in result.errors[0].message


@override_settings(
    GRAPHQL_MAX_DEPTH=10,
    GRAPHQL_MAX_ALIASES=10,
    GRAPHQL_MAX_COMPLEXITY=1000,
    GRAPHQL_INTROSPECTION_ENABLED=False,
)
def test_graphql_introspection_can_be_disabled():
    schema = build_schema()

    result = schema.execute_sync("{ __schema { queryType { name } } }")

    assert result.errors
    assert "GraphQL introspection has been disabled" in result.errors[0].message


def test_non_debug_runtime_validation_rejects_unsafe_defaults():
    try:
        validate_non_debug_runtime_settings(
            environment_name="production",
            config={
                "SECRET_KEY": "insecure-dev-key-change-me",  # pragma: allowlist secret
                "JWT_SECRET_KEY": "insecure-dev-key-change-me",  # pragma: allowlist secret
                "ENCRYPTION_KEY": "",
                "GCP_STORAGE_BUCKET": "ziona-media-dev",
                "GCP_CREDENTIALS_FILE": "",
                "FIREBASE_CREDENTIALS_FILE": "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected validate_non_debug_runtime_settings to fail")

    assert "DJANGO_SECRET_KEY must be set to a non-default value" in message
    assert "JWT_SECRET_KEY must be set independently from DJANGO_SECRET_KEY" in message
    assert "ENCRYPTION_KEY must be configured" in message
    assert "Production must not use the ziona-media-dev bucket" in message


def test_non_debug_runtime_validation_accepts_valid_config():
    validate_non_debug_runtime_settings(
        environment_name="staging",
        config={
            "SECRET_KEY": "staging-secret-key-that-is-not-default",  # pragma: allowlist secret
            "JWT_SECRET_KEY": "staging-jwt-secret-key-that-is-different",  # pragma: allowlist secret
            "ENCRYPTION_KEY": "test-encryption-key",  # pragma: allowlist secret
            "GCP_STORAGE_BUCKET": "ziona-media-dev",
            "GCP_CREDENTIALS_FILE": "/etc/secrets/gcp-credentials.json",
            "FIREBASE_CREDENTIALS_FILE": "/etc/secrets/firebase-credentials.json",
        },
    )
