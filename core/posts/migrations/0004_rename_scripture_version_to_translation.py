# Generated manually — Rename scripture_version to scripture_translation

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("posts", "0003_alter_post_category"),
    ]

    operations = [
        migrations.RenameField(
            model_name="post",
            old_name="scripture_version",
            new_name="scripture_translation",
        ),
    ]
