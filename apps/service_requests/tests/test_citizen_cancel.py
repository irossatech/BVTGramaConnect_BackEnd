"""A citizen cancels their own service request (owner's decision of 2026-11-18). Contract section 16.

While it is New, Approved or In progress; never once Completed, Rejected or Cancelled. Only the install
that sent it. The request is kept as Cancelled, final for everyone; the assignee is told; it still counts
towards the phone's daily limit.
"""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.notifications.models import Notification, NotificationKind
from apps.service_requests.models import ServiceRequest, ServiceRequestEvent

from .conftest import LIST, MINE, ask

pytestmark = pytest.mark.django_db


def cancel(citizen, request_id, **body):
    return citizen.client.post(f"{MINE}{request_id}/cancel/", body, format="json")


def official(api, world, name, request_id, method="get", **body):
    client = api(world.users[name])
    if method == "patch":
        return client.patch(f"{LIST}{request_id}/", body, format="json")
    return client.get(f"{LIST}{request_id}/")


def code(response):
    return response.json()["error"]["code"]


def test_a_citizen_cancels_a_new_request(api, world, request_from, django_capture_on_commit_callbacks):
    citizen, request_id = request_from()
    assert citizen.client.get(MINE).json()[0]["can_cancel"] is True
    with django_capture_on_commit_callbacks(execute=True):
        response = cancel(citizen, request_id, reason="  పని   అయిపోయింది ")
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "CANCELLED" and data["can_cancel"] is False and data["note"] == ""
    assert ServiceRequest.objects.filter(pk=request_id, status="CANCELLED").exists()  # kept, never deleted

    event = ServiceRequestEvent.objects.filter(request_id=request_id).last()
    assert event.by_citizen and event.by is None and event.status == "CANCELLED" and event.note == "పని అయిపోయింది"
    entry = AuditLog.objects.get(action="service_request.cancelled")
    assert entry.data == {"from": "NEW", "citizen": citizen.device_id, "reason_given": True}
    assert "అయిపోయింది" not in str(entry.data)  # the citizen's words stay out of the audit log

    assignee = world.users["va_kolakaluru"]
    note = Notification.objects.filter(user=assignee, kind=NotificationKind.SERVICE_REQUEST).latest("created_at")
    assert note.service_request_id == request_id and "రద్దు" in note.title  # the official's language is Telugu


def test_the_officials_see_who_cancelled_and_why(api, world, request_from):
    citizen, request_id = request_from()
    cancel(citizen, request_id, reason="No longer needed")
    data = official(api, world, "va_kolakaluru", request_id).json()
    assert data["status"] == "CANCELLED" and data["allowed_actions"] == []
    last = data["history"][-1]
    assert last == {"at": last["at"], "by": None, "by_citizen": True, "note": "No longer needed", "status": "CANCELLED"}


def test_without_a_reason_the_history_says_so(api, world, request_from):
    citizen, request_id = request_from()
    assert cancel(citizen, request_id).status_code == 200
    assert ServiceRequestEvent.objects.filter(request_id=request_id).last().note == "Cancelled by the citizen"
    assert AuditLog.objects.get(action="service_request.cancelled").data["reason_given"] is False


def test_a_reason_is_at_most_300_characters(request_from):
    citizen, request_id = request_from()
    response = cancel(citizen, request_id, reason="x" * 301)
    assert response.status_code == 400 and "reason" in response.json()["error"]["fields"]
    assert ServiceRequest.objects.get(pk=request_id).status == "NEW"


@pytest.mark.parametrize("status", ["APPROVED", "IN_PROGRESS"])
def test_it_can_be_cancelled_while_in_progress(api, world, request_from, status):
    citizen, request_id = request_from()
    assert official(api, world, "va_kolakaluru", request_id, "patch", status=status).status_code == 200
    response = cancel(citizen, request_id)
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    assert AuditLog.objects.get(action="service_request.cancelled").data["from"] == status


