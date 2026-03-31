from django.db import models


class Category(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    label = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    icon = models.URLField(max_length=500)
    bg_color = models.CharField(max_length=7)
    bd_color = models.CharField(max_length=7)
    text_post_bg = models.CharField(max_length=7, null=True, blank=True)
    order = models.IntegerField()

    class Meta:
        db_table = "categories"
        ordering = ["order"]

    def __str__(self):
        return self.label
