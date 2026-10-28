"""Citizen feed: which posts, in which order, how many per page, in what shape."""

import uuid

import pytest
from rest_framework.test import APIClient

from apps.geography.models import Village
from apps.posts.models import PostMedia, PostStatus

pytestmark = pytest.mark.django_db


def feed(world, village="kolakaluru", query="", client=None, **headers):
    client = client or APIClient()
    return client.get(f"/api/v1/feed/?village_id={world.areas[village].id}{query}", **headers)


def titles(response):
    return [row["title"] for row in response.json()["results"]]


def test_feed_has_the_village_its_mandal_district_and_state_only(world, make_post):
    visible = [make_post(key, title=key) for key in ("kolakaluru", "tenali", "guntur", "ap")]
    for key in ("nandivelugu", "mangalagiri", "kaza", "krishna", "gannavaram", "nidamanuru"):
        make_post(key, title=key)
    response = feed(world)
    assert response.status_code == 200
    assert set(titles(response)) == {post.title for post in visible}


@pytest.mark.parametrize(
    "status", [PostStatus.DRAFT, PostStatus.PENDING, PostStatus.RECHECK, PostStatus.DISCARDED, PostStatus.UNPUBLISHED]
)
def test_only_published_posts(world, make_post, status):
    make_post("kolakaluru", status=status)
    assert feed(world).json()["results"] == []


def test_newest_first(world, make_post):
    make_post("guntur", title="old", minutes_ago=60)
    make_post("kolakaluru", title="new", minutes_ago=1)
    make_post("tenali", title="middle", minutes_ago=30)
    assert titles(feed(world)) == ["new", "middle", "old"]


def test_cursor_pages_of_twenty(world, make_post):
    for minute in range(45):
        make_post("kolakaluru", title=f"p{minute:02d}", minutes_ago=minute)
    client = APIClient()
    seen = []
    page = client.get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}")
    sizes = []
    while True:
        data = page.json()
        sizes.append(len(data["results"]))
        seen += [row["title"] for row in data["results"]]
        if not data["next"]:
            break
        assert "cursor=" in data["next"]
        page = client.get(data["next"])
    assert sizes == [20, 20, 5]
    assert seen == [f"p{minute:02d}" for minute in range(45)]


def test_slim_shape_with_thumbnail(world, make_post):
    post = make_post("tenali", media=3)
    make_post("kolakaluru", minutes_ago=5)
    rows = feed(world).json()["results"]
    first, second = rows
    assert set(first) == {
        "id", "category", "language", "title", "body", "translation", "area", "published_at",
        "thumbnail_url", "image_url", "media_count", "source_role", "event", "is_urgent", "video",
    }
    assert first["event"] is None and first["is_urgent"] is False and first["video"] is None
    assert first["id"] == str(post.id)
    assert first["category"] == "announce"
    assert first["area"] == {"level": "MANDAL", "id": world.areas["tenali"].id, "name": "తెనాలి"}
    assert first["thumbnail_url"] == f"/media/posts/2026/10/{post.pk}-0-320.webp"
    assert first["image_url"] == f"/media/posts/2026/10/{post.pk}-0.webp"
    assert first["media_count"] == 3
    assert first["source_role"] == "VILLAGE_ADMIN"
    assert second["thumbnail_url"] is None and second["media_count"] == 0
    assert second["image_url"] is None


def test_image_url_is_the_full_size_photo_even_with_a_medium_file(world, make_post):
    """The 640 px file looked soft on big cards, so image_url stays the 1280 px photo."""
    post = make_post("kolakaluru", media=2)
    first = post.media.order_by("order").first()
    PostMedia.objects.filter(pk=first.pk).update(medium=f"posts/2026/10/{post.pk}-0-640.webp")
    row = feed(world).json()["results"][0]
    assert row["image_url"] == f"/media/posts/2026/10/{post.pk}-0.webp"
    assert row["thumbnail_url"] == f"/media/posts/2026/10/{post.pk}-0-320.webp"


def test_area_names_follow_accept_language(world, make_post):
    make_post("guntur")
    row = feed(world, HTTP_ACCEPT_LANGUAGE="en").json()["results"][0]
    assert row["area"]["name"] == "Guntur"


def test_category_filter(world, make_post):
    make_post("kolakaluru", category="news", title="news")
    make_post("kolakaluru", category="updates", title="updates")
    make_post("kolakaluru", category="schemes", title="schemes")
    assert set(titles(feed(world, query="&category=news,updates"))) == {"news", "updates"}


def test_public_even_with_a_stale_token(world, make_post):
    make_post("kolakaluru")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")
    assert feed(world, client=client).status_code == 200


def test_cacheable_and_304_when_unchanged(world, make_post):
    make_post("kolakaluru")
    first = feed(world)
    assert "max-age=60" in first["Cache-Control"] and "public" in first["Cache-Control"]
    again = feed(world, HTTP_IF_NONE_MATCH=first["ETag"])
    assert again.status_code == 304


def test_village_id_is_required_and_must_be_active(world):
    assert APIClient().get("/api/v1/feed/").status_code == 400
    assert APIClient().get("/api/v1/feed/?village_id=999999").status_code == 404
    Village.objects.filter(pk=world.areas["kolakaluru"].pk).update(is_active=False)
    assert feed(world).status_code == 404


def test_post_detail_has_every_photo(world, make_post):
    post = make_post("kolakaluru", media=2)
    data = APIClient().get(f"/api/v1/feed/posts/{post.id}/").json()
    assert [m["thumb_url"] for m in data["media"]] == [
        f"/media/posts/2026/10/{post.pk}-0-320.webp",
        f"/media/posts/2026/10/{post.pk}-1-320.webp",
    ]
    assert data["media"][0]["url"] == f"/media/posts/2026/10/{post.pk}-0.webp"
    assert data["path"] == "కొలకలూరు · తెనాలి · గుంటూరు"


def test_unpublished_post_detail_is_404(world, make_post):
    post = make_post("kolakaluru", status=PostStatus.UNPUBLISHED)
    response = APIClient().get(f"/api/v1/feed/posts/{post.id}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_saved_posts_by_id_keep_order_and_drop_unpublished(world, make_post):
    a = make_post("kolakaluru", title="a")
    b = make_post("krishna", title="b")
    gone = make_post("kolakaluru", status=PostStatus.UNPUBLISHED)
    ids = ",".join(str(p.id) for p in (b, gone, a))
    data = APIClient().get(f"/api/v1/feed/posts/?ids={ids}").json()
    assert [row["title"] for row in data] == ["b", "a"]


@pytest.mark.parametrize("ids", ["", "not-a-uuid", ",".join(str(uuid.uuid4()) for _ in range(51))])
def test_saved_posts_validation(world, ids):
    assert APIClient().get(f"/api/v1/feed/posts/?ids={ids}").status_code == 400


def test_feed_query_count_stays_flat(world, make_post, django_assert_max_num_queries):
    for index in range(20):
        make_post("kolakaluru" if index % 2 else "guntur", media=1)
    with django_assert_max_num_queries(8):
        assert len(feed(world).json()["results"]) == 20
