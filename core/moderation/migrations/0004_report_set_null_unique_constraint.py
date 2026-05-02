# Generated migration for Ziona audit fixes:
# - Issue #2: Change post/comment FKs from CASCADE to SET_NULL (audit trail preservation)
# - Issue #7: Add UniqueConstraint to prevent duplicate reports

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("engagement", "0002_remove_bookmarkfolder_icon"),
        ("moderation", "0003_remove_report_ck_report_has_target_report_action_and_more"),
        ("posts", "0004_rename_scripture_version_to_translation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Issue #2: Preserve audit trail — reports must survive post/comment deletion
        migrations.AlterField(
            model_name="report",
            name="post",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports",
                to="posts.post",
            ),
        ),
        migrations.AlterField(
            model_name="report",
            name="comment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports",
                to="engagement.comment",
            ),
        ),
        # Issue #7: Prevent duplicate spam reports at the database level
        migrations.AddConstraint(
            model_name="report",
            constraint=models.UniqueConstraint(
                fields=["reporter", "target_type", "target_id", "reason"],
                name="unique_user_report",
            ),
        ),
    ]
