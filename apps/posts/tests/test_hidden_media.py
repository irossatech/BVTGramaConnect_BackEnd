"""Photos and videos of taken-down and discarded posts leave /media/ (BVTGC-192, finding BE-SEC-208).

- Taken down or discarded: every file of the post moves to PRIVATE_MEDIA_ROOT, the old /media/ link is gone,
  and the WhatsApp preview picture is deleted. The API gives officials (and the citizen, for their own post)
  signed links that work for HIDDEN_MEDIA_LINK_SECONDS.
- A reversal that shows the post again moves the files back; the links are the /media/ ones again.
- A signed link that is altered, expired or names a file outside posts/ answers 404.
- The daily housekeeping (reconcile) hides the files of posts hidden before, and finishes a move back.
- The private folder may never sit inside a served folder (check posts.E001).
"""

import io
import time
import uuid
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.citizens import services as citizen_services
from apps.citizens.models import CitizenDevice
from apps.citizens.serializers import CitizenPostSerializer
from apps.posts import hidden_media, media, services
from apps.posts.checks import private_media_not_served
from apps.posts.models import Category, Post, PostMedia, PostStatus

pytestmark = pytest.mark.django_db
HIDDEN_PREFIX = "/api/v1/media/hidden/"


def jpeg() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


@pytest.fixture(autouse=True)
def private_root(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private"
    return settings.PRIVATE_MEDIA_ROOT


def names(post) -> list[str]:
    return [name for item in PostMedia.objects.filter(post=post) for name in hidden_media.file_names(item)]


def public(post) -> list[bool]:
    return [default_storage.exists(name) for name in names(post)]


def private(post) -> list[bool]:
    storage = hidden_media.private_storage()
    return [storage.exists(name) for name in names(post)]


@pytest.fixture
def published(world, make_post, django_capture_on_commit_callbacks):
    """A Panchayat member's post with a photo, published by the Village Admin."""
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media.add_photo(world.users["pm_kolakaluru"], post, jpeg())
    post = services.submit_post(world.users["pm_kolakaluru"], post)
    with django_capture_on_commit_callbacks(execute=True):
        post = services.review(world.users["va_kolakaluru"], post, "publish")
    assert len(names(post)) == 3 and all(public(post))
    return post


def take_down(world, post, capture):
    with capture(execute=True):
        return services.review(world.users["va_kolakaluru"], post, "unpublish", "ఫిర్యాదు వచ్చింది")


def detail_media(api, world, name, post):
    response = api(world.users[name]).get(f"/api/v1/posts/{post.pk}/")
    assert response.status_code == 200, response.json()
    return response.json()["media"]


def test_taking_down_moves_every_file_out_of_media_and_gives_officials_signed_links(
    world, api, published, client, settings, django_capture_on_commit_callbacks
):
    settings.SERVE_FILES = True  # Django serves /media/ here, as on a laptop and the qa server
    old_link = default_storage.url(names(published)[0])
    before = client.get(old_link)
    assert before.status_code == 200  # public while published
    # Read it to the end: the test client then closes the file itself (Windows cannot move an open file).
    # Never call response.close() in a test: it sends request_finished, which closes the database
    # connection; SQLite ignores that, MariaDB and PostgreSQL then fail with "server has gone away".
    assert b"".join(before.streaming_content)
    post = take_down(world, published, django_capture_on_commit_callbacks)
    assert post.status == PostStatus.UNPUBLISHED
    assert not any(public(post)) and all(private(post))
    assert client.get(old_link).status_code == 404  # the link seen before stops working

    [item] = detail_media(api, world, "va_kolakaluru", post)
    for key in ("url", "thumb_url"):
        assert HIDDEN_PREFIX in item[key]
        assert "kolakaluru" not in item[key]  # ids and a signature only
    response = client.get(item["url"][item["url"].index(HIDDEN_PREFIX):])  # no login needed: an <img> loads it
    assert response.status_code == 200
    assert b"".join(response.streaming_content)[:4] == b"RIFF"  # the WebP itself
    assert "private" in response["Cache-Control"]


def test_a_reversal_that_publishes_again_moves_the_files_back(world, api, published, django_capture_on_commit_callbacks):
    post = take_down(world, published, django_capture_on_commit_callbacks)
    with django_capture_on_commit_callbacks(execute=True):
        post = services.review(world.users["ma_tenali"], post, "reverse", "తప్పుగా తీసేశారు", to_status=PostStatus.PUBLISHED)
    assert all(public(post)) and not any(private(post))
    [item] = detail_media(api, world, "va_kolakaluru", post)
    assert "/media/posts/" in item["url"] and HIDDEN_PREFIX not in item["url"]


def test_discarding_a_pending_post_hides_its_files(world, make_post, django_capture_on_commit_callbacks):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media.add_photo(world.users["pm_kolakaluru"], post, jpeg())
    post = services.submit_post(world.users["pm_kolakaluru"], post)
    with django_capture_on_commit_callbacks(execute=True):
        post = services.review(world.users["va_kolakaluru"], post, "discard", "సరిపోదు")
    assert not any(public(post)) and all(private(post))


def test_the_whatsapp_preview_of_a_taken_down_post_is_deleted(world, published, django_capture_on_commit_callbacks):
    preview = f"og/{published.share_code}.jpg"
    default_storage.save(preview, ContentFile(b"jpeg"))
    take_down(world, published, django_capture_on_commit_callbacks)
    assert not default_storage.exists(preview)


def test_a_bad_expired_or_outside_link_answers_404(world, api, published, client, django_capture_on_commit_callbacks):
    post = take_down(world, published, django_capture_on_commit_callbacks)
    [item] = detail_media(api, world, "va_kolakaluru", post)
    path = item["url"][item["url"].index(HIDDEN_PREFIX):]
    assert client.get(path[:-3] + "xx/").status_code == 404  # altered
    later = time.time() + 2 * 3600
    with mock.patch("django.core.signing.time.time", return_value=later):
        assert client.get(path).status_code == 404  # expired after HIDDEN_MEDIA_LINK_SECONDS
    from django.core import signing

    for name in ("../grama/settings.py", "og/x.jpg"):
        token = signing.dumps(name, salt=hidden_media.SALT, compress=True)
        assert client.get(f"{HIDDEN_PREFIX}{token}/").status_code == 404


def test_the_citizen_sees_their_discarded_post_with_signed_links(world, django_capture_on_commit_callbacks):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n-1",
    )
    post = citizen_services.create_post(
        device, category=Category.objects.get(key="news"), language="te", title="పైపు", body="నీళ్లు రావడం లేదు",
        photos=[jpeg()],
    )
    with django_capture_on_commit_callbacks(execute=True):
        post = services.review(world.users["va_kolakaluru"], post, "discard", "సరిపోదు")
    post = Post.objects.prefetch_related("media").get(pk=post.pk)
    [photo] = CitizenPostSerializer(post, context={}).data["photos"]
    assert photo["url"].startswith(HIDDEN_PREFIX) and photo["thumb_url"].startswith(HIDDEN_PREFIX)


