"""Dashboard and reports count in the database (audit BE-DB-001).

The expected numbers are worked out here from the rule: a post counts once in each district and
mandal it reaches; a post for the whole state (Everyone) counts in no district; drafts and posts
older than the window do not count.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.geography.models import District, Mandal, Village
from apps.posts.models import Category, Post, PostStatus, PostTarget
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


def post_for(world, areas, status, *, days_ago=0, hours_ago=1):
    """A post by pm_kolakaluru for every area key in `areas` ("ap" is Everyone)."""
    first = world.ref(areas[0])
    now = timezone.now()
    post = make_bare_post(
        author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level="VILLAGE",
        posted_as_area_id=world.ref("kolakaluru").id, category=Category.objects.get(key="announce"),
        title="పరీక్ష", body="b", target_level=first.level, target_area_id=first.id, status=status,
        submitted_at=now - timedelta(hours=hours_ago),
    )
    for key in areas[1:]:
        ref = world.ref(key)
        PostTarget.objects.create(post=post, area_level=ref.level, area_id=ref.id)
    if days_ago:
        Post.objects.filter(pk=post.pk).update(created_at=now - timedelta(days=days_ago))
    return post


def counts(row):
    return (row["total"], row["published"], row["pending"], row["rejected"])


@pytest.fixture
def multi_area_posts(world):
    post_for(world, ["kolakaluru", "nandivelugu"], PostStatus.PUBLISHED)  # A: two villages of Tenali
    post_for(world, ["mangalagiri", "kolakaluru"], PostStatus.PENDING, hours_ago=30)  # B: a mandal and a village
    post_for(world, ["krishna", "kaza"], PostStatus.DISCARDED)  # C: a district and a village elsewhere
    post_for(world, ["ap"], PostStatus.PUBLISHED)  # D: Everyone
    post_for(world, ["kolakaluru"], PostStatus.DRAFT)  # E: a draft
    post_for(world, ["kolakaluru"], PostStatus.PUBLISHED, days_ago=40)  # F: outside 30 days


def test_posts_by_area_counts_each_post_once_per_area(api, world, multi_area_posts):
    data = api(world.users["super"]).get("/api/v1/admin/reports/posts-by-area/").json()
    districts = {row["district"]["id"]: row for row in data}
    guntur = districts[world.areas["guntur"].id]
    krishna = districts[world.areas["krishna"].id]
    assert counts(guntur) == (3, 1, 1, 1)  # A, B, C once each
    assert counts(krishna) == (1, 0, 0, 1)  # C only
    assert krishna["mandals"] == []  # C is for the whole district, not one mandal
    mandals = {row["mandal"]["id"]: row for row in guntur["mandals"]}
    assert counts(mandals[world.areas["tenali"].id]) == (2, 1, 1, 0)  # A, B
    assert counts(mandals[world.areas["mangalagiri"].id]) == (2, 0, 1, 1)  # B, C
    # Ordered by English name, as before: Mangalagiri, then Tenali.
    assert [row["mandal"]["id"] for row in guntur["mandals"]] == [world.areas["mangalagiri"].id, world.areas["tenali"].id]


def test_summary_counts_each_post_once(api, world, multi_area_posts):
    data = api(world.users["super"]).get("/api/v1/admin/reports/summary/").json()
    assert data == {"total": 4, "published": 2, "pending": 1, "rejected": 1, "days": 30}  # A, B, C, D


def test_districts_without_posts_are_listed_with_zeros(api, world):
    District.objects.create(state=world.state, name_en="Prakasam", name_te="ప్రకాశం")
    data = api(world.users["super"]).get("/api/v1/admin/reports/posts-by-area/").json()
    assert [row["district"]["name"] for row in data] == ["గుంటూరు", "కృష్ణా", "ప్రకాశం"]  # name_en order
    assert all(counts(row) == (0, 0, 0, 0) and row["mandals"] == [] for row in data)


def test_dashboard_pending_counts_a_post_once_per_district(api, world):
    post_for(world, ["mangalagiri", "kolakaluru"], PostStatus.PENDING, hours_ago=30)
    post_for(world, ["nidamanuru", "kesarapalli"], PostStatus.PENDING, hours_ago=5)
    post_for(world, ["kaza"], PostStatus.PUBLISHED)
    data = api(world.users["super"]).get("/api/v1/admin/dashboard/").json()
    by_district = {row["district"]["id"]: row for row in data["pending_by_district"]}
    assert by_district[world.areas["guntur"].id]["pending"] == 1
    assert by_district[world.areas["guntur"].id]["oldest_pending_hours"] == pytest.approx(30, abs=0.2)
    assert by_district[world.areas["krishna"].id]["pending"] == 1
    assert by_district[world.areas["krishna"].id]["oldest_pending_hours"] == pytest.approx(5, abs=0.2)
    assert data["kpis"]["pending_districts"] == 2


def add_districts(world, how_many):
    """More districts, each with mandals, villages and a pending post for one of the villages."""
    category = Category.objects.get(key="announce")
    for index in range(how_many):
        district = District.objects.create(state=world.state, name_en=f"Extra {index}")
        for m_index in range(3):
            mandal = Mandal.objects.create(district=district, name_en=f"Extra {index}-{m_index}")
            village = Village.objects.create(mandal=mandal, name_en=f"Extra {index}-{m_index}-v")
            make_bare_post(
                author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level="VILLAGE",
                posted_as_area_id=world.ref("kolakaluru").id, category=category, title="t", body="b",
                target_level="VILLAGE", target_area_id=village.id, status=PostStatus.PENDING,
                submitted_at=timezone.now(),
            )


@pytest.mark.parametrize("path", ["/api/v1/admin/dashboard/", "/api/v1/admin/reports/posts-by-area/"])
def test_queries_do_not_grow_with_districts_and_mandals(api, world, multi_area_posts, path):
    client = api(world.users["super"])
    with CaptureQueriesContext(connection) as small:
        assert client.get(path).status_code == 200
    add_districts(world, 4)
    with CaptureQueriesContext(connection) as large:
        assert client.get(path).status_code == 200
    assert len(large.captured_queries) <= len(small.captured_queries)
