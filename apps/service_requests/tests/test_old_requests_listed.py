"""Officials' list shows every request of their area, however old (decision of 2026-11-20).

Open and finished requests alike: nothing is hidden by age, and nothing is deleted. The area rule still
applies: an official sees only the requests of their own area. (Before 2026-11-20 a completed, rejected
or cancelled request dropped off 180 days after its last change.)
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.service_requests.models import ServiceRequest

from .conftest import LIST, MINE

pytestmark = pytest.mark.django_db


def age(request_id, status, days):
    """Set a request's status and make its last change `days` old (update() skips auto_now)."""
    ServiceRequest.objects.filter(pk=request_id).update(status=status, updated_at=timezone.now() - timedelta(days=days))


def listed(client, query=""):
    data = client.get(LIST + query).json()
    return {row["id"] for row in data["results"]}, data


@pytest.fixture
def five(request_from):
    """Five requests in Kolakaluru, aged and closed in different ways."""
    names = ["old_open", "recent_done", "old_done", "old_rejected", "old_cancelled"]
    ids = {name: request_from(ip=f"10.0.0.{n}")[1] for n, name in enumerate(names, start=1)}
    age(ids["old_open"], "NEW", 400)
    age(ids["recent_done"], "COMPLETED", 10)
    age(ids["old_done"], "COMPLETED", 200)
    age(ids["old_rejected"], "REJECTED", 500)
    age(ids["old_cancelled"], "CANCELLED", 365)
    return ids


@pytest.mark.parametrize("viewer", ["super", "va_kolakaluru", "ma_tenali", "da_guntur"])
def test_every_request_of_the_area_however_old(api, world, five, viewer):
    shown, data = listed(api(world.users[viewer]))
    assert shown == set(five.values())
    assert data["count"] == 5
    assert data["status_counts"] == {"NEW": 1, "APPROVED": 0, "IN_PROGRESS": 0, "COMPLETED": 2, "REJECTED": 1, "CANCELLED": 1}
    assert "closed_visible_days" not in data


@pytest.mark.parametrize("viewer", ["va_kaza", "ma_gannavaram", "da_krishna"])
def test_officials_outside_the_area_still_see_none(api, world, five, viewer):
    assert listed(api(world.users[viewer]))[0] == set()


def test_filters_and_search_reach_old_finished_requests(api, world, five):
    client = api(world.users["super"])
    assert listed(client, "?status=COMPLETED")[0] == {five["recent_done"], five["old_done"]}
    assert listed(client, "?status=REJECTED")[0] == {five["old_rejected"]}
    assert five["old_done"] in listed(client, "?q=lakshmi")[0]


def test_an_old_finished_request_opens_and_is_not_deleted(api, world, five):
    response = api(world.users["super"]).get(f"{LIST}{five['old_done']}/")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert ServiceRequest.objects.count() == 5


def test_the_citizens_own_list_keeps_old_finished_requests(world, request_from):
    citizen, request_id = request_from()
    age(request_id, "COMPLETED", 400)
    mine = citizen.client.get(MINE).json()
    rows = mine["results"] if isinstance(mine, dict) else mine
    assert request_id in {row["id"] for row in rows}
