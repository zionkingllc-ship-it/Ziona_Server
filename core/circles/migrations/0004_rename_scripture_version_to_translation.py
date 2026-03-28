# Generated manually — Rename scripture_version to scripture_translation on Anchor

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("circles", "0003_anchorresponse_anchorresponsereaction_circlereport_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="anchor",
            old_name="scripture_version",
            new_name="scripture_translation",
        ),
    ]
