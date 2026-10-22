"""Trim stray whitespace from existing display names.

Names were saved exactly as typed, so some carry leading or trailing spaces
("Chinenye Jessica Kanu "). New saves are trimmed at write time; this cleans the
names already stored, so the edit-profile form and admin views match what the
API now shows as displayName. Only rows that actually change are written.
"""

from django.db import migrations
from django.db.models import Q

BATCH_SIZE = 500


def trim_full_names(apps, schema_editor):
    User = apps.get_model("users", "User")
    # Whitespace at either end; a cheap prefilter before the exact Python check.
    candidates = User._base_manager.filter(
        Q(full_name__regex=r"^\s") | Q(full_name__regex=r"\s$")
    ).only("id", "full_name")
    batch = []
    for user in candidates.iterator(chunk_size=BATCH_SIZE):
        trimmed = user.full_name.strip()
        if trimmed != user.full_name:
            user.full_name = trimmed
            batch.append(user)
        if len(batch) >= BATCH_SIZE:
            User._base_manager.bulk_update(batch, ["full_name"])
            batch = []
    if batch:
        User._base_manager.bulk_update(batch, ["full_name"])


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_user_daily_activity"),
    ]

    # The original whitespace carried no meaning, so there is nothing to restore.
    operations = [migrations.RunPython(trim_full_names, migrations.RunPython.noop)]
