"""media_count on every public news list, counted without grouping the posts (audit finding BE-DB-008).

media_count is the number of photos and videos of the post: worked out here from what each test attaches.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.feed.views import published_posts, village_feed
from apps.posts.models import MediaKind, PostMedia

pytestmark = pytest.mark.django_db


def add_video(post):
    PostMedia.objects.create(
        post=post, kind=MediaKind.VIDEO, video=f"posts/2026/10/{post.pk}.mp4",
        image=f"posts/2026/10/{post.pk}-poster.webp", thumbnail=f"posts/2026/10/{post.pk}-poster-320.webp",
        width=1280, height=720, bytes=3_000_000, duration_s=42, order=9,
    )


@pytest.fixture
def posts(world, make_post):
    """title -> expected media_count, all for Kolakaluru, newest first."""
    none = make_post("kolakaluru", title="none", minutes_ago=1)
    one = make_post("kolakaluru", title="one", media=1, minutes_ago=2)
    three = make_post("kolakaluru", title="three", media=3, minutes_ago=3)
    video = make_post("kolakaluru", title="video", minutes_ago=4)
    add_video(video)
    mixed = make_post("tenali", title="mixed", media=2, minutes_ago=5)
    add_video(mixed)
    return {"none": 0, "one": 1, "three": 3, "video": 1, "mixed": 3}, [none, one, three, video, mixed]


def counts(rows):
    return {row["title"]: row["media_count"] for row in rows}


def test_the_feed(world, posts):
    expected, _ = posts
    response = APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}")
    assert counts(response.json()["results"]) == expected


def test_one_post_and_saved_posts(world, posts):
    expected, made = posts
    client = APIClient()
    for post in made:
        assert client.get(f"/api/v1/feed/posts/{post.pk}/").json()["media_count"] == expected[post.title]
    ids = ",".join(str(post.pk) for post in made)
    assert counts(client.get(f"/api/v1/feed/posts/?ids={ids}").json()) == expected


def test_the_website_showcase(world, posts):
    data = APIClient().get("/api/v1/public/showcase/").json()
    expected, _ = posts
    for row in [data["featured"], *data["items"]]:
        assert row["media_count"] == expected[row["title"]]


def test_the_feed_query_groups_nothing(world, posts):
    """The outer statement has no GROUP BY (the per-post subqueries may group their own rows)."""
    for queryset in (published_posts().order_by("-published_at"), village_feed(world.areas["kolakaluru"].id)):
        outer = str(queryset.query)
        while "(SELECT" in outer:  # drop every subquery, keep the outer statement
            start = outer.find("(SELECT")
            depth, end = 0, start
            for end in range(start, len(outer)):
                depth += {"(": 1, ")": -1}.get(outer[end], 0)
                if depth == 0:
                    break
            outer = outer[:start] + outer[end + 1:]
        assert "GROUP BY" not in outer.upper()


def test_the_feed_query_count_stays_flat(world, make_post):
    client = APIClient()
    url = f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}"
    make_post("kolakaluru", media=2)
    with CaptureQueriesContext(connection) as small:
        client.get(url)
    for index in range(8):
        make_post("kolakaluru", media=index % 4)
    with CaptureQueriesContext(connection) as large:
        client.get(url)
    assert len(large.captured_queries) <= len(small.captured_queries)
