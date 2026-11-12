"""Officials' list shows finished requests only for a while (decision of 2026-11-11, like the audit log).

Open requests are always listed. A completed or rejected request is listed while its last change is
at most SERVICE_REQUEST_CLOSED_VISIBLE_DAYS old. Nothing is deleted: it still opens by its id, and the
citizen's own list keeps it.
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
def five(request_from, settings):
    """Five requests in Kolakaluru, aged and closed in different ways."""
    settings.SERVICE_REQUEST_CLOSED_VISIBLE_DAYS = 180
    names = ["old_open", "recent_done", "old_done", "old_rejected", "old_in_progress"]
    ids = {name: request_from(ip=f"10.0.0.{n}")[1] for n, name in enumerate(names, start=1)}
    age(ids["old_open"], "NEW", 400)
    age(ids["recent_done"], "COMPLETED", 10)
    age(ids["old_done"], "COMPLETED", 200)
    age(ids["old_rejected"], "REJECTED", 181)
    age(ids["old_in_progress"], "IN_PROGRESS", 300)
    return ids


@pytest.mark.parametrize("viewer", ["super", "va_kolakaluru"])
def test_open_always_finished_only_while_recent(api, world, five, viewer):
    shown, data = listed(api(world.users[viewer]))
    assert shown == {five["old_open"], five["recent_done"], five["old_in_progress"]}
    assert data["count"] == 3
    assert data["status_counts"] == {"NEW": 1, "APPROVED": 0, "IN_PROGRESS": 1, "COMPLETED": 1, "REJECTED": 0}
    assert data["closed_visible_days"] == 180


def test_filters_never_bring_back_old_finished_requests(api, world, five):
    client = api(world.users["super"])
    assert listed(client, "?status=COMPLETED")[0] == {five["recent_done"]}
    assert listed(client, "?status=REJECTED")[0] == set()
    assert five["old_done"] not in listed(client, "?q=lakshmi")[0]


def test_an_old_finished_request_still_opens_and_is_not_deleted(api, world, five):
    response = api(world.users["super"]).get(f"{LIST}{five['old_done']}/")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert ServiceRequest.objects.count() == 5


def test_a_new_change_lists_it_again(api, world, five):
    ServiceRequest.objects.filter(pk=five["old_done"]).update(updated_at=timezone.now())  # e.g. a note added today
    assert five["old_done"] in listed(api(world.users["super"]))[0]


def test_the_days_come_from_the_setting(api, world, five, settings):
    settings.SERVICE_REQUEST_CLOSED_VISIBLE_DAYS = 365
    shown, data = listed(api(world.users["super"]))
    assert shown == set(five.values())
    assert data["closed_visible_days"] == 365


def test_the_citizens_own_list_keeps_old_finished_requests(world, request_from, settings):
    settings.SERVICE_REQUEST_CLOSED_VISIBLE_DAYS = 180
    citizen, request_id = request_from()
    age(request_id, "COMPLETED", 400)
    mine = citizen.client.get(MINE).json()
    rows = mine["results"] if isinstance(mine, dict) else mine
    assert request_id in {row["id"] for row in rows}
