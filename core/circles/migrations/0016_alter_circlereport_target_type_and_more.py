"""Add ``comment`` to the circle-report target types.

Choices-only, so it is a no-op at the database level — but two AlterField
operations are required because ``HiddenCircleContent.target_type`` reuses
``CircleReport.TARGET_TYPE_CHOICES`` by reference. Mirrors migration 0014,
which added ``post`` the same way.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("circles", "0015_circlepostmediathrough_position"),
    ]

    operations = [
        migrations.AlterField(
            model_name="circlereport",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("anchor", "Anchor"),
                    ("response", "Anchor Response"),
                    ("circle", "Circle"),
                    ("post", "Circle Post"),
                    ("comment", "Circle Post Comment"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="hiddencirclecontent",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("anchor", "Anchor"),
                    ("response", "Anchor Response"),
                    ("circle", "Circle"),
                    ("post", "Circle Post"),
                    ("comment", "Circle Post Comment"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
    ]
