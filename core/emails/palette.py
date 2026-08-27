"""Canonical colors used by Ziona's production email templates."""

from types import MappingProxyType

EMAIL_COLORS = MappingProxyType(
    {
        "primary": "#742092",
        "primary_accent": "#9629BC",
        "brand_inverse": "#F6EAFA",
        "text_primary": "#181419",
        "text_secondary": "#4E4252",
        "text_tertiary": "#836F8B",
        "background": "#F5F2F8",
        "surface": "#FFFFFF",
        "surface_secondary": "#FAF9FA",
        "border": "#9C8BA2",
        "footer_text": "#484848",
    }
)

# These are the exact solid composites of the React template's translucent
# colors over the white email card. Solid colors render consistently in Outlook.
SUPPORT_DONATION_COLORS = MappingProxyType(
    {
        "highlight_background": "#F9F0FC",
        "divider": "#CEC5D1",
    }
)
