"""
Email HTML templates for the Ziona platform.

All render_* functions return a 3-tuple: (subject, plain_text, html_body).
Each function is None/empty-safe:
  - user_name falls back to "Friend" when falsy
  - activities defaults to [] when None

Design: purple (#6B21A8) / gold (#F59E0B) Ziona branding, inline CSS only
        for maximum Ensend / email-client compatibility.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

# ─────────────────────────────────────────────────────────────
# Brand configuration
# ─────────────────────────────────────────────────────────────

_BRAND_CONFIG: dict[str, dict] = {
    "ZIONA": {
        "name": "Ziona",
        "from_email": "noreply@ziona.app",
        "primary": "#6B21A8",
        "accent": "#F59E0B",
        "logo_text": "ZIONA",
        "tagline": "Faith. Community. Connection.",
    },
    "ZIONKING": {
        "name": "Zion King",
        "from_email": "noreply@zionking.org",
        "primary": "#1E3A5F",
        "accent": "#F59E0B",
        "logo_text": "ZION KING",
        "tagline": "Advancing the Kingdom.",
    },
}


def _brand(brand_key: str) -> dict:
    """Return brand config, defaulting to ZIONA for unknown keys."""
    return _BRAND_CONFIG.get(brand_key.upper(), _BRAND_CONFIG["ZIONA"])


def _display_name(user_name: str | None) -> str:
    return (user_name or "").strip() or "Friend"


def _base_context(**overrides) -> dict:
    context = {
        "asset_base_url": settings.EMAIL_ASSET_BASE_URL.rstrip("/"),
        "app_link": settings.EMAIL_APP_BASE_URL,
        "verify_link": settings.EMAIL_VERIFY_URL,
        "reset_link": settings.EMAIL_PASSWORD_RESET_URL,
        "notification_link": settings.EMAIL_APP_BASE_URL,
        "unsubscribe_link": settings.EMAIL_UNSUBSCRIBE_URL,
        "year": timezone.now().year,
    }
    context.update(overrides)
    return context


def _email_date() -> str:
    return timezone.now().strftime("%b %d, %Y").replace(" 0", " ")


# ─────────────────────────────────────────────────────────────
# Shared layout helpers
# ─────────────────────────────────────────────────────────────


def _wrap_layout(brand_key: str, inner_html: str) -> str:
    """Wrap inner HTML in the shared branded email layout."""
    b = _brand(brand_key)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{b["name"]}</title>
</head>
<body style="margin:0;padding:0;background:#0F0F0F;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0F0F0F;">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#1A1A2E;border-radius:16px;overflow:hidden;max-width:600px;">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,{b["primary"]},{b["accent"]});
                     padding:32px;text-align:center;">
            <h1 style="margin:0;color:#FFFFFF;font-size:28px;letter-spacing:4px;
                       font-weight:900;">{b["logo_text"]}</h1>
            <p style="margin:8px 0 0;color:rgba(255,255,255,0.8);font-size:13px;">
              {b["tagline"]}
            </p>
          </td>
        </tr>

        <!-- Content -->
        <tr>
          <td style="padding:40px 40px 32px;">
            {inner_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:24px 40px;border-top:1px solid rgba(255,255,255,0.08);
                     text-align:center;">
            <p style="margin:0;color:rgba(255,255,255,0.4);font-size:12px;">
              &copy; 2025 {b["name"]}. All rights reserved.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _h2(text: str) -> str:
    return f'<h2 style="margin:0 0 16px;color:#FFFFFF;font-size:22px;">{text}</h2>'


def _p(text: str) -> str:
    return f'<p style="margin:0 0 16px;color:rgba(255,255,255,0.75);font-size:15px;line-height:1.6;">{text}</p>'


def _otp_box(code: str) -> str:
    return f"""<div style="background:rgba(107,33,168,0.2);border:1px solid rgba(107,33,168,0.5);
                           border-radius:12px;padding:24px;text-align:center;margin:24px 0;">
  <p style="margin:0 0 8px;color:rgba(255,255,255,0.6);font-size:13px;letter-spacing:2px;
             text-transform:uppercase;">Verification Code</p>
  <p style="margin:0;color:#F59E0B;font-size:40px;font-weight:900;letter-spacing:10px;
             font-family:monospace;">{code}</p>
</div>"""


def _cta_button(label: str, href: str = "#", color: str = "#6B21A8") -> str:
    return f"""<div style="text-align:center;margin:24px 0;">
  <a href="{href}" style="display:inline-block;background:{color};color:#FFFFFF;
     text-decoration:none;padding:14px 36px;border-radius:8px;font-weight:700;
     font-size:15px;letter-spacing:0.5px;">{label}</a>
