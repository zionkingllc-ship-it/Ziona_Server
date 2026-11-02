"""Add Post.shuffle_key and give every existing post its own random value.

AddField evaluates a callable default once and writes that single value into
every existing row, so on its own this would give the whole back catalogue the
same key — and those posts would never shuffle relative to one another. The
backfill gives each existing post an independent value.
"""

from django.db import migrations, models

import core.posts.models

BATCH_SIZE = 500


def backfill_shuffle_keys(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    batch = []
    for post in Post.objects.only("id").iterator(chunk_size=BATCH_SIZE):
        post.shuffle_key = core.posts.models.new_shuffle_key()
        batch.append(post)
        if len(batch) >= BATCH_SIZE:
            Post.objects.bulk_update(batch, ["shuffle_key"])
            batch = []
    if batch:
        Post.objects.bulk_update(batch, ["shuffle_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("posts", "0005_postmediathrough_position"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="shuffle_key",
            field=models.FloatField(default=core.posts.models.new_shuffle_key, editable=False),
        ),
        # Reversing drops the column, so there is nothing to undo here.
        migrations.RunPython(backfill_shuffle_keys, migrations.RunPython.noop),
    ]
