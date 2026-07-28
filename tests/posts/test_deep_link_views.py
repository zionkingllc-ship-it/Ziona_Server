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
def test_apple_app_site_association_builds_appid_from_team_id(client, settings):
    settings.APPLE_TEAM_ID = "ABCDE12345"
    settings.APPLE_DEFAULT_CLIENT_IDS = ["com.zionking.ziona"]

    resp = client.get("/.well-known/apple-app-site-association")

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    detail = resp.json()["applinks"]["details"][0]
    assert detail["appID"] == "ABCDE12345.com.zionking.ziona"
    assert detail["paths"] == ["/post/*"]


@pytest.mark.django_db
def test_apple_appid_falls_back_to_placeholder_when_team_id_unset(client, settings):
    settings.APPLE_TEAM_ID = ""
    settings.APPLE_DEFAULT_CLIENT_IDS = ["com.zionking.ziona"]

    resp = client.get("/.well-known/apple-app-site-association")

    assert resp.json()["applinks"]["details"][0]["appID"] == "TEAMID.com.zionking.ziona"


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
