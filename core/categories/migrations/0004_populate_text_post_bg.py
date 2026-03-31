# Data migration to populate text_post_bg for all 6 categories.

from django.db import migrations

TEXT_POST_BG_MAP = {
    "all": "#27260C",
    "love": "#270C0E",
    "trust": "#0C0F27",
    "worship": "#271C0C",
    "patience": "#270C1F",
    "prayer": "#0C2720",
}


def populate_text_post_bg(apps, schema_editor):
    Category = apps.get_model("categories", "Category")
    for slug, color in TEXT_POST_BG_MAP.items():
        Category.objects.filter(slug=slug).update(text_post_bg=color)


def reverse_populate(apps, schema_editor):
    Category = apps.get_model("categories", "Category")
    Category.objects.all().update(text_post_bg=None)


class Migration(migrations.Migration):
    dependencies = [
        ("categories", "0003_category_text_post_bg"),
    ]

    operations = [
        migrations.RunPython(populate_text_post_bg, reverse_populate),
    ]