@pytest.mark.parametrize("status", ["COMPLETED", "REJECTED"])
def test_a_finished_request_cannot_be_cancelled(api, world, request_from, status):
    citizen, request_id = request_from()
    official(api, world, "va_kolakaluru", request_id, "patch", status=status, note="Done at the Sachivalayam")
    assert citizen.client.get(MINE).json()[0]["can_cancel"] is False
    response = cancel(citizen, request_id)
    assert response.status_code == 409 and code(response) == "invalid_transition"
    assert ServiceRequest.objects.get(pk=request_id).status == status


def test_cancelling_twice_is_refused(request_from):
    citizen, request_id = request_from()
    assert cancel(citizen, request_id).status_code == 200
    again = cancel(citizen, request_id)
    assert again.status_code == 409 and code(again) == "invalid_transition"
    assert ServiceRequestEvent.objects.filter(request_id=request_id, by_citizen=True).count() == 1


def test_only_the_install_that_sent_it_can_cancel(phone, request_from):
    _owner, request_id = request_from()
    other = phone("kolakaluru", ip="10.1.1.1")
    response = cancel(other, request_id)
    assert response.status_code == 404 and code(response) == "not_found"
    assert cancel(other, 999999).status_code == 404
    assert ServiceRequest.objects.get(pk=request_id).status == "NEW"


def test_without_a_token_nothing_happens(request_from):
    _citizen, request_id = request_from()
    response = APIClient().post(f"{MINE}{request_id}/cancel/", {}, format="json")
    assert response.status_code == 401
    assert ServiceRequest.objects.get(pk=request_id).status == "NEW"


def test_cancelled_is_final_for_the_officials(api, world, request_from):
    citizen, request_id = request_from()
    cancel(citizen, request_id)
    for body in ({"status": "IN_PROGRESS"}, {"note": "We will call you"},
                 {"assigned_to": str(world.users["pm_kolakaluru"].id)}):
        response = official(api, world, "va_kolakaluru", request_id, "patch", **body)
        assert response.status_code == 409 and code(response) == "invalid_transition", body
    assert ServiceRequest.objects.get(pk=request_id).status == "CANCELLED"


def test_officials_cannot_cancel(api, world, request_from):
    _citizen, request_id = request_from()
    response = official(api, world, "va_kolakaluru", request_id, "patch", status="CANCELLED")
    assert response.status_code == 400 and "status" in response.json()["error"]["fields"]
    assert ServiceRequest.objects.get(pk=request_id).status == "NEW"


def test_the_officials_list_has_a_cancelled_tab_and_keeps_old_ones(api, world, request_from):
    citizen, request_id = request_from()
    cancel(citizen, request_id)
    client = api(world.users["va_kolakaluru"])
    page = client.get(LIST, {"status": "CANCELLED"}).json()
    assert [row["id"] for row in page["results"]] == [request_id]
    assert page["status_counts"]["CANCELLED"] == 1
    # However old, it stays listed (decision of 2026-11-20: nothing is hidden by age).
    ServiceRequest.objects.filter(pk=request_id).update(updated_at=ServiceRequest.objects.get(pk=request_id).updated_at
                                                        - timedelta(days=400))
    page = client.get(LIST).json()
    assert request_id in [row["id"] for row in page["results"]] and page["status_counts"]["CANCELLED"] == 1


def test_a_cancelled_request_still_counts_towards_the_daily_limit(phone, settings):
    settings.SERVICE_REQUESTS_PER_DAY = 1
    citizen = phone("kolakaluru")
    first = ask(citizen)
    assert first.status_code == 201
    assert cancel(citizen, first.json()["id"]).status_code == 200
    second = ask(citizen)
    assert second.status_code == 429 and code(second) == "throttled"


def test_an_unassigned_request_is_cancelled_without_a_notification(world, request_from, django_capture_on_commit_callbacks):
    RoleAssignment.objects.filter(user=world.users["va_kolakaluru"]).update(is_active=False)
    citizen, request_id = request_from()
    before = Notification.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        assert cancel(citizen, request_id).status_code == 200
    assert Notification.objects.count() == before


def test_a_blocked_assignee_is_not_told(world, request_from, django_capture_on_commit_callbacks):
    citizen, request_id = request_from()
    User.objects.filter(pk=world.users["va_kolakaluru"].pk).update(is_active=False)
    before = Notification.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        assert cancel(citizen, request_id).status_code == 200
    assert Notification.objects.count() == before
