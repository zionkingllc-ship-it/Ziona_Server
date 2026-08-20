import json

from django.core.management import call_command

from core.emails.templates import (
    render_admin_announcement,
    render_moderation_notice,
    render_notification_digest,
    render_reset_password,
    render_support_donation,
    render_verify_email,
    render_welcome_email,
)


def _assert_shipped_email_html(html: str) -> None:
    assert "api.builder.io" not in html
    assert 'href="#"' not in html
    assert "var(--" not in html
    assert "{{" not in html
    assert "{%" not in html
    assert "<style" not in html.lower()
    assert "external stylesheet" not in html.lower()
    if "<img" in html:
        assert "display:block" in html
    assert "Mona Sans" in html


def _assert_card_width(html: str, width: int) -> None:
    assert f'width="{width}"' in html
    assert f"width:{width}px" in html


def test_verify_email_template_renders_html_and_plain(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_VERIFY_URL = "https://ziona.app/verify-email"

    subject, plain, html = render_verify_email("Brian", "627702")

    assert "Verify" in subject
    assert "627702" in plain
    assert "627702" in html
    assert "Hi Brian" in html
    assert "Verify your email" in html
    assert "https://cdn.example.com/email/assets/brand-logo.png" in html
    assert "https://cdn.example.com/email/assets/social-linkedin.png" in html
    assert "https://ziona.app/verify-email" in html
    assert "This code expires in 30 minutes" in plain
    assert "line-height:24px" in html
    _assert_card_width(html, 393)
    _assert_shipped_email_html(html)


def test_reset_password_template_renders_without_temporary_builder_assets(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_PASSWORD_RESET_URL = "https://ziona.app/reset-password"

    _, plain, html = render_reset_password("Sarah", "112233")

    assert "112233" in plain
    assert "112233" in html
    assert "Reset your password with this link" in plain
    assert "https://cdn.example.com/email/assets/brand-logo.png" in html
    assert "https://ziona.app/reset-password" in html
    assert "This code expires in 30 minutes" in html
    _assert_card_width(html, 480)
    _assert_shipped_email_html(html)


def test_welcome_template_renders(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_APP_BASE_URL = "https://ziona.app"

    _, plain, html = render_welcome_email("Grace")

    assert "Welcome" in plain
    assert "Hi Grace" in html
    assert "Make a post" in html
    assert "Find other creators" in html
    assert "Join a circle" in html
    assert "welcome-hero.png" not in html
    assert "https://ziona.app" in html
    _assert_card_width(html, 393)
    _assert_shipped_email_html(html)


def test_notification_digest_template_renders_three_items(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    _, plain, html = render_notification_digest(
        "Mike",
        [
            {"actor_name": "Sarah", "content": "followed you", "timestamp": "3 hrs ago"},
            {"title": "Circle", "description": "New anchor posted", "time": "Now"},
            {"actor_name": "Josh", "content": "mentioned you", "timestamp": "1 hr ago"},
            {"actor_name": "Hidden", "content": "fourth item", "timestamp": "later"},
        ],
    )

    assert "Sarah" in plain
    assert "Hi Mike" in html
    assert "Stay up to date" in html
    assert "New anchor posted" in html
    assert "fourth item" not in html
    assert "S" in html
    _assert_card_width(html, 393)
    _assert_shipped_email_html(html)


def test_admin_announcement_template_renders(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    _, plain, html = render_admin_announcement(
        user_name="Love",
        heading="Daily Anchor Update",
        body="A new anchor is available.",
        circle_name="Faith, Work & Purpose",
        published_at="May 26, 2026",
    )

    assert "Daily Anchor Update" in plain
    assert "Faith, Work &amp; Purpose" in html
    assert "announcement-hero.png" not in html
    assert "Open Ziona" in html
    _assert_card_width(html, 600)
    _assert_shipped_email_html(html)


def test_support_donation_template_renders(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    _, plain, html = render_support_donation("David", "5.00", "May 26, 2026")

    assert "5.00" in plain
    assert "part of something beautiful" in html
    assert "Thank you for your support!" in html
    assert "success-illustration.png" not in html
    assert "https://cdn.example.com/email/assets/support-hero.png" in html
    _assert_card_width(html, 480)
    _assert_shipped_email_html(html)


def test_moderation_notice_warn_renders_branded_html_with_reason(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    subject, plain, html = render_moderation_notice(
        user_name="Grace",
        action_type="warned",
        reason="Repeated off-topic posts",
    )

    assert subject == "Community Warning"
    assert "Repeated off-topic posts" in plain
    assert "Repeated off-topic posts" in html
    assert "Account Warning" in html
    assert "https://cdn.example.com/email/assets/brand-logo.png" in html
    assert "<!DOCTYPE html>" in html
    _assert_card_width(html, 393)
    _assert_shipped_email_html(html)


def test_moderation_notice_covers_suspend_and_reactivate(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    for action in ("suspended", "reactivated"):
        subject, plain, html = render_moderation_notice(
            user_name="Grace", action_type=action, reason="policy"
        )
        assert subject
        assert "<!DOCTYPE html>" in html
        assert plain.strip()
        _assert_card_width(html, 393)
        _assert_shipped_email_html(html)


def test_email_asset_manifest_lists_required_assets():
    with open("templates/emails/asset-manifest.json", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    assets = manifest["assets"]
    assert assets["brandLogo"]["outputPath"] == "assets/brand-logo.png"
    assert assets["linkedinIcon"]["outputPath"] == "assets/social-linkedin.png"
    assert assets["instagramIcon"]["outputPath"] == "assets/social-instagram.png"
    assert assets["tiktokIcon"]["outputPath"] == "assets/social-tiktok.png"
    assert assets["facebookIcon"]["outputPath"] == "assets/social-facebook.png"
    assert assets["supportHero"]["outputPath"] == "assets/support-hero.png"
    assert "supportHero" in manifest["templates"]["support_donation.html"]


def test_render_email_previews_command_outputs_all_templates(settings, tmp_path):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_APP_BASE_URL = "https://ziona.app"
    settings.EMAIL_VERIFY_URL = "https://ziona.app/verify-email"
    settings.EMAIL_PASSWORD_RESET_URL = "https://ziona.app/reset-password"
    settings.EMAIL_UNSUBSCRIBE_URL = "https://ziona.app/unsubscribe"

    call_command("render_email_previews", output_dir=str(tmp_path))

    expected = {
        "verify_email.html",
        "password_reset.html",
        "welcome.html",
        "notification_digest.html",
        "admin_announcement.html",
        "support_donation.html",
        "warning.html",
        "suspension.html",
        "reactivation.html",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected
    for path in tmp_path.iterdir():
        _assert_shipped_email_html(path.read_text(encoding="utf-8"))
