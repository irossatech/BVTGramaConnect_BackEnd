"""Feed extras from the owner decisions: news search, video posts, "Don't miss" events (Q7), urgent (Q19)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.posts.models import MediaKind, Post, PostMedia, PostStatus

pytestmark = pytest.mark.django_db


def get(world, path, village="kolakaluru", query="", **headers):
    return APIClient().get(f"/api/v1/feed/{path}?village_id={world.areas[village].id}{query}", **headers)


def titles(rows):
    return [row["title"] for row in rows]


def with_event(post, start, end=None, kind="camp"):
    Post.objects.filter(pk=post.pk).update(event_kind=kind, event_start=start, event_end=end)
    return post


def add_video(post, seconds=42):
    return PostMedia.objects.create(
        post=post, kind=MediaKind.VIDEO, video=f"posts/2026/10/{post.pk}.mp4",
        image=f"posts/2026/10/{post.pk}-poster-1280.webp", thumbnail=f"posts/2026/10/{post.pk}-poster-320.webp",
        width=1280, height=720, bytes=3_000_000, duration_s=seconds,
    )


# --- News search --------------------------------------------------------------------------


def test_search_matches_title_or_body_in_any_case(world, make_post):
    make_post("kolakaluru", title="Water supply on Monday")
    make_post("tenali", title="తాగునీటి సరఫరా")
    make_post("guntur", title="Road work")
    make_post("kaza", title="water elsewhere")                           # not in this village feed
    assert set(titles(get(world, "", query="&q=WATER").json()["results"])) == {"Water supply on Monday"}
    assert titles(get(world, "", query="&q=తాగునీటి").json()["results"]) == ["తాగునీటి సరఫరా"]
    # The fixture body says "గ్రామ సభ రేపు ఉదయం 10 గంటలకు."
    assert len(get(world, "", query="&q=%E0%B0%97%E0%B1%8D%E0%B0%B0%E0%B0%BE%E0%B0%AE").json()["results"]) == 3


def test_search_combines_with_category(world, make_post):
    make_post("kolakaluru", title="water news", category="news")
    make_post("kolakaluru", title="water notice", category="announce")
    assert titles(get(world, "", query="&q=water&category=news").json()["results"]) == ["water news"]


def test_search_needs_two_characters(world, make_post):
    response = get(world, "", query="&q=a")
    assert response.status_code == 400
    assert "q" in response.json()["error"]["fields"]
    assert get(world, "", query="&q=%20%20").status_code == 200        # blank means no search


# --- Video posts --------------------------------------------------------------------------


def test_video_posts_only(world, make_post):
    video = make_post("kolakaluru", title="video")
    add_video(video)
    make_post("kolakaluru", title="photo", media=1)
    make_post("kolakaluru", title="text")
    rows = get(world, "", query="&has_video=1").json()["results"]
    assert titles(rows) == ["video"]
    assert rows[0]["video"] == {
        "url": f"/media/posts/2026/10/{video.pk}.mp4",
        "poster_url": f"/media/posts/2026/10/{video.pk}-poster-1280.webp",
        "duration_s": 42,
    }
    assert rows[0]["thumbnail_url"] == f"/media/posts/2026/10/{video.pk}-poster-320.webp"
    assert rows[0]["image_url"] is None                                  # the poster is in video.poster_url
    assert len(get(world, "").json()["results"]) == 3


def test_video_in_the_post_detail(world, make_post):
    post = make_post("kolakaluru")
    add_video(post, seconds=12)
    media = APIClient().get(f"/api/v1/feed/posts/{post.id}/").json()["media"]
    assert media[0]["kind"] == "video"
    assert media[0]["url"].endswith(".mp4") and media[0]["poster_url"].endswith("-poster-1280.webp")
    assert media[0]["duration_s"] == 12


def test_photo_posts_have_no_video(world, make_post):
    make_post("kolakaluru", media=1)
    row = get(world, "").json()["results"][0]
    assert row["video"] is None
    detail = APIClient().get(f"/api/v1/feed/posts/{row['id']}/").json()
    assert detail["media"][0]["kind"] == "image" and detail["media"][0]["poster_url"] is None


# --- "Don't miss": events from today on ---------------------------------------------------


def test_upcoming_events_soonest_first_and_past_ones_dropped(world, make_post):
    today = timezone.localdate()
    with_event(make_post("kolakaluru", title="next week"), today + timedelta(days=7))
    with_event(make_post("tenali", title="today"), today, kind="meeting")
    with_event(make_post("guntur", title="still running"), today - timedelta(days=2), today + timedelta(days=1))
    with_event(make_post("ap", title="tomorrow"), today + timedelta(days=1))
    with_event(make_post("kolakaluru", title="over"), today - timedelta(days=3), today - timedelta(days=1))
    with_event(make_post("kolakaluru", title="yesterday"), today - timedelta(days=1))
    with_event(make_post("kaza", title="other village"), today + timedelta(days=1))
    with_event(make_post("kolakaluru", title="pending", status=PostStatus.PENDING), today + timedelta(days=1))
    make_post("kolakaluru", title="no event")
    response = get(world, "upcoming/")
    assert response.status_code == 200
    rows = response.json()
    assert titles(rows) == ["still running", "today", "tomorrow", "next week"]
    assert rows[1]["event"] == {"kind": "meeting", "start_date": today.isoformat(), "end_date": None}
    assert "max-age=60" in response["Cache-Control"]


def test_upcoming_is_at_most_ten(world, make_post):
    today = timezone.localdate()
    for day in range(12):
        with_event(make_post("kolakaluru", title=f"d{day:02d}"), today + timedelta(days=day))
    assert titles(get(world, "upcoming/").json()) == [f"d{day:02d}" for day in range(10)]


# --- Urgent banner ----------------------------------------------------------------------


def urgent(post, days_ago=0):
    Post.objects.filter(pk=post.pk).update(is_urgent=True, published_at=timezone.now() - timedelta(days=days_ago))
    return post


def test_urgent_posts_of_the_last_seven_days_newest_first(world, make_post):
    urgent(make_post("kolakaluru", title="two days"), days_ago=2)
    urgent(make_post("guntur", title="today"))
    urgent(make_post("tenali", title="eight days"), days_ago=8)
    urgent(make_post("kaza", title="other village"))
    urgent(make_post("kolakaluru", title="taken down", status=PostStatus.UNPUBLISHED))
    make_post("kolakaluru", title="ordinary")
    rows = get(world, "urgent/").json()
    assert titles(rows) == ["today", "two days"]
    assert all(row["is_urgent"] for row in rows)
    flags = {row["title"]: row["is_urgent"] for row in get(world, "").json()["results"]}
    assert flags["ordinary"] is False and flags["two days"] is True


def test_urgent_is_at_most_five(world, make_post):
    for index in range(7):
        urgent(make_post("kolakaluru", title=f"u{index}", minutes_ago=index))
    rows = get(world, "urgent/").json()
    assert len(rows) == 5


def test_every_list_carries_the_large_photo(world, make_post):
    post = urgent(make_post("kolakaluru", media=2))
    with_event(post, timezone.localdate() + timedelta(days=1))
    large = f"/media/posts/2026/10/{post.pk}-0.webp"
    assert get(world, "upcoming/").json()[0]["image_url"] == large
    assert get(world, "urgent/").json()[0]["image_url"] == large
    assert APIClient().get(f"/api/v1/feed/posts/?ids={post.pk}").json()[0]["image_url"] == large


@pytest.mark.parametrize("path", ["upcoming/", "urgent/"])
def test_short_lists_need_an_active_village(world, path):
    assert APIClient().get(f"/api/v1/feed/{path}").status_code == 400
    assert APIClient().get(f"/api/v1/feed/{path}?village_id=999999").status_code == 404


@pytest.mark.parametrize("path", ["upcoming/", "urgent/"])
def test_short_lists_keep_queries_flat(world, make_post, django_assert_max_num_queries, path):
    today = timezone.localdate()
    for index in range(10):
        post = urgent(make_post("kolakaluru" if index % 2 else "guntur", media=1))
        with_event(post, today + timedelta(days=index))
    with django_assert_max_num_queries(8):
        assert len(get(world, path).json()) >= 5