</div>"""


def _expiry_note(minutes: int) -> str:
    return _p(
        f'<em>This code expires in <strong style="color:#F59E0B;">{minutes} minutes</strong>. '
        f"Do not share it with anyone.</em>"
    )


# ─────────────────────────────────────────────────────────────
# Template 1: verify_email
# ─────────────────────────────────────────────────────────────


def render_verify_email(
    user_name: str | None,
    otp_code: str,
    expiry_minutes: int = 30,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render email verification template."""
    name = _display_name(user_name)
    b = _brand(brand)
    subject = "Verify your " + b["name"] + " account"
    context = _base_context(
        username=name,
        verification_code=otp_code,
        expiry_minutes=expiry_minutes,
    )
    plain = render_to_string("emails/text/email_verification.txt", context)
    html = render_to_string("emails/email_verification.html", context)
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 2: reset_password
# ─────────────────────────────────────────────────────────────


def render_reset_password(
    user_name: str | None,
    otp_code: str,
    expiry_minutes: int = 30,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render password reset OTP template."""
    name = _display_name(user_name)
    b = _brand(brand)
    subject = "Reset your " + b["name"] + " password"
    context = _base_context(
        username=name,
        reset_code=otp_code,
        expiry_minutes=expiry_minutes,
    )
    plain = render_to_string("emails/text/password_reset.txt", context)
    html = render_to_string("emails/password_reset.html", context)
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 3: welcome_email
# ─────────────────────────────────────────────────────────────


def render_welcome_email(
    user_name: str | None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render post-verification welcome template."""
    name = _display_name(user_name)
    b = _brand(brand)
    subject = f"Welcome to {b['name']} 🙏"
    plain = (
        f"Hi {name},\n\n"
        f"Welcome to {b['name']}! Your account is now active.\n\n"
        f"Get started:\n"
        f"  • Make your first post\n"
        f"  • Find creators to follow\n"
        f"  • Join a Circle\n\n"
        f"The {b['name']} Team"
    )
    html = render_to_string(
        "emails/welcome.html",
        _base_context(username=name),
    )
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 4: notification_digest
# ─────────────────────────────────────────────────────────────


def render_notification_digest(
    user_name: str | None,
    activities: list[dict] | None = None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render daily notification digest template.

    activities items: {type, actor_name, content, timestamp}
    Returns empty tuple ('','','') if no activities — caller must guard.
    """
    name = _display_name(user_name)
    safe_activities = activities or []
    b = _brand(brand)
    subject = f"Your daily update from {b['name']} 📬"

    lines = [f"Hi {name},\n\nHere's what happened while you were away:\n"]
    for act in safe_activities:
        lines.append(f"  • {act.get('actor_name', 'Someone')}: {act.get('content', '')}")
    lines.append(f"\nOpen the app to see more.\n\nThe {b['name']} Team")
    plain = "\n".join(lines)

    notification_items = []
    for index, act in enumerate(safe_activities[:3], start=1):
        actor = act.get("actor_name") or "Someone"
        content = act.get("content") or ""
        notification_items.append(
            {
                "title": act.get("title") or f"{actor} updated you",
                "description": act.get("description") or content,
                "time": act.get("timestamp") or act.get("time") or f"Item {index}",
            }
        )

    html = render_to_string(
        "emails/notification_digest.html",
        _base_context(username=name, notification_items=notification_items),
    )
    return subject, plain, html


def render_admin_announcement(
    user_name: str | None,
    heading: str,
    body: str,
    circle_name: str = "Ziona",
    published_at: str | None = None,
    cta_label: str = "Open Ziona",
    cta_link: str | None = None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render an admin announcement email."""
    name = _display_name(user_name)
    b = _brand(brand)
    published = published_at or _email_date()
    subject = f"{b['name']} Announcement: {heading}"
    plain = (
        f"Hello {name},\n\n"
        f"{heading}\n\n"
        f"{body}\n\n"
        f"Circle: {circle_name}\n"
        f"Published: {published}\n\n"
        f"The {b['name']} Team"
    )
    html = render_to_string(
        "emails/admin_announcement.html",
        _base_context(
            username=name,
            announcement_heading=heading,
            announcement_body=body,
            circle_name=circle_name,
            announcement_published_at=published,
            announcement_cta_label=cta_label,
            announcement_cta_link=cta_link or settings.EMAIL_APP_BASE_URL,
        ),
    )
    return subject, plain, html


def render_support_donation(
    user_name: str | None,
    support_amount: str | Decimal,
    support_date: str | None = None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render donation/support confirmation email."""
    name = _display_name(user_name)
    b = _brand(brand)
    amount = str(support_amount)
    received_on = support_date or _email_date()
    subject = "Thank you for your donation!"
    plain = (
        f"Hi {name},\n\n"
        f"Thank you for your support. Your contribution of {amount} was received "
        f"on {received_on}.\n\n"
        f"We are grateful to have you on this journey with us.\n\n"
        f"The {b['name']} Team"
    )
    html = render_to_string(
        "emails/support_donation.html",
        _base_context(
            username=name,
            support_amount=amount,
            support_date=received_on,
        ),
    )
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 5: waitlist_confirmation
# ─────────────────────────────────────────────────────────────


def render_waitlist_confirmation(
    email: str,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render waitlist confirmation template."""
    b = _brand(brand)
    if brand.upper() == "ZIONKING":
        subject = "You're on the Zion King waitlist!"
    else:
        subject = "You're on the Ziona waitlist! 🎉"

    plain = (
        f"Hi there,\n\n"
        f"You're on the {b['name']} waitlist! We'll notify you at {email} when we launch.\n\n"
        f"The {b['name']} Team"
    )
    inner = (
        _h2("You're on the list! 🎉")
        + _p(
            f"Thanks for your interest in <strong style='color:#F59E0B;'>{b['name']}</strong>. "
            f"You're officially on the waitlist."
        )
        + f"""<div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);
                          border-radius:12px;padding:20px;text-align:center;margin:24px 0;">
  <p style="margin:0;color:#F59E0B;font-size:18px;font-weight:700;">🙏 We'll be in touch!</p>
  <p style="margin:8px 0 0;color:rgba(255,255,255,0.7);font-size:14px;">
    We'll send a launch notification to <strong>{email}</strong>
  </p>
</div>"""
        + _p("Stay tuned. Something great is coming.")
    )
    html = _wrap_layout(brand, inner)
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 6: contact_auto_reply
# ─────────────────────────────────────────────────────────────


def render_contact_auto_reply(
    name: str | None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render auto-reply sent to contact form submitter."""
    display = name or "Friend"
    b = _brand(brand)
    subject = f"We received your message — {b['name']} Support"
    plain = (
        f"Hi {display},\n\n"
        f"Thanks for reaching out to {b['name']}. We've received your message "
        f"and will get back to you within 2–3 business days.\n\n"
        f"The {b['name']} Team"
    )
    inner = (
        _h2(f"We got your message, {display}! ✅")
        + _p(
            f"Thank you for contacting <strong style='color:#F59E0B;'>{b['name']}</strong> support."
        )
        + _p(
            "Our team has received your message and will respond within "
            "<strong style='color:#FFFFFF;'>2–3 business days</strong>."
        )
        + _p("In the meantime, feel free to check our community for answers.")
    )
    html = _wrap_layout(brand, inner)
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 7: contact_internal_notification
# ─────────────────────────────────────────────────────────────


def render_contact_internal_notification(
    submitter_name: str | None,
    submitter_email: str,
    message: str,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render internal team notification email for a new contact submission."""
    display = submitter_name or "Anonymous"
    b = _brand(brand)
    subject = f"[{b['name']}] New contact from {display}"
    plain = (
        f"New contact submission ({b['name']})\n\n"
        f"From: {display} <{submitter_email}>\n\n"
        f"Message:\n{message}"
    )
    inner = (
        _h2("📩 New Contact Submission")
        + f"""<table width="100%" cellpadding="0" cellspacing="0"
               style="background:rgba(255,255,255,0.04);border-radius:8px;
                      padding:16px;margin-bottom:16px;">
  <tr>
    <td style="color:rgba(255,255,255,0.5);font-size:13px;padding:4px 0;width:80px;">Brand</td>
    <td style="color:#FFFFFF;font-size:14px;font-weight:600;">{b["name"]}</td>
  </tr>
  <tr>
    <td style="color:rgba(255,255,255,0.5);font-size:13px;padding:4px 0;">Name</td>
    <td style="color:#FFFFFF;font-size:14px;">{display}</td>
  </tr>
  <tr>
    <td style="color:rgba(255,255,255,0.5);font-size:13px;padding:4px 0;">Email</td>
    <td style="color:#F59E0B;font-size:14px;">{submitter_email}</td>
  </tr>
</table>"""
        + _p("<strong style='color:#FFFFFF;'>Message:</strong>")
        + f'<div style="background:rgba(255,255,255,0.05);border-radius:8px;padding:16px;'
        f'color:rgba(255,255,255,0.85);font-size:14px;line-height:1.7;white-space:pre-wrap;">'
        f"{message}</div>"
    )
    html = _wrap_layout(brand, inner)
    return subject, plain, html
