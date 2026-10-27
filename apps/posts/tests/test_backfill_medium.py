"""backfill_medium_images: the 640 px file for media uploaded before it existed."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image

from apps.posts.models import MediaKind, PostMedia

pytestmark = pytest.mark.django_db


def photo(size=(1600, 1200)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


def old_media(api, world, post, size=(1600, 1200)) -> PostMedia:
    """An upload as it was before the medium size: no medium file."""
    response = api(world.users["pm_kolakaluru"]).post(f"/api/v1/posts/{post.id}/media/", {"file": photo(size)}, format="multipart")
    media = PostMedia.objects.get(pk=response.json()["id"])
    media.medium.delete(save=False)
    PostMedia.objects.filter(pk=media.pk).update(medium="")
    media.refresh_from_db()
    return media


def run(*args) -> str:
    out = io.StringIO()
    call_command("backfill_medium_images", *args, stdout=out)
    return out.getvalue()


def test_backfill_makes_the_medium_file_from_the_large_one(api, world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media = old_media(api, world, post)
    assert "1 photo(s) or poster(s) need the medium file" in run("--dry-run")
    assert PostMedia.objects.get(pk=media.pk).medium == ""          # the dry run wrote nothing

    assert "Medium files made: 1. Could not read: 0." in run()
    media.refresh_from_db()
    assert media.medium.name.endswith(f"{media.id}-640.webp")
    assert media.medium.name.rsplit("/", 1)[0] == media.image.name.rsplit("/", 1)[0]   # next to the large file
    with Image.open(media_root / media.medium.name) as medium:
        assert medium.format == "WEBP" and medium.size == (640, 480)


def test_backfill_is_safe_to_run_again_and_skips_rows_that_have_one(api, world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    old_media(api, world, post)
    fresh = PostMedia.objects.get(pk=api(world.users["pm_kolakaluru"]).post(
        f"/api/v1/posts/{post.id}/media/", {"file": photo()}, format="multipart"
    ).json()["id"])
    before = fresh.medium.name
    assert "Medium files made: 1." in run()
    assert "Medium files made: 0. Could not read: 0." in run()
    fresh.refresh_from_db()
    assert fresh.medium.name == before


def test_backfill_names_a_video_poster_like_an_upload(api, world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media = old_media(api, world, post)
    PostMedia.objects.filter(pk=media.pk).update(kind=MediaKind.VIDEO)
    run()
    assert PostMedia.objects.get(pk=media.pk).medium.name.endswith(f"{media.id}-poster-640.webp")


def test_a_missing_large_file_is_reported_and_left_alone(api, world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    broken = old_media(api, world, post)
    (media_root / broken.image.name).unlink()
    good = old_media(api, world, post)
    output = run()
    assert f"Could not read the large file of media {broken.pk}" in output
    assert "Medium files made: 1. Could not read: 1." in output
    assert PostMedia.objects.get(pk=broken.pk).medium == ""
    assert PostMedia.objects.get(pk=good.pk).medium.name.endswith("-640.webp")
