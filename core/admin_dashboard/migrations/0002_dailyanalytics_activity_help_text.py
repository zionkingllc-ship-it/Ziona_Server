from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dailyanalytics",
            name="dau",
            field=models.IntegerField(default=0, help_text="Daily active users"),
        ),
        migrations.AlterField(
            model_name="dailyanalytics",
            name="wau",
            field=models.IntegerField(default=0, help_text="Weekly active users"),
        ),
        migrations.AlterField(
            model_name="dailyanalytics",
            name="mau",
            field=models.IntegerField(default=0, help_text="Monthly active users"),
        ),
    ]
