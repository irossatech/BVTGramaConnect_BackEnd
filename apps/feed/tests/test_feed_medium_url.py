"""medium_url: the first photo at 640 px for feed cards on slow networks (CROSS-API-003, BVTGC-95).

image_url stays the 1280 px photo for the full-screen view; the app picks which one a card shows.
"""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.posts.models import MediaKind, Post, PostMedia, PostStatus

pytestmark = pytest.mark.django_db

BASE = "http://testserver/media/posts/2026/10"


def feed(world, path="feed/", village="kolakaluru"):
    return APIClient().get(f"/api/v1/{path}?village_id={world.areas[village].id}").json()


def first_row(world):
    return feed(world)["results"][0]


def with_medium(post):
    """Give each photo of the post its 640 px file, as an upload since posts.0005 has."""
    for media in post.media.all():
        PostMedia.objects.filter(pk=media.pk).update(medium=f"posts/2026/10/{post.pk}-{media.order}-640.webp")


def test_medium_url_is_the_first_photos_640_px_file(world, make_post):
    post = make_post("kolakaluru", media=3)
    with_medium(post)
    row = first_row(world)
    assert row["medium_url"] == f"{BASE}/{post.pk}-0-640.webp"
    # The other sizes are unchanged: 1280 px for full screen, 320 px for small rows.
    assert row["image_url"] == f"{BASE}/{post.pk}-0.webp"
    assert row["thumbnail_url"] == f"{BASE}/{post.pk}-0-320.webp"


def test_the_first_photo_is_picked_by_order(world, make_post):
    post = make_post("kolakaluru", media=2)
    with_medium(post)
    PostMedia.objects.filter(post=post, order=0).update(order=5)  # the second photo is now first
    assert first_row(world)["medium_url"] == f"{BASE}/{post.pk}-1-640.webp"


def test_an_older_photo_without_its_640_px_file_sends_null_and_keeps_image_url(world, make_post):
    post = make_post("kolakaluru", media=1)
    row = first_row(world)
    assert row["medium_url"] is None
    assert row["image_url"] == f"{BASE}/{post.pk}-0.webp"  # what the app falls back to


def test_a_post_without_photos_sends_null(world, make_post):
    make_post("kolakaluru")
    row = first_row(world)
    assert row["medium_url"] is None and row["image_url"] is None


def test_a_video_post_sends_null_like_image_url(world, make_post):
    post = make_post("kolakaluru")
    PostMedia.objects.create(
        post=post, kind=MediaKind.VIDEO, video=f"posts/2026/10/{post.pk}.mp4",
        image=f"posts/2026/10/{post.pk}-poster.webp", medium=f"posts/2026/10/{post.pk}-poster-640.webp",
        thumbnail=f"posts/2026/10/{post.pk}-poster-320.webp", width=1280, height=720, bytes=50000, order=0,
    )
    row = first_row(world)
    assert row["medium_url"] is None and row["image_url"] is None
    assert row["video"]["poster_url"] == f"{BASE}/{post.pk}-poster.webp"


def test_every_feed_shape_carries_it(world, make_post):
    post = make_post("kolakaluru", media=1)
    with_medium(post)
    today = timezone.localdate()
    Post.objects.filter(pk=post.pk).update(
        is_urgent=True, event_kind="meeting", event_start=today + timedelta(days=2), share_code="Ab3xK9q2"
    )
    expected = f"{BASE}/{post.pk}-0-640.webp"
    village = world.areas["kolakaluru"].id
    client = APIClient()
    shapes = {
        "list": feed(world)["results"][0],
        "detail": client.get(f"/api/v1/feed/posts/{post.pk}/").json(),
        "ids": client.get(f"/api/v1/feed/posts/?ids={post.pk}").json()[0],
        "upcoming": client.get(f"/api/v1/feed/upcoming/?village_id={village}").json()[0],
        "urgent": client.get(f"/api/v1/feed/urgent/?village_id={village}").json()[0],
        "share": client.get("/api/v1/feed/share/Ab3xK9q2/").json(),
    }
    assert {name: row["medium_url"] for name, row in shapes.items()} == {name: expected for name in shapes}


def test_it_adds_no_query_per_post(world, make_post, django_assert_max_num_queries):
    with_medium(make_post("kolakaluru", media=1))
    with CaptureQueriesContext(connection) as one:
        feed(world)
    for _ in range(5):
        with_medium(make_post("kolakaluru", media=2))
    with django_assert_max_num_queries(len(one.captured_queries)):
        rows = feed(world)["results"]
    assert len(rows) == 6 and all(row["medium_url"] for row in rows)


def test_a_real_upload_and_the_backfill_both_fill_it(api, world, make_post, settings, tmp_path):
    """End to end: an upload makes the 640 px file; an older photo gets it from backfill_medium_images."""
    settings.MEDIA_ROOT = media_root = tmp_path / "media"
    post = make_post("kolakaluru", status=PostStatus.DRAFT, author="pm_kolakaluru")
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (31, 138, 59)).save(buffer, "JPEG")
    upload = SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")
    response = api(world.users["pm_kolakaluru"]).post(f"/api/v1/posts/{post.pk}/media/", {"file": upload}, format="multipart")
    assert response.status_code == 201
    Post.objects.filter(pk=post.pk).update(status=PostStatus.PUBLISHED, published_at=timezone.now())
    media = PostMedia.objects.get(post=post)

    url = first_row(world)["medium_url"]
    assert url.endswith(f"{media.id}-640.webp")
    with Image.open(media_root / media.medium.name) as medium:
        assert medium.size == (640, 480)

    # The same photo as it was before posts.0005: no 640 px file, so null, until the backfill runs.
    media.medium.delete(save=False)
    PostMedia.objects.filter(pk=media.pk).update(medium="")
    assert first_row(world)["medium_url"] is None
    call_command("backfill_medium_images", stdout=io.StringIO())
    assert first_row(world)["medium_url"].endswith(f"{media.id}-640.webp")
