from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("engagement", "0003_hiddenpost_hiddenpost_uq_hidden_user_post"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookmarkfolder",
            name="thumbnail_url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