def test_housekeeping_hides_files_of_posts_hidden_before_and_brings_back_shown_ones(published):
    # Taken down before this release: the status says hidden, the files are still public.
    Post.objects.filter(pk=published.pk).update(status=PostStatus.UNPUBLISHED)
    assert hidden_media.reconcile() == 3
    assert not any(public(published)) and all(private(published))
    # Shown again, but the move back was cut short: the files are still private.
    Post.objects.filter(pk=published.pk).update(status=PostStatus.PUBLISHED)
    assert hidden_media.reconcile() == 3
    assert all(public(published)) and not any(private(published))
    assert hidden_media.reconcile() == 0  # nothing left to do


def test_deleting_media_removes_its_files_from_either_folder(world, published, django_capture_on_commit_callbacks):
    post = take_down(world, published, django_capture_on_commit_callbacks)
    PostMedia.objects.get(post=post).delete()
    assert not any(private(post)) and not any(public(post))


def test_the_private_folder_may_never_be_inside_a_served_folder(settings, tmp_path):
    assert private_media_not_served(None) == []
    settings.PRIVATE_MEDIA_ROOT = settings.MEDIA_ROOT / "private"
    [error] = private_media_not_served(None)
    assert error.id == "posts.E001" and "MEDIA_ROOT" in error.msg
    settings.DEBUG = False
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "htdocs" / "site" / "private"
    [error] = private_media_not_served(None)
    assert "htdocs" in error.msg
