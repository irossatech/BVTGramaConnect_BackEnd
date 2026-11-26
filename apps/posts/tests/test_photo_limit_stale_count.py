"""Finding BE-TEST-211 (Stage 6, 2026-11-26): the photo limit's recount reads a prefetched list.

apps/posts/media.py add_photo() checks the room twice: before processing the photo, and again inside the
transaction after the new row is written, with the post row locked (BE-DB-014, BVTGC-109). Both checks
call post.media.all().count(). The API's post views load the post with prefetch_related("media")
(apps/posts/views.py, post_queryset), so count() answers from the list loaded with the post, not from
the database. Uploads sent at the same moment all loaded the post before any of them finished, so each
recount sees only itself and all of them pass the limit of 3: on MariaDB, six uploads at once stored six
photos (grama/tests/test_concurrency_threads.py). The same count guards "photos or one video, never both".

This test shows it without threads, on any database: the post as the view loaded it, while three other
uploads finish meanwhile. Not fixed in this stage, by the stage's rule; strict, so it reports once fixed.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.posts import media
from apps.posts.models import Post, PostMedia
from grama.exceptions import ApiError

pytestmark = pytest.mark.django_db


def jpeg() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


@pytest.mark.xfail(strict=True, reason="BE-TEST-211: the photo limit's recount reads the post's prefetched media")
def test_an_upload_that_loaded_the_post_before_others_finished_is_still_refused(world, make_post, settings):
    author = world.users["pm_kolakaluru"]
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    seen = Post.objects.prefetch_related("media").get(pk=post.pk)  # as the post views load it: no photos yet
    assert list(seen.media.all()) == []
    for _ in range(settings.MAX_PHOTOS_PER_POST):  # the other uploads, finished meanwhile
        media.add_photo(author, Post.objects.get(pk=post.pk), jpeg())
    with pytest.raises(ApiError):
        media.add_photo(author, seen, jpeg())
    assert PostMedia.objects.filter(post=post).count() == settings.MAX_PHOTOS_PER_POST


def test_without_a_prefetched_list_the_limit_holds(world, make_post, settings):
    """The same order of events with the post loaded fresh: refused, as BE-DB-014 meant."""
    author = world.users["pm_kolakaluru"]
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    for _ in range(settings.MAX_PHOTOS_PER_POST):
        media.add_photo(author, Post.objects.get(pk=post.pk), jpeg())
    with pytest.raises(ApiError):
        media.add_photo(author, Post.objects.get(pk=post.pk), jpeg())
    assert PostMedia.objects.filter(post=post).count() == settings.MAX_PHOTOS_PER_POST
