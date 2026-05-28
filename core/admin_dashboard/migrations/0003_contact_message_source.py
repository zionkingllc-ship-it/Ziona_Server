from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0002_dailyanalytics_activity_help_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="contactmessage",
            name="brand",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="ip_address",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="source",
            field=models.CharField(db_index=True, default="mobile_app", max_length=50),
        ),
        migrations.AddIndex(
            model_name="contactmessage",
            index=models.Index(
                fields=["source", "-created_at"],
                name="idx_contact_source_created",
            ),
        ),
    ]
