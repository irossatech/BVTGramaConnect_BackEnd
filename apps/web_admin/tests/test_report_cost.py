"""Dashboard and reports in a fixed number of queries, and the console's escalation setting honoured
(audit BE_01, BE-PERF-002)."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.geography.models import District, Mandal, Village
from apps.posts.models import Category, PostStatus
from conftest import make_bare_post


def post_for(world, area_key, status=PostStatus.PUBLISHED):
    ref = world.ref(area_key)
    return make_bare_post(
        author=world.users["super"], posted_as_role="SUPER_ADMIN", posted_as_level="STATE",
        posted_as_area_id=world.ref("ap").id, category=Category.objects.get(key="news"), title="t", body="b",
        target_level=ref.level, target_area_id=ref.id, status=status,
    )


def queries(client, url) -> int:
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200
    return len(captured)


def add_areas(world, n):
    """n more districts, each with a mandal and a village, and a post in each village."""
    state = District.objects.first().state
    for i in range(n):
        district = District.objects.create(state=state, name_en=f"District {i}")
        mandal = Mandal.objects.create(district=district, name_en=f"Mandal {i}")
        village = Village.objects.create(mandal=mandal, name_en=f"Village {i}")
        world.areas[f"v{i}"] = village
        post_for(world, f"v{i}", PostStatus.PENDING)


def test_posts_by_area_and_dashboard_do_not_cost_a_query_per_area(world, api):
    post_for(world, "kolakaluru")
    post_for(world, "kaza", PostStatus.PENDING)
    client = api(world.users["super"])
    before ={url: queries(client, url) for url in ("/api/v1/admin/reports/posts-by-area/", "/api/v1/admin/dashboard/")}
    add_areas(world, 6)
    after = {url: queries(client, url) for url in before}
    assert after == before


def test_posts_by_area_counts_a_post_once_in_each_area_it_reaches(world, api):
    post = post_for(world, "kolakaluru")
    from apps.posts.models import PostTarget

    PostTarget.objects.create(post=post, area_level="VILLAGE", area_id=world.ref("nandivelugu").id)  # same district
    PostTarget.objects.create(post=post, area_level="DISTRICT", area_id=world.ref("krishna").id)
    rows = {row["district"]["id"]: row for row in api(world.users["super"]).get("/api/v1/admin/reports/posts-by-area/").json()}
    assert rows[world.ref("guntur").id]["total"] == 1
    assert rows[world.ref("krishna").id]["total"] == 1 and rows[world.ref("krishna").id]["mandals"] == []
    mandals = {m["mandal"]["id"]: m["total"] for m in rows[world.ref("guntur").id]["mandals"]}
    assert mandals == {world.ref("tenali").id: 1}  # both villages are in Tenali mandal: counted once


def test_dashboard_uses_the_escalation_hours_the_super_admin_set(world, api):
    client = api(world.users["super"])
    assert client.patch("/api/v1/admin/settings/", {"review_escalation_hours": 48}, format="json").status_code == 200
    assert client.get("/api/v1/admin/dashboard/").json()["escalation_hours"] == 48
    review = client.get("/api/v1/admin/reports/review-time/").json()
    assert {row["escalation_hours"] for row in review} == {48}
