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
  <title>{b['name']}</title>
</head>
<body style="margin:0;padding:0;background:#0F0F0F;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0F0F0F;">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#1A1A2E;border-radius:16px;overflow:hidden;max-width:600px;">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,{b['primary']},{b['accent']});
                     padding:32px;text-align:center;">
            <h1 style="margin:0;color:#FFFFFF;font-size:28px;letter-spacing:4px;
                       font-weight:900;">{b['logo_text']}</h1>
            <p style="margin:8px 0 0;color:rgba(255,255,255,0.8);font-size:13px;">
              {b['tagline']}
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
              &copy; 2025 {b['name']}. All rights reserved.
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
    name = user_name or "Friend"
    b = _brand(brand)
    subject = f"Verify your {b['name']} account"
    plain = (
        f"Hi {name},\n\n"
        f"Your verification code is: {otp_code}\n"
        f"This code expires in {expiry_minutes} minutes.\n\n"
        f"The {b['name']} Team"
    )
    inner = (
        _h2(f"Welcome to {b['name']}, {name}! 🙏")
        + _p("You're one step away. Enter the code below to verify your email address.")
        + _otp_box(otp_code)
        + _expiry_note(expiry_minutes)
        + _p("If you didn't create an account, you can safely ignore this email.")
    )
    html = _wrap_layout(brand, inner)
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
    name = user_name or "Friend"
    b = _brand(brand)
    subject = f"Reset your {b['name']} password"
    plain = (
        f"Hi {name},\n\n"
        f"Your password reset code is: {otp_code}\n"
        f"This code expires in {expiry_minutes} minutes.\n\n"
        f"If you didn't request this, please ignore.\n\n"
        f"The {b['name']} Team"
    )
    inner = (
        _h2("Password Reset Request 🔐")
        + _p(
            f"Hi <strong style='color:#FFFFFF;'>{name}</strong>, we received a request to reset your password."
        )
        + _otp_box(otp_code)
        + _expiry_note(expiry_minutes)
        + _p(
            "If you didn't request a password reset, you can safely ignore this email — "
            "your password has not been changed."
        )
    )
    html = _wrap_layout(brand, inner)
    return subject, plain, html


# ─────────────────────────────────────────────────────────────
# Template 3: welcome_email
# ─────────────────────────────────────────────────────────────


def render_welcome_email(
    user_name: str | None,
    brand: str = "ZIONA",
) -> tuple[str, str, str]:
    """Render post-verification welcome template."""
    name = user_name or "Friend"
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
    inner = (
        _h2(f"You're in, {name}! 🎉")
        + _p(
            f"Your account is active. Here's how to get started on <strong style='color:#F59E0B;'>{b['name']}</strong>:"
        )
        + """<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;">
  <tr>
    <td style="padding:12px;background:rgba(107,33,168,0.15);border-radius:8px;
               border-left:3px solid #6B21A8;margin-bottom:12px;">
      <p style="margin:0;color:#FFFFFF;font-size:14px;">
        <strong>✍️ Make a post</strong> — Share your faith journey with the community
      </p>
    </td>
  </tr>
  <tr><td style="height:8px;"></td></tr>
  <tr>
    <td style="padding:12px;background:rgba(107,33,168,0.15);border-radius:8px;
               border-left:3px solid #F59E0B;">
      <p style="margin:0;color:#FFFFFF;font-size:14px;">
        <strong>🔍 Find creators</strong> — Follow believers who inspire you
      </p>
    </td>
  </tr>
  <tr><td style="height:8px;"></td></tr>
  <tr>
    <td style="padding:12px;background:rgba(107,33,168,0.15);border-radius:8px;
               border-left:3px solid #6B21A8;">
      <p style="margin:0;color:#FFFFFF;font-size:14px;">
        <strong>⭕ Join a Circle</strong> — Connect in faith-based communities
      </p>
    </td>
  </tr>
</table>"""
        + _cta_button("Open the App")
        + _p(
            '<em style="color:rgba(255,255,255,0.5);">God bless you on your journey. — The Ziona Team</em>'
        )
    )
    html = _wrap_layout(brand, inner)
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
    name = user_name or "Friend"
    safe_activities = activities or []
    b = _brand(brand)
    subject = f"Your daily update from {b['name']} 📬"

    # Plain text
    lines = [f"Hi {name},\n\nHere's what happened while you were away:\n"]
    for act in safe_activities:
        lines.append(f"  • {act.get('actor_name', 'Someone')}: {act.get('content', '')}")
    lines.append(f"\nOpen the app to see more.\n\nThe {b['name']} Team")
    plain = "\n".join(lines)

    # Activity rows HTML
    activity_rows = ""
    for act in safe_activities:
        actor = act.get("actor_name") or "Someone"
        content = act.get("content") or ""
        ts = act.get("timestamp") or ""
        avatar = act.get("actor_avatar") or ""
        avatar_html = (
            f'<img src="{avatar}" width="36" height="36" '
            f'style="border-radius:50%;object-fit:cover;" alt="{actor}"/>'
            if avatar
            else f'<div style="width:36px;height:36px;border-radius:50%;background:#6B21A8;'
            f"display:flex;align-items:center;justify-content:center;color:#FFF;"
            f'font-weight:700;font-size:14px;">{actor[:1].upper()}</div>'
        )
        activity_rows += f"""
<tr>
  <td style="padding:12px 0;border-bottom:1px solid rgba(255,255,255,0.06);">
    <table cellpadding="0" cellspacing="0" width="100%">
      <tr>
        <td width="44" valign="top">{avatar_html}</td>
        <td style="padding-left:12px;">
          <p style="margin:0;color:#FFFFFF;font-size:14px;">
            <strong>{actor}</strong> {content}
          </p>
          <p style="margin:4px 0 0;color:rgba(255,255,255,0.4);font-size:12px;">{ts}</p>
        </td>
      </tr>
    </table>
  </td>
</tr>"""

    inner = (
        _h2(f"Your daily update, {name} 📬")
        + _p("Here's what happened in your community today:")
        + f'<table width="100%" cellpadding="0" cellspacing="0">{activity_rows}</table>'
        + _cta_button("See All Notifications")
        + _p(
            '<em style="color:rgba(255,255,255,0.5);font-size:12px;">You can manage digest '
            "preferences in your account settings.</em>"
        )
    )
    html = _wrap_layout(brand, inner)
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
    <td style="color:#FFFFFF;font-size:14px;font-weight:600;">{b['name']}</td>
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
