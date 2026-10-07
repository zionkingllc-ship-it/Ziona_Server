"""Deep-link well-known files + share-preview store fallback (Ticket 12)."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone

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
        "ED:9D:BD:54:63:28:CC:7A:AE:44:F9:59:04:AA:67:FC:56:0C:76:2C:18:69:BA:15:3A:0E:3F:35:59:F8:39:30",
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
    # …the OG tags still carry the canonical https URL…
    assert f"https://ziona.app/post/{post.id}" in body
    # …but the primary CTA forces the app open via the custom scheme (an
    # https button just reloads a web page where App Links don't fire).
    assert f"ziona://viewer/{post.id}" in body


@pytest.mark.django_db
def test_share_preview_serves_slashless_url_without_redirect(client, settings):
    """Mobile shares /post/{id} (no trailing slash) — serve it with a 200."""
    settings.APP_SHARE_BASE_URL = "https://ziona.app"

    user = User.objects.create_user(
        email="noslash@example.com",
        username="noslash",
        password="Pass123!",  # pragma: allowlist secret
    )
    post = Post.objects.create(user=user, post_type="text", caption="no slash")

    resp = client.get(f"/post/{post.id}")

    assert resp.status_code == 200
    assert f"ziona://viewer/{post.id}" in resp.content.decode()


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

    Apple and Google refuse to verify a deep-link domain whose .well-known files
    30x-redirect, and will not open the app through a redirect. `ziona.app` used
    to 308 to `www.ziona.app`, so www was the target; the apex now serves
    directly and proxies the .well-known files plus /post/* and /profile/* to
    this backend.

    The value must equal the host declared in the Android intent filter and the
    AASA — App Links open only for the exact host that was verified — so this
    guards against it drifting back to a host the app does not claim.
    """
    assert settings.APP_SHARE_BASE_URL == "https://ziona.app"


class _PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.scripts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "id" in attrs:
            self.links[attrs["id"]] = attrs.get("href", "")
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data


@pytest.mark.django_db
@pytest.mark.parametrize("staging", [False, True], ids=["production", "staging"])
def test_rendered_share_preview_browser_behavior(client, settings, staging):
    """Execute the actual rendered JS, not a reimplementation of its logic.

    Node is test-only (CI installs it explicitly); no npm/browser dependencies.
    The harness checks page-load effects and link targets, not OS app launching.
    """
    settings.APP_DEEP_LINK_SCHEME = "zionastaging" if staging else "ziona"
    settings.ANDROID_APP_PACKAGE_NAME = "com.zionking.ziona" + (".staging" if staging else "")
    settings.APP_SHARE_BASE_URL = "https://staging.ziona.app" if staging else "https://ziona.app"
    settings.IOS_APP_STORE_URL = "https://apps.apple.com/app/id123456789"
    settings.ANDROID_PLAY_STORE_URL = (
        "https://play.google.com/store/apps/details?id="
        + settings.ANDROID_APP_PACKAGE_NAME
        + "&hl=en"
    )
    user = User.objects.create_user(email="browser@example.com", username="browser")
    caption = '<script>alert("caption")</script> & a post'
    post = Post.objects.create(user=user, post_type="text", caption=caption)
    response = client.get(f"/post/{post.id}")
    assert response.status_code == 200
    html = response.content.decode()
    assert '<meta property="og:url" content="' + settings.APP_SHARE_BASE_URL in html
    assert "&lt;script&gt;" in html
    parser = _PreviewParser()
    parser.feed(html)
    assert len(parser.scripts) == 1, "User content must not inject executable scripts"
    deep_link = f"{settings.APP_DEEP_LINK_SCHEME}://viewer/{post.id}"
    # Progressive enhancement: before any JS, the CTA and stores are real links.
    assert parser.links["open-app"] == deep_link
    assert parser.links["ios-store"] == settings.IOS_APP_STORE_URL
    assert parser.links["android-store"] == settings.ANDROID_PLAY_STORE_URL
    node = shutil.which("node")
    assert node, "Install Node.js 20+ to run share-preview behavior tests (no npm install needed)"
    result = subprocess.run(
        [node, str(Path(__file__).with_name("share_preview_harness.cjs"))],  # noqa: S603 - trusted harness, no shell
        input=json.dumps(
            {
                "script": parser.scripts[0],
                "links": parser.links,
                "deepLink": deep_link,
                "packageName": settings.ANDROID_APP_PACKAGE_NAME,
            }
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.django_db
@pytest.mark.parametrize("suffix", ["", "/"])
def test_share_preview_rejects_missing_invalid_and_deleted_posts(client, suffix):
    assert client.get(f"/post/not-a-uuid{suffix}").status_code == 404
    assert client.get(f"/post/{uuid4()}{suffix}").status_code == 404
    user = User.objects.create_user(email="deletedshare@example.com", username="deletedshare")
    post = Post.objects.create(
        user=user, post_type="text", caption="deleted", deleted_at=timezone.now()
    )
    assert client.get(f"/post/{post.id}{suffix}").status_code == 404


@pytest.mark.django_db
def test_profile_share_preview_serves_slashless_url_without_redirect(client):
    user = User.objects.create_user(email="profilelink@example.com", username="profilelink")
    response = client.get(f"/profile/{user.id}")
    assert response.status_code == 200
    assert "Location" not in response

@pytest.mark.parametrize("scheme", ["ziona", "zionastaging"])
def test_profile_open_button_uses_environment_scheme(client, settings, create_user, scheme):
    settings.APP_DEEP_LINK_SCHEME = scheme
    user = create_user()
    response = client.get(f"/profile/{user.id}")
    assert response.status_code == 200
    assert f'href="{scheme}://profile/{user.id}"' in response.content.decode()
