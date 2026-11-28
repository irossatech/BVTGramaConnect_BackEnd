"""Post.share_code: the random part of a post's public link (/p/<code>/), and a code for every
post that is published now. Later posts get theirs when they are published (apps.posts.sharing)."""

import secrets
import string

from django.db import migrations, models

ALPHABET = string.ascii_letters + string.digits


def give_published_posts_a_code(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    used = set(Post.objects.exclude(share_code=None).values_list("share_code", flat=True))
    for post_id in Post.objects.filter(status="PUBLISHED", share_code=None).values_list("id", flat=True):
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        while code in used:
            code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        used.add(code)
        Post.objects.filter(id=post_id).update(share_code=code)


class Migration(migrations.Migration):

    dependencies = [
        ("posts", "0008_post_targets"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="share_code",
            field=models.CharField(blank=True, editable=False, max_length=10, null=True, unique=True),
        ),
        migrations.RunPython(give_published_posts_a_code, migrations.RunPython.noop),
    ]
