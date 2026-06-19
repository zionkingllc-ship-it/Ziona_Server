from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediafile",
            name="processing_error_code",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="mediafile",
            name="processing_error_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="mediafile",
            name="processing_failed_stage",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]
