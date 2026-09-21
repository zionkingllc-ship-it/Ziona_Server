"""Canonicalise CamelCase ``reference_type`` values written by older code.

Three writers stored model names rather than the lowercase snake_case
vocabulary the rest of the system uses: ``"Anchor"``, ``"User"`` and
``"ContactMessage"``. Both consumers are case-sensitive, so those rows were
broken in two visible ways:

- the mobile route map missed them (JS object lookups are case-sensitive), so
  tapping the notification did nothing;
- ``CIRCLE_REFERENCE_TYPES`` missed ``"Anchor"``, so anchor notifications never
  appeared under the Circles filter.

Data-only — no schema change. New rows are normalised at write time in
``create_notification``.
"""

from django.db import migrations

RENAMES = (
    ("Anchor", "anchor"),
    ("User", "user"),
    ("ContactMessage", "contact_message"),
)


def normalize(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    for old, new in RENAMES:
        Notification.objects.filter(reference_type=old).update(reference_type=new)


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0008_notification_muted_user"),
    ]

    # Deliberately not reversible. Re-capitalising would also hit rows that were
    # always lowercase — core.follows writes reference_type="user" directly —
    # and would corrupt data this migration never touched. Unapplying is a no-op.
    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
