"""Admin activity is counted, ordered and paged in the database (audit finding BE-DB-001, admin-activity).

Order (worked out here from the rule): most decisions in the window first, then by name, then by id.
A page loads only its own admins: the number of queries does not grow with the number of admins.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.roles import Role
from apps.posts.models import Category, PostStatus, ReviewAction
from conftest import make_bare_post, make_official

pytestmark = pytest.mark.django_db
URL = "/api/v1/admin/reports/admin-activity/"


def decide(world, actor, times):
    ref = world.ref("kolakaluru")
    for _ in range(times):
        post = make_bare_post(
            author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level=ref.level,
            posted_as_area_id=ref.id, category=Category.objects.first(), title="t", body="b",
            target_level=ref.level, target_area_id=ref.id, status=PostStatus.PUBLISHED,
        )
        ReviewAction.objects.create(post=post, action="PUBLISH", from_status="PENDING", to_status="PUBLISHED",
                                    actor=world.users[actor], actor_role="VILLAGE_ADMIN")


def names(rows):
    return [row["user"]["name"] for row in rows]


def test_most_active_first_then_by_name_across_pages(api, world):
    decide(world, "va_kaza", 3)
    decide(world, "ma_tenali", 1)
    decide(world, "va_nandivelugu", 1)
    client = api(world.users["super"])
    first = client.get(f"{URL}?page_size=2").json()
    second = client.get(f"{URL}?page_size=2&page=2").json()
    assert first["count"] == 9
    assert names(first["results"]) == ["va_kaza", "ma_tenali"]  # 3, then the 1s by name
    assert names(second["results"])[0] == "va_nandivelugu"
    everyone = names(client.get(f"{URL}?page_size=50").json()["results"])
    assert everyone[3:] == sorted(everyone[3:])  # the rest have 0 decisions: by name


def test_a_decision_outside_the_window_does_not_count(api, world):
    decide(world, "va_kaza", 2)
    ReviewAction.objects.update(created_at=timezone.now() - timedelta(days=40))
    rows = api(world.users["super"]).get(f"{URL}?page_size=50").json()["results"]
    assert next(row for row in rows if row["user"]["name"] == "va_kaza")["reviewed"] == 0


def test_queries_do_not_grow_with_admins(api, world):
    client = api(world.users["super"])

    def queries():
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(f"{URL}?page_size=5").status_code == 200
        return len(ctx.captured_queries)

    few = queries()
    for index in range(20):
        make_official(world, f"va_extra_{index:02d}", Role.VILLAGE_ADMIN, "kolakaluru", f"9222{index:06d}")
    assert queries() <= few


def test_the_csv_still_has_every_admin(api, world):
    for index in range(30):
        make_official(world, f"va_extra_{index:02d}", Role.VILLAGE_ADMIN, "kolakaluru", f"9222{index:06d}")
    response = api(world.users["super"]).get(f"{URL}export/?format=csv", HTTP_ACCEPT_LANGUAGE="en")
    lines = response.content.decode("utf-8").splitlines()
    assert len(lines) == 1 + 9 + 30
