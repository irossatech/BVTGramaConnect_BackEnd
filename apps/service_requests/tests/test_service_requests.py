"""Citizens ask for a service; the request goes to their Village Admin. Contract section 16."""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenDevice
from apps.notifications.models import Device, Notification, NotificationKind
from apps.notifications.push import LocmemPushGateway
from apps.service_requests.models import Service, ServiceRequest, ServiceRequestEvent
from conftest import TEST_PASSWORD

from .conftest import LIST, MINE, ask

pytestmark = pytest.mark.django_db

REVIEWERS = {"va_kolakaluru", "ma_tenali", "da_guntur", "super"}
OTHERS = {"va_nandivelugu", "va_kaza", "ma_mangalagiri", "da_krishna"}
PERSONAL = ("9876543210", "Lakshmi", "lakshmi@", "Temple Street")


def code(response):
    return response.json()["error"]["code"]


def detail(api, world, name, request_id):
    return api(world.users[name]).get(f"{LIST}{request_id}/")


def patch(api, world, name, request_id, **body):
    return api(world.users[name]).patch(f"{LIST}{request_id}/", body, format="json")


# --- Citizens --------------------------------------------------------------------------------------


def test_a_citizen_asks_for_a_service_for_their_village(world, phone):
    citizen = phone("kolakaluru")
    response = ask(citizen)
    assert response.status_code == 201
    data = response.json()
    assert isinstance(data["id"], int)
    assert (data["service"], data["service_name"], data["status"], data["note"]) == ("aadhaar", "ఆధార్ సేవలు", "NEW", "")
    assert (data["name"], data["phone"], data["email"]) == ("Lakshmi Devi", "9876543210", "lakshmi@example.com")
    assert data["address"] == "4-12, Temple Street, Kolakaluru" and data["details"] == ""
    assert data["village"] == {"id": world.areas["kolakaluru"].id, "name": "కొలకలూరు"}
    assert data["mandal"] == {"id": world.areas["tenali"].id, "name": "తెనాలి"}
    assert data["district"] == {"id": world.areas["guntur"].id, "name": "గుంటూరు"}
    assert data["created_at"].endswith("+05:30")
    assert "assigned_to" not in data and "history" not in data  # officials only
    stored = ServiceRequest.objects.get()
    assert stored.village_id == world.areas["kolakaluru"].id and str(stored.device_id) == citizen.device_id


def test_email_is_optional_and_names_follow_the_language(world, phone):
    response = phone().client.post(
        MINE, {"service": "pan", "name": "  Ravi   Kumar ", "phone": "98765 43210", "address": " 1-2, Main Road "},
        format="json", HTTP_ACCEPT_LANGUAGE="en",
    )
    assert response.status_code == 201
    data = response.json()
    assert (data["name"], data["phone"], data["email"], data["address"]) == ("Ravi Kumar", "9876543210", "", "1-2, Main Road")
    assert data["service_name"] == "PAN Services" and data["village"]["name"] == "Kolakaluru"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"service": "passport"}, "service"),
        ({"name": "A"}, "name"),
        ({"phone": "5876543210"}, "phone"),
        ({"phone": "98765"}, "phone"),
        ({"phone": "+919876543210"}, "phone"),
        ({"email": "not-an-email"}, "email"),
        ({"address": ""}, "address"),
        ({"address": " 4-1 "}, "address"),
        ({"address": "x" * 301}, "address"),
        ({"details": "x" * 501}, "details"),
    ],
)
def test_bad_fields_are_refused(world, phone, changes, field):
    response = ask(phone(), **changes)
    assert response.status_code == 400 and field in response.json()["error"]["fields"]
    assert not ServiceRequest.objects.exists()


