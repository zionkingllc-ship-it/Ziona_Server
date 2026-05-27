from core.emails.templates import (
    render_admin_announcement,
    render_notification_digest,
    render_reset_password,
    render_support_donation,
    render_verify_email,
    render_welcome_email,
)


def test_verify_email_template_renders_html_and_plain(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_VERIFY_URL = "https://ziona.app/verify-email"

    subject, plain, html = render_verify_email("Brian", "627702")

    assert "Verify" in subject
    assert "627702" in plain
    assert "627702" in html
    assert "Hi Brian" in html
    assert "https://cdn.example.com/email/assets/brand-logo.png" in html
    assert "https://ziona.app/verify-email" in html


def test_reset_password_template_renders_without_temporary_builder_assets(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_PASSWORD_RESET_URL = "https://ziona.app/reset-password"

    _, plain, html = render_reset_password("Sarah", "112233")

    assert "112233" in plain
    assert "112233" in html
    assert "https://cdn.example.com/email/assets/brand-logo.png" in html
    assert "api.builder.io" not in html


def test_welcome_template_renders(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"
    settings.EMAIL_APP_BASE_URL = "https://ziona.app"

    _, plain, html = render_welcome_email("Grace")

    assert "Welcome" in plain
    assert "Hi Grace" in html
    assert "welcome-hero.png" in html


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
    assert "New anchor posted" in html
    assert "fourth item" not in html


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
    assert "announcement-hero.png" in html


def test_support_donation_template_renders(settings):
    settings.EMAIL_ASSET_BASE_URL = "https://cdn.example.com/email"

    _, plain, html = render_support_donation("David", "$25.00", "May 26, 2026")

    assert "$25.00" in plain
    assert "Thank you for your support, David" in html
    assert "success-illustration.png" in html
