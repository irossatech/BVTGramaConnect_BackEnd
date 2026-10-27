"""Citizens ask for help with a service; the officials of their village handle it. Contract section 16."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenDevice
from apps.notifications.models import Device, Notification, NotificationKind
from apps.notifications.push import LocmemPushGateway
from apps.service_requests.models import ServiceRequest, ServiceRequestEvent

from .conftest import LIST, MINE, ask

pytestmark = pytest.mark.django_db

REVIEWERS = {"va_kolakaluru", "ma_tenali", "da_guntur", "super"}
OTHERS = {"va_nandivelugu", "va_kaza", "ma_mangalagiri", "da_krishna"}


def code(response):
    return response.json()["error"]["code"]


def detail(api, world, name, request_id):
    return api(world.users[name]).get(f"{LIST}{request_id}/")


def patch(api, world, name, request_id, **body):
    return api(world.users[name]).patch(f"{LIST}{request_id}/", body, format="json")


# --- Citizens --------------------------------------------------------------------------------------


def test_a_citizen_asks_for_help_for_their_village(world, phone):
    citizen = phone("kolakaluru")
    response = ask(citizen)
    assert response.status_code == 201
    data = response.json()
    assert isinstance(data["id"], int)
    assert data["service"] == "aadhaar" and data["status"] == "NEW" and data["note"] == ""
    assert (data["name"], data["phone"], data["email"]) == ("Lakshmi Devi", "9876543210", "lakshmi@example.com")
    assert data["village"] == {"id": world.areas["kolakaluru"].id, "name": "కొలకలూరు"}
    assert data["mandal"] == {"id": world.areas["tenali"].id, "name": "తెనాలి"}
    assert data["district"] == {"id": world.areas["guntur"].id, "name": "గుంటూరు"}
    assert data["created_at"].endswith("+05:30")
    stored = ServiceRequest.objects.get()
    assert stored.village_id == world.areas["kolakaluru"].id and str(stored.device_id) == citizen.device_id


def test_email_and_details_are_optional_and_names_follow_the_language(world, phone):
    citizen = phone()
    response = citizen.client.post(
        MINE, {"service": "pan", "name": "  Ravi   Kumar ", "phone": "98765 43210"}, format="json",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert response.status_code == 201
    data = response.json()
    assert (data["name"], data["phone"], data["email"], data["details"]) == ("Ravi Kumar", "9876543210", "", "")
    assert data["village"]["name"] == "Kolakaluru"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"service": "passport"}, "service"),
        ({"name": "A"}, "name"),
        ({"phone": "5876543210"}, "phone"),
        ({"phone": "98765"}, "phone"),
        ({"phone": "+919876543210"}, "phone"),
        ({"email": "not-an-email"}, "email"),
        ({"details": "x" * 501}, "details"),
    ],
)
def test_bad_fields_are_refused(world, phone, changes, field):
    response = ask(phone(), **changes)
    assert response.status_code == 400 and field in response.json()["error"]["fields"]
    assert not ServiceRequest.objects.exists()


def test_a_phone_sees_only_its_own_requests_newest_first(world, phone):
    mine, other = phone(), phone(ip="10.1.1.1")
    ask(mine, service="aadhaar")
    ask(mine, service="pan")
    ask(other, service="voter_id")
    rows = mine.client.get(MINE).json()
    assert [row["service"] for row in rows] == ["pan", "aadhaar"]


def test_every_call_needs_the_phones_token(world):
    for response in (APIClient().get(MINE), APIClient().post(MINE, {}, format="json")):
        assert response.status_code == 401 and code(response) == "citizen_token_invalid"


def test_a_blocked_phone_cannot_ask(world, phone):
    citizen = phone()
    CitizenDevice.objects.update(block_reason="Spam", blocked_at="2026-10-27T10:00:00+05:30")
    response = ask(citizen)
    assert response.status_code == 403 and code(response) == "citizen_blocked"
    assert response.json()["error"]["reason"] == "Spam"


def test_ten_requests_a_day_per_phone(world, phone, settings):
    citizen = phone()
    for _ in range(10):
        assert ask(citizen).status_code == 201
    eleventh = ask(citizen)
    assert eleventh.status_code == 429 and code(eleventh) == "throttled"
    settings.SERVICE_REQUESTS_PER_DAY = 0  # 0 is no limit
    assert ask(citizen).status_code == 201


def test_creation_is_audited_without_personal_data(world, phone):
    citizen = phone()
    ask(citizen)
    entry = AuditLog.objects.get(action="service_request.created")
    assert entry.via == "citizen" and entry.data == {"service": "aadhaar", "citizen": citizen.device_id}
    text = str(entry.__dict__)
    assert "9876543210" not in text and "Lakshmi" not in text and "lakshmi@" not in text


# --- Who sees it ------------------------------------------------------------------------------------


def test_the_villages_admins_and_the_super_admin_see_it_nobody_else(api, world, request_from):
    _citizen, request_id = request_from("kolakaluru")
    for name in REVIEWERS:
        ids = {row["id"] for row in api(world.users[name]).get(LIST).json()["results"]}
        assert request_id in ids, name
        assert detail(api, world, name, request_id).status_code == 200, name
    for name in OTHERS:
        ids = {row["id"] for row in api(world.users[name]).get(LIST).json()["results"]}
        assert request_id not in ids, name
        assert detail(api, world, name, request_id).status_code == 404, name


def test_the_officials_view_has_assignee_actions_and_history(api, world, request_from):
    _citizen, request_id = request_from()
    data = detail(api, world, "va_kolakaluru", request_id).json()
    assert data["assigned_to"] is None and data["history"] == []
    assert data["allowed_actions"] == ["set_status", "assign"]
    assert data["phone"] == "9876543210"  # officials may call the citizen


def test_the_areas_admins_get_a_notification(world, request_from):
    for name in ("va_kolakaluru", "ma_tenali", "da_guntur", "super"):
        Device.objects.create(fcm_token=f"tok-{name}", platform="android", user=world.users[name])
    LocmemPushGateway.outbox.clear()
    _citizen, request_id = request_from()
    notified = {n.user.username for n in Notification.objects.filter(kind=NotificationKind.SERVICE_REQUEST)}
    assert notified == {"va_kolakaluru", "ma_tenali", "da_guntur"}  # the Super Admin sees the list instead
    note = Notification.objects.get(user=world.users["va_kolakaluru"])
    assert note.title == "కొలకలూరు: Lakshmi Devi నుండి కొత్త ఆధార్ అభ్యర్థన"
    assert note.service_request_id == request_id
    assert "9876543210" not in note.title + note.body
    pushes = [m for m in LocmemPushGateway.outbox if m.token]
    assert {m.token for m in pushes} == {"tok-va_kolakaluru", "tok-ma_tenali", "tok-da_guntur"}
    assert {m.data["type"] for m in pushes} == {"SERVICE_REQUEST"}
    assert {m.data["request_id"] for m in pushes} == {str(request_id)}


# --- Officials change it ---------------------------------------------------------------------------


def test_an_admin_approves_and_assigns_a_panchayat_member(api, world, request_from):
    citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    response = patch(api, world, "va_kolakaluru", request_id, status="APPROVED", assigned_to=str(member.id),
                     note="Ravi will help you")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "APPROVED" and data["note"] == "Ravi will help you"
    assert data["assigned_to"] == {"id": str(member.id), "name": member.name, "role": "PANCHAYAT_MEMBER"}
    (event,) = data["history"]
    assert event["status"] == "APPROVED" and event["assigned_to"] == {"id": str(member.id), "name": member.name}
    assert event["by"]["role"] == "VILLAGE_ADMIN" and event["note"] == "Ravi will help you"
    mine = citizen.client.get(MINE).json()[0]
    assert mine["status"] == "APPROVED" and mine["note"] == "Ravi will help you"
    assert AuditLog.objects.get(action="service_request.status_changed").data["to"] == "APPROVED"
    assert AuditLog.objects.get(action="service_request.assigned").data["to"] == str(member.id)


def test_history_lists_only_what_changed_oldest_first(api, world, request_from):
    _citizen, request_id = request_from()
    patch(api, world, "va_kolakaluru", request_id, status="APPROVED")
    patch(api, world, "va_kolakaluru", request_id, assigned_to=str(world.users["pm_kolakaluru"].id))
    patch(api, world, "va_kolakaluru", request_id, note="Bring your ration card")
    patch(api, world, "va_kolakaluru", request_id, assigned_to=None)
    history = detail(api, world, "va_kolakaluru", request_id).json()["history"]
    assert [set(row) - {"at", "by", "note"} for row in history] == [{"status"}, {"assigned_to"}, set(), {"assigned_to"}]
    assert history[3]["assigned_to"] is None and history[2]["note"] == "Bring your ration card"
    assert ServiceRequestEvent.objects.count() == 4


def test_nothing_changed_writes_nothing(api, world, request_from):
    _citizen, request_id = request_from()
    assert patch(api, world, "va_kolakaluru", request_id, status="NEW").status_code == 200
    assert not ServiceRequestEvent.objects.exists()


@pytest.mark.parametrize("assignee", ["pm_nandivelugu", "va_kaza", "da_krishna"])
def test_only_an_official_of_the_area_can_be_assigned(api, world, request_from, assignee):
    _citizen, request_id = request_from("kolakaluru")
    response = patch(api, world, "ma_tenali", request_id, assigned_to=str(world.users[assignee].id))
    assert response.status_code == 400 and "assigned_to" in response.json()["error"]["fields"]


def test_blocked_or_unknown_users_cannot_be_assigned(api, world, request_from):
    _citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    User.objects.filter(pk=member.pk).update(is_active=False)
    assert patch(api, world, "va_kolakaluru", request_id, assigned_to=str(member.id)).status_code == 400
    unknown = "00000000-0000-0000-0000-000000000000"
    assert patch(api, world, "va_kolakaluru", request_id, assigned_to=unknown).status_code == 400


def test_admins_may_assign_themselves_or_a_higher_official(api, world, request_from):
    _citizen, request_id = request_from()
    assert patch(api, world, "va_kolakaluru", request_id, assigned_to=str(world.users["va_kolakaluru"].id)).status_code == 200
    assert patch(api, world, "va_kolakaluru", request_id, assigned_to=str(world.users["ma_tenali"].id)).status_code == 200


def test_rejecting_needs_a_note_the_citizen_then_sees(api, world, request_from):
    citizen, request_id = request_from()
    refused = patch(api, world, "da_guntur", request_id, status="REJECTED")
    assert refused.status_code == 400 and "note" in refused.json()["error"]["fields"]
    assert patch(api, world, "da_guntur", request_id, status="REJECTED", note="  ").status_code == 400
    response = patch(api, world, "da_guntur", request_id, status="REJECTED", note="Please apply at the Mee Seva centre")
    assert response.status_code == 200
    mine = citizen.client.get(MINE).json()[0]
    assert (mine["status"], mine["note"]) == ("REJECTED", "Please apply at the Mee Seva centre")


def test_unknown_status_is_refused(api, world, request_from):
    _citizen, request_id = request_from()
    response = patch(api, world, "va_kolakaluru", request_id, status="DONE")
    assert response.status_code == 400 and "status" in response.json()["error"]["fields"]


# --- The assigned Panchayat member ------------------------------------------------------------------


def test_a_panchayat_member_sees_only_requests_assigned_to_them(api, world, request_from):
    _c1, assigned = request_from()
    _c2, other = request_from(ip="10.2.2.2")
    member = world.users["pm_kolakaluru"]
    patch(api, world, "va_kolakaluru", assigned, assigned_to=str(member.id))
    ids = {row["id"] for row in api(member).get(LIST).json()["results"]}
    assert ids == {assigned}
    assert detail(api, world, "pm_kolakaluru", other).status_code == 404
    assert detail(api, world, "pm_kolakaluru", assigned).json()["allowed_actions"] == ["set_status"]


def test_the_assignee_moves_it_to_in_progress_and_completed_only(api, world, request_from):
    citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    patch(api, world, "va_kolakaluru", request_id, status="APPROVED", assigned_to=str(member.id))
    assert patch(api, world, "pm_kolakaluru", request_id, status="IN_PROGRESS", note="Visiting tomorrow").status_code == 200
    for refused in ({"status": "REJECTED", "note": "x"}, {"status": "APPROVED"}, {"status": "NEW"},
                    {"assigned_to": str(world.users["va_kolakaluru"].id)}):
        response = patch(api, world, "pm_kolakaluru", request_id, **refused)
        assert response.status_code == 403 and code(response) == "permission_denied", refused
    assert patch(api, world, "pm_kolakaluru", request_id, status="COMPLETED").status_code == 200
    event = ServiceRequestEvent.objects.filter(by=member).last()
    assert event.by_role == "PANCHAYAT_MEMBER"
    assert citizen.client.get(MINE).json()[0]["status"] == "COMPLETED"


def test_a_panchayat_member_cannot_change_an_unassigned_request(api, world, request_from):
    _citizen, request_id = request_from()
    assert patch(api, world, "pm_kolakaluru", request_id, status="IN_PROGRESS").status_code == 404


def test_officials_without_a_role_cannot_see_the_list(api, world):
    loner = User.objects.create_user(username="loner", name="Loner", password="Gram@2026x")
    RoleAssignment.objects.filter(user=loner).delete()
    assert api(loner).get(LIST).status_code == 403


# --- Lists ----------------------------------------------------------------------------------------


def test_status_counts_and_filters(api, world, request_from):
    _c, first = request_from(name="Lakshmi Devi", phone="9876543210")
    _c, second = request_from(ip="10.3.3.3", name="Ravi Kumar", phone="9123456780")
    _c, third = request_from(ip="10.4.4.4", name="Sita", phone="9000000001")
    admin = api(world.users["va_kolakaluru"])
    patch(api, world, "va_kolakaluru", second, status="APPROVED", assigned_to=str(world.users["va_kolakaluru"].id))
    patch(api, world, "va_kolakaluru", third, status="REJECTED", note="Duplicate")

    data = admin.get(f"{LIST}?status=APPROVED").json()
    assert [row["id"] for row in data["results"]] == [second]
    assert data["status_counts"] == {"NEW": 1, "APPROVED": 1, "IN_PROGRESS": 0, "COMPLETED": 0, "REJECTED": 1}
    assert [row["id"] for row in admin.get(f"{LIST}?assigned=me").json()["results"]] == [second]
    assert [row["id"] for row in admin.get(f"{LIST}?q=ravi").json()["results"]] == [second]
    assert [row["id"] for row in admin.get(f"{LIST}?q=98765").json()["results"]] == [first]
    assert admin.get(f"{LIST}?q=ravi").json()["status_counts"]["APPROVED"] == 1
    newest = [row["id"] for row in admin.get(LIST).json()["results"]]
    assert newest[0] == third  # the latest change first
    assert admin.get(f"{LIST}?status=DONE").status_code == 400


# --- The citizen hears about status changes ----------------------------------------------------------


def test_the_citizen_gets_a_push_on_each_status_change(api, world, phone, django_capture_on_commit_callbacks):
    citizen = phone(fcm_token="fcm-citizen", language="en")
    with django_capture_on_commit_callbacks(execute=True):
        request_id = ask(citizen).json()["id"]
    LocmemPushGateway.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        patch(api, world, "va_kolakaluru", request_id, note="Just a note")  # no status change: no push
        patch(api, world, "va_kolakaluru", request_id, status="REJECTED", note="Apply at Mee Seva")
    to_phone = [m for m in LocmemPushGateway.outbox if m.token == "fcm-citizen"]
    assert len(to_phone) == 1
    message = to_phone[0]
    assert message.title == "Your Aadhaar request: Rejected" and message.body == "Apply at Mee Seva"
    assert message.data == {"type": "SERVICE_REQUEST_UPDATED", "request_id": str(request_id), "status": "REJECTED"}
