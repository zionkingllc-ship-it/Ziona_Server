from django.db import migrations, models

NOTIFICATION_CHOICES = [
    ("reply_comment", "Reply to Comment"),
    ("reply_post", "Reply to Post"),
    ("like_post", "Like Post"),
    ("like_comment", "Like Comment"),
    ("new_anchor", "New Anchor"),
    ("mention", "Mention"),
    ("new_circle_post", "New Circle Post"),
    ("support_reply", "Support Reply"),
    ("admin_announcement", "Admin Announcement"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0005_mobile_preferences_and_notification_title"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=NOTIFICATION_CHOICES,
                db_index=True,
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="notificationmetrics",
            name="notification_type",
            field=models.CharField(choices=NOTIFICATION_CHOICES, max_length=50),
        ),
    ]
