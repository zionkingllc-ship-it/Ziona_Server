from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0006_add_support_reply_notification_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("reply_comment", "Reply to Comment"),
                    ("reply_post", "Reply to Post"),
                    ("like_post", "Like Post"),
                    ("like_comment", "Like Comment"),
                    ("new_anchor", "New Anchor"),
                    ("mention", "Mention"),
                    ("new_circle_post", "New Circle Post"),
                    ("new_follower", "New Follower"),
                    ("support_reply", "Support Reply"),
                    ("admin_announcement", "Admin Announcement"),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="notificationmetrics",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("reply_comment", "Reply to Comment"),
                    ("reply_post", "Reply to Post"),
                    ("like_post", "Like Post"),
                    ("like_comment", "Like Comment"),
                    ("new_anchor", "New Anchor"),
                    ("mention", "Mention"),
                    ("new_circle_post", "New Circle Post"),
                    ("new_follower", "New Follower"),
                    ("support_reply", "Support Reply"),
                    ("admin_announcement", "Admin Announcement"),
                ],
                max_length=50,
            ),
        ),
    ]