def test_the_address_is_required(world, phone):
    body = {"service": "aadhaar", "name": "Lakshmi Devi", "phone": "9876543210"}
    response = phone().client.post(MINE, body, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["fields"]["address"] == ["Enter your address."]


def test_a_hidden_service_cannot_be_asked_for(world, phone):
    Service.objects.filter(key="pan").update(is_active=False)
    response = ask(phone(), service="pan")
    assert response.status_code == 400
    assert response.json()["error"]["fields"]["service"] == ["This service is not available."]


def test_a_phone_sees_only_its_own_requests_newest_first(world, phone):
    mine, other = phone(), phone(ip="10.1.1.1")
    ask(mine, service="aadhaar")
    ask(mine, service="pan")
    ask(other, service="voter_id")
    rows = mine.client.get(MINE).json()
    assert [row["service"] for row in rows] == ["pan", "aadhaar"]


def test_old_requests_stay_readable_after_the_service_is_hidden(world, phone):
    citizen = phone()
    ask(citizen, service="pan")
    Service.objects.filter(key="pan").update(is_active=False)
    assert citizen.client.get(MINE).json()[0]["service"] == "pan"


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
    assert eleventh.json()["error"]["retry_after_s"] > 0
    settings.SERVICE_REQUESTS_PER_DAY = 0  # 0 is no limit
    assert ask(citizen).status_code == 201


def test_creation_is_audited_without_personal_data(world, phone):
    citizen = phone()
    ask(citizen)
    entry = AuditLog.objects.get(action="service_request.created")
    assert entry.via == "citizen" and entry.data == {"service": "aadhaar", "citizen": citizen.device_id}
    for row in AuditLog.objects.filter(action__startswith="service_request."):
        text = str(row.__dict__)
        assert not any(value in text for value in PERSONAL), row.action


# --- Automatic assignment ---------------------------------------------------------------------------


def test_a_new_request_is_assigned_to_the_village_admin(api, world, request_from):
    _citizen, request_id = request_from("kolakaluru")
    admin = world.users["va_kolakaluru"]
    data = detail(api, world, "ma_tenali", request_id).json()
    assert data["status"] == "NEW"
    assert data["assigned_to"] == {"id": str(admin.id), "name": admin.name, "role": "VILLAGE_ADMIN"}
    (event,) = data["history"]
    assert event["by"] is None and "status" not in event
    assert event["assigned_to"] == {"id": str(admin.id), "name": admin.name}
    assert event["note"] == "Assigned to the Village Admin automatically"
    entry = AuditLog.objects.get(action="service_request.assigned")
    assert entry.data == {"to": str(admin.id), "to_name": admin.name, "auto": True}


def test_without_an_active_village_admin_it_stays_unassigned(api, world, request_from):
    RoleAssignment.objects.filter(user=world.users["va_kolakaluru"]).update(is_active=False)
    _citizen, request_id = request_from("kolakaluru")
    data = detail(api, world, "ma_tenali", request_id).json()
    assert data["assigned_to"] is None and data["history"] == []
    assert not AuditLog.objects.filter(action="service_request.assigned").exists()


def test_a_blocked_village_admin_is_skipped(api, world, request_from):
    User.objects.filter(pk=world.users["va_kolakaluru"].pk).update(is_active=False)
    _citizen, request_id = request_from("kolakaluru")
    assert ServiceRequest.objects.get(pk=request_id).assigned_to is None


def test_a_panchayat_member_or_a_higher_admin_is_never_auto_assigned(world, request_from):
    RoleAssignment.objects.filter(user=world.users["va_kolakaluru"]).delete()
    _citizen, request_id = request_from("kolakaluru")  # pm_kolakaluru and ma_tenali are still there
    assert ServiceRequest.objects.get(pk=request_id).assigned_to is None


def test_with_two_village_admins_the_earlier_assignment_wins(world, request_from):
    second = User.objects.create_user(username="va.second", name="Second Admin", password=TEST_PASSWORD)
    RoleAssignment.objects.assign(second, "VILLAGE_ADMIN", world.ref("kolakaluru"))
    _c, first_id = request_from("kolakaluru")
    assert ServiceRequest.objects.get(pk=first_id).assigned_to == world.users["va_kolakaluru"]

    first_assignment = RoleAssignment.objects.get(user=world.users["va_kolakaluru"])
    RoleAssignment.objects.filter(user=second).update(assigned_at=first_assignment.assigned_at - timedelta(days=1))
    _c, second_id = request_from("kolakaluru", ip="10.9.9.9")
    assert ServiceRequest.objects.get(pk=second_id).assigned_to == second


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


def test_the_officials_view_has_the_citizens_details_and_actions(api, world, request_from):
    _citizen, request_id = request_from()
    data = detail(api, world, "va_kolakaluru", request_id).json()
    assert data["allowed_actions"] == ["set_status", "assign"]
    assert data["phone"] == "9876543210" and data["address"] == "4-12, Temple Street, Kolakaluru"


def test_the_areas_admins_get_a_notification_and_the_assignee_hears_it_is_theirs(world, request_from):
    for name in ("va_kolakaluru", "ma_tenali", "da_guntur", "super"):
        Device.objects.create(fcm_token=f"tok-{name}", platform="android", user=world.users[name])
    LocmemPushGateway.outbox.clear()
    _citizen, request_id = request_from()
    notified = {n.user.username for n in Notification.objects.filter(kind=NotificationKind.SERVICE_REQUEST)}
    assert notified == {"va_kolakaluru", "ma_tenali", "da_guntur"}  # the Super Admin sees the list instead
    mine = Notification.objects.get(user=world.users["va_kolakaluru"])
    assert mine.title == "Lakshmi Devi నుండి కొత్త ఆధార్ సేవలు అభ్యర్థన, మీకు అప్పగించబడింది"
    assert mine.service_request_id == request_id
    other = Notification.objects.get(user=world.users["ma_tenali"])
    assert other.title == "కొలకలూరు: Lakshmi Devi నుండి కొత్త ఆధార్ సేవలు అభ్యర్థన"
    for note in Notification.objects.all():
        assert "9876543210" not in note.title + note.body and "Temple" not in note.title + note.body
    pushes = [m for m in LocmemPushGateway.outbox if m.token]
    assert {m.token for m in pushes} == {"tok-va_kolakaluru", "tok-ma_tenali", "tok-da_guntur"}
    assert {m.data["type"] for m in pushes} == {"SERVICE_REQUEST"}
    assert {m.data["request_id"] for m in pushes} == {str(request_id)}


def test_the_inbox_names_the_request(api, world, request_from):
    _citizen, request_id = request_from()
    rows = api(world.users["va_kolakaluru"]).get("/api/v1/notifications/").json()["results"]
    assert rows[0]["kind"] == "SERVICE_REQUEST" and rows[0]["service_request_id"] == request_id


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
    event = data["history"][-1]
    assert event["status"] == "APPROVED" and event["assigned_to"] == {"id": str(member.id), "name": member.name}
    assert event["by"]["role"] == "VILLAGE_ADMIN" and event["note"] == "Ravi will help you"
    mine = citizen.client.get(MINE).json()[0]
    assert mine["status"] == "APPROVED" and mine["note"] == "Ravi will help you"
    assert AuditLog.objects.get(action="service_request.status_changed").data["to"] == "APPROVED"
    assert AuditLog.objects.get(action="service_request.assigned", data__auto=False).data["to"] == str(member.id)


def test_the_new_assignee_is_told(api, world, request_from, django_capture_on_commit_callbacks):
    _citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    with django_capture_on_commit_callbacks(execute=True):
        patch(api, world, "va_kolakaluru", request_id, assigned_to=str(member.id))
    note = Notification.objects.get(user=member)
    assert note.kind == NotificationKind.SERVICE_REQUEST and note.service_request_id == request_id
    assert "మీకు అప్పగించబడింది" in note.title
    before = Notification.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        patch(api, world, "ma_tenali", request_id, assigned_to=str(world.users["ma_tenali"].id))
    assert Notification.objects.count() == before  # assigning yourself tells nobody


def test_history_lists_only_what_changed_oldest_first(api, world, request_from):
    _citizen, request_id = request_from()
    patch(api, world, "va_kolakaluru", request_id, status="APPROVED")
    patch(api, world, "va_kolakaluru", request_id, assigned_to=str(world.users["pm_kolakaluru"].id))
    patch(api, world, "va_kolakaluru", request_id, note="Bring your ration card")
    patch(api, world, "va_kolakaluru", request_id, assigned_to=None)
    history = detail(api, world, "va_kolakaluru", request_id).json()["history"]
    keys = [set(row) - {"at", "by", "note"} for row in history]
    assert keys == [{"assigned_to"}, {"status"}, {"assigned_to"}, set(), {"assigned_to"}]
    assert history[4]["assigned_to"] is None and history[3]["note"] == "Bring your ration card"
    assert ServiceRequestEvent.objects.count() == 5


def test_nothing_changed_writes_nothing(api, world, request_from):
    _citizen, request_id = request_from()
    assert patch(api, world, "va_kolakaluru", request_id, status="NEW").status_code == 200
    assert ServiceRequestEvent.objects.count() == 1  # only the automatic assignment


@pytest.mark.parametrize("assignee", ["pm_nandivelugu", "va_kaza", "da_krishna"])
def test_only_an_official_of_the_area_can_be_assigned(api, world, request_from, assignee):
    _citizen, request_id = request_from("kolakaluru")
    response = patch(api, world, "ma_tenali", request_id, assigned_to=str(world.users[assignee].id))
    assert response.status_code == 400 and "assigned_to" in response.json()["error"]["fields"]


def test_blocked_unknown_or_malformed_users_cannot_be_assigned(api, world, request_from):
    _citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    User.objects.filter(pk=member.pk).update(is_active=False)
    for value in (str(member.id), "00000000-0000-0000-0000-000000000000", "15"):
        response = patch(api, world, "va_kolakaluru", request_id, assigned_to=value)
        assert response.status_code == 400 and "assigned_to" in response.json()["error"]["fields"], value


def test_admins_may_assign_themselves_or_a_higher_official(api, world, request_from):
    _citizen, request_id = request_from()
    assert patch(api, world, "ma_tenali", request_id, assigned_to=str(world.users["ma_tenali"].id)).status_code == 200
    assert patch(api, world, "va_kolakaluru", request_id, assigned_to=str(world.users["da_guntur"].id)).status_code == 200


def test_rejecting_needs_a_reason_the_citizen_then_sees(api, world, request_from):
    citizen, request_id = request_from()
    refused = patch(api, world, "da_guntur", request_id, status="REJECTED")
    assert refused.status_code == 400 and "note" in refused.json()["error"]["fields"]
    assert patch(api, world, "da_guntur", request_id, status="REJECTED", note="  ").status_code == 400
    response = patch(api, world, "da_guntur", request_id, status="REJECTED", note="Please apply at the Mee Seva centre")
    assert response.status_code == 200
    mine = citizen.client.get(MINE).json()[0]
    assert (mine["status"], mine["note"]) == ("REJECTED", "Please apply at the Mee Seva centre")


def test_completing_with_a_description(api, world, request_from):
    citizen, request_id = request_from()
    patch(api, world, "va_kolakaluru", request_id, status="IN_PROGRESS")
    response = patch(api, world, "va_kolakaluru", request_id, status="COMPLETED", note="Card updated, collect it")
    assert response.status_code == 200
    assert citizen.client.get(MINE).json()[0]["note"] == "Card updated, collect it"


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
    loner = User.objects.create_user(username="loner", name="Loner", password=TEST_PASSWORD)
    RoleAssignment.objects.filter(user=loner).delete()
    assert api(loner).get(LIST).status_code == 403


# --- Lists ----------------------------------------------------------------------------------------


def test_status_counts_and_filters(api, world, request_from):
    _c, first = request_from(name="Lakshmi Devi", phone="9876543210")
    _c, second = request_from(ip="10.3.3.3", name="Ravi Kumar", phone="9123456780")
    _c, third = request_from(ip="10.4.4.4", name="Sita", phone="9000000001")
    admin = api(world.users["ma_tenali"])
    patch(api, world, "ma_tenali", second, status="APPROVED", assigned_to=str(world.users["ma_tenali"].id))
    patch(api, world, "ma_tenali", third, status="REJECTED", note="Duplicate")

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
    assert message.title == "Your Aadhaar Services request: Rejected" and message.body == "Apply at Mee Seva"
    assert message.data == {"type": "SERVICE_REQUEST_UPDATED", "request_id": str(request_id), "status": "REJECTED"}
    assert not any(value in message.title + message.body for value in PERSONAL)
