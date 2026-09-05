"""Deep-link well-known files + share-preview store fallback (Ticket 12)."""

import pytest

from core.posts.models import Post
from core.users.models import User


@pytest.mark.django_db
def test_android_assetlinks_reflects_settings(client, settings):
    settings.ANDROID_APP_PACKAGE_NAME = "com.zionking.ziona"
    settings.ANDROID_SHA256_CERT_FINGERPRINTS = ["AA:BB:CC"]

    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    entry = resp.json()[0]
    assert entry["target"]["package_name"] == "com.zionking.ziona"
    assert entry["target"]["sha256_cert_fingerprints"] == ["AA:BB:CC"]
    assert "delegate_permission/common.handle_all_urls" in entry["relation"]
    assert "delegate_permission/common.get_login_creds" in entry["relation"]


@pytest.mark.django_db
def test_android_assetlinks_default_matches_release_fingerprints(client):
    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    entry = resp.json()[0]
    assert entry["target"]["package_name"] == "com.zionking.ziona"
    assert entry["target"]["sha256_cert_fingerprints"] == [
        "B6:A8:22:F3:C7:E0:71:56:6B:24:93:C4:57:6A:85:D9:81:01:65:3D:BD:CB:70:D2:0E:34:23:4B:5D:45:6B:52",
        "53:5B:CE:7A:2F:80:80:F4:2C:66:77:6E:9E:C7:E9:15:72:79:D5:52:73:1A:58:B1:81:6A:B7:26:23:1C:72:68",
    ]
    assert entry["relation"] == [
        "delegate_permission/common.handle_all_urls",
        "delegate_permission/common.get_login_creds",
    ]


@pytest.mark.django_db
def test_apple_app_site_association_builds_appid_from_team_id(client, settings):
    settings.APPLE_TEAM_ID = "ABCDE12345"
    settings.APPLE_BUNDLE_ID = "com.zionking.ziona"

    resp = client.get("/.well-known/apple-app-site-association")

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    detail = resp.json()["applinks"]["details"][0]
    # Modern AASA shape (iOS 13+): appIDs/components, not appID/paths.
    assert detail["appIDs"] == ["ABCDE12345.com.zionking.ziona"]
    assert [c["/"] for c in detail["components"] if "/" in c] == [
        "/post/*",
        "/profile/*",
        "/viewer/*",
    ]


@pytest.mark.django_db
def test_apple_app_site_association_excludes_opt_out_fragment(client):
    """The exclude rule must come first — order decides which rule wins."""
    resp = client.get("/.well-known/apple-app-site-association")

    components = resp.json()["applinks"]["details"][0]["components"]
    assert components[0] == {
        "#": "no_universal_links",
        "exclude": True,
        "comment": "Matches any URL whose fragment begins with no_universal_links",
    }


@pytest.mark.django_db
def test_apple_appid_tracks_the_configured_bundle_id(client, settings):
    """Staging must be able to serve its own bundle.

    The appID used to be read from APPLE_DEFAULT_CLIENT_IDS — a hardcoded list
    that doubles as the Sign-in-with-Apple audience allowlist — so staging served
    the production bundle and its build could never verify a Universal Link.
    """
    settings.APPLE_TEAM_ID = "RLL2NX9J5Z"
    settings.APPLE_BUNDLE_ID = "com.zionking.ziona.staging"

    resp = client.get("/.well-known/apple-app-site-association")

    detail = resp.json()["applinks"]["details"][0]
    assert detail["appIDs"] == ["RLL2NX9J5Z.com.zionking.ziona.staging"]


@pytest.mark.django_db
def test_apple_appid_falls_back_to_placeholder_when_team_id_unset(client, settings):
    settings.APPLE_TEAM_ID = ""
    settings.APPLE_BUNDLE_ID = "com.zionking.ziona"

    resp = client.get("/.well-known/apple-app-site-association")

    assert resp.json()["applinks"]["details"][0]["appIDs"] == ["TEAMID.com.zionking.ziona"]


@pytest.mark.django_db
def test_share_preview_includes_store_fallback_and_deep_link(client, settings):
    settings.IOS_APP_STORE_URL = "https://apps.apple.com/app/id123456789"
    settings.ANDROID_PLAY_STORE_URL = (
        "https://play.google.com/store/apps/details?id=com.zionking.ziona"
    )
    settings.APP_SHARE_BASE_URL = "https://ziona.app"

    user = User.objects.create_user(
        email="sharer@example.com",
        username="sharer",
        password="Pass123!",  # pragma: allowlist secret
    )
    post = Post.objects.create(user=user, post_type="text", caption="hi there")

    resp = client.get(f"/post/{post.id}/")

    assert resp.status_code == 200
    body = resp.content.decode()
    # Store fallbacks present…
    assert "https://apps.apple.com/app/id123456789" in body
    assert "play.google.com/store/apps/details?id=com.zionking.ziona" in body
    # …and the primary CTA points at the post deep link (not a bare root URL).
    assert f"https://ziona.app/post/{post.id}" in body


@pytest.mark.django_db
def test_profile_share_preview_includes_store_fallback_and_deep_link(client, settings):
    settings.IOS_APP_STORE_URL = "https://apps.apple.com/app/id123456789"
    settings.ANDROID_PLAY_STORE_URL = (
        "https://play.google.com/store/apps/details?id=com.zionking.ziona"
    )
    settings.APP_SHARE_BASE_URL = "https://ziona.app"

    user = User.objects.create_user(
        email="profile-share@example.com",
        username="profileshare",
        password="Pass123!",  # pragma: allowlist secret
        full_name="Profile Share",
        bio="Sharing faith stories.",
    )

    resp = client.get(f"/profile/{user.id}/")

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "https://apps.apple.com/app/id123456789" in body
    assert "play.google.com/store/apps/details?id=com.zionking.ziona" in body
    assert f"https://ziona.app/profile/{user.id}" in body
    assert "Sharing faith stories." in body


def test_share_base_url_defaults_to_the_serving_host_not_a_redirecting_one(settings):
    """Deep links must target the host that serves the site.

    `ziona.app` permanently 308s to `www.ziona.app`; Apple/Google refuse to verify
    a deep-link domain whose .well-known files redirect, and will not open the app
    through a redirect. Guards against reverting the default to the bare apex.
    """
    assert settings.APP_SHARE_BASE_URL == "https://www.ziona.app"
