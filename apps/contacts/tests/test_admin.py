"""The officials' API: validation, duplicates, cap, consent, soft delete, verify, targets, types, audit."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.contacts.models import Contact, ContactType
from apps.geography.models import Village

from .conftest import ADMIN_URL, area_body, helpline_body

pytestmark = pytest.mark.django_db
TYPES_URL = "/api/v1/admin/contact-types/"


@pytest.fixture
def admin(world, api):
    return api(world.users["super"])


def errors(response) -> dict:
    return response.json()["error"].get("fields", {})


# --- Validation ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, field",
    [
        ({"phone": "12"}, "phone"),
        ({"phone": "*#06#"}, "phone"),
        ({"phone": "5000010001"}, "phone"),
        ({"phone": "108"}, "phone"),  # a short code on an area number
        ({"phone": ""}, "phone"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 151}, "name"),
        ({"type": "pizza"}, "type"),
        ({"type": "health"}, "area"),  # a helpline type below the state
        ({"area": {"level": "VILLAGE", "id": 999999}}, "area"),
        ({"area": {"level": "TOWN", "id": 1}}, "area"),
        ({"email": "not-an-email"}, "email"),
        ({"custom_type": "Anganwadi"}, "custom_type"),  # only "Other" types take one
        ({"type": "other"}, "custom_type"),  # "Other" needs one
        ({"type": "other", "custom_type": "x" * 41}, "custom_type"),
        ({"sort_order": 1001}, "sort_order"),
    ],
)
def test_validation(world, admin, change, field):
    response = admin.post(ADMIN_URL, {**area_body(world, "kolakaluru"), **change}, format="json")
    assert response.status_code == 400, response.json()
    assert field in errors(response)
    assert Contact.objects.count() == 0


def test_an_area_number_cannot_sit_at_state_level(world, admin):
    response = admin.post(ADMIN_URL, area_body(world, "ap"), format="json")
    assert response.status_code == 400 and "area" in errors(response)


def test_inactive_area_is_refused(world, admin):
    Village.objects.filter(pk=world.ref("kolakaluru").id).update(is_active=False)
    response = admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json")
    assert response.status_code == 400 and "area" in errors(response)


def test_switched_off_type_is_refused(world, admin):
    ContactType.objects.filter(key="official").update(is_active=False)
    response = admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json")
    assert response.status_code == 400 and "type" in errors(response)


def test_mobiles_need_consent_unless_switched_off(world, admin, settings):
    body = area_body(world, "kolakaluru", type="vro", name="K. Srinivasa Rao", phone="90000 10001")
    refused = admin.post(ADMIN_URL, body, format="json")
    assert refused.status_code == 400 and "consent_recorded" in errors(refused)
    created = admin.post(ADMIN_URL, {**body, "consent_recorded": True}, format="json")
    assert created.status_code == 201 and created.json()["phone"] == "9000010001"
    settings.CONTACTS_REQUIRE_MOBILE_CONSENT = False
    other = {**body, "phone": "9000010002"}
    assert admin.post(ADMIN_URL, other, format="json").status_code == 201


def test_duplicates_are_409_but_the_same_number_may_serve_another_type_or_area(world, admin):
    assert admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").status_code == 201
    again = admin.post(ADMIN_URL, area_body(world, "kolakaluru", phone="08644-222-333"), format="json")
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate" and "phone" in errors(again)
    assert admin.post(ADMIN_URL, area_body(world, "nandivelugu"), format="json").status_code == 201
    assert admin.post(ADMIN_URL, area_body(world, "kolakaluru", type="revenue"), format="json").status_code == 201


def test_a_removed_number_can_be_added_again(world, admin):
    first = admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").json()
    admin.delete(f"{ADMIN_URL}{first['id']}/")
    assert admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").status_code == 201


def test_per_area_cap(world, admin, settings, number):
    settings.CONTACTS_MAX_PER_AREA = 2
    number("kolakaluru", phone="08644222301")
    number("kolakaluru", phone="08644222302")
    full = admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json")
    assert full.status_code == 400 and "area" in errors(full)
    assert admin.post(ADMIN_URL, area_body(world, "nandivelugu"), format="json").status_code == 201


def test_cleaning_telugu_and_email(world, admin):
    body = area_body(world, "kolakaluru", name="  Tahsildar   Office ", name_te="తహసీల్దార్ కార్యాలయం", email=" MRO@Example.IN ")
    data = admin.post(ADMIN_URL, body, format="json").json()
    assert (data["name"], data["name_te"], data["email"]) == ("Tahsildar Office", "తహసీల్దార్ కార్యాలయం", "mro@example.in")
    assert Contact.objects.get().name_te == "తహసీల్దార్ కార్యాలయం"


def test_helplines_take_short_codes(world, admin):
    created = admin.post(ADMIN_URL, helpline_body(world), format="json")
    assert created.status_code == 201
    assert (created.json()["phone_kind"], created.json()["type"]["section"]) == ("SHORT_CODE", "HELPLINE")


# --- Editing, removing, verifying ---------------------------------------------------------------------


def test_admin_shape(world, api):
    actor = world.users["ma_tenali"]
    data = api(actor).post(ADMIN_URL, area_body(world, "tenali"), format="json").json()
    assert set(data) == {
        "id", "type", "custom_type", "name", "name_te", "phone", "phone_kind", "email", "area", "sort_order",
        "consent_recorded", "verified_at", "needs_recheck", "created_by", "created_at", "updated_at", "allowed_actions",
    }
    assert data["type"] == {"key": "official", "section": "AREA", "name": "ప్రభుత్వ అధికారి"}
    assert data["area"]["path"] == "తెనాలి · గుంటూరు"
    assert data["created_by"] == {"id": str(actor.id), "name": actor.name}
    assert data["verified_at"] is not None and data["needs_recheck"] is False
    assert data["allowed_actions"] == ["edit", "remove", "verify"]


def test_edit_revalidates_changed_fields(world, admin, number):
    contact = number("kolakaluru")
    url = f"{ADMIN_URL}{contact.id}/"
    assert admin.patch(url, {"phone": "12"}, format="json").status_code == 400
    assert admin.patch(url, {"type": "other"}, format="json").status_code == 400  # needs custom_type
    changed = admin.patch(url, {"type": "other", "custom_type": "Anganwadi"}, format="json")
    assert changed.status_code == 200 and changed.json()["custom_type"] == "Anganwadi"
    back = admin.patch(url, {"type": "official"}, format="json")
    assert back.status_code == 200 and back.json()["custom_type"] == ""  # cleared with the type
    number("kolakaluru", "official", "Twin", phone="08644222399")
    clash = admin.patch(url, {"phone": "08644222399"}, format="json")
    assert clash.status_code == 409


def test_remove_is_a_soft_delete_and_a_second_remove_is_404(world, admin, number):
    contact = number("kolakaluru")
    url = f"{ADMIN_URL}{contact.id}/"
    assert admin.delete(url).status_code == 204
    contact.refresh_from_db()
    assert contact.is_active is False and contact.removed_by == world.users["super"] and contact.removed_at
    assert admin.delete(url).status_code == 404
    assert admin.get(url).status_code == 404
    assert admin.patch(url, {"name": "Back"}, format="json").status_code == 404


def test_verify_and_needs_recheck(world, admin, number, settings):
    contact = number("kolakaluru", verified_at=timezone.now() - timedelta(days=settings.CONTACTS_VERIFY_DAYS + 1))
    url = f"{ADMIN_URL}{contact.id}/"
    assert admin.get(url).json()["needs_recheck"] is True
    verified = admin.patch(url, {"verified": True}, format="json").json()
    assert verified["needs_recheck"] is False
    assert number("tenali").verified_at is None and admin.get(f"{ADMIN_URL}{Contact.objects.get(area_level='MANDAL').id}/").json()["needs_recheck"]


def test_unknown_ids_are_404(world, admin):
    assert admin.get(f"{ADMIN_URL}00000000-0000-0000-0000-000000000000/").status_code == 404
    assert admin.get(f"{ADMIN_URL}not-a-uuid/").status_code == 404


# --- Lists and targets ------------------------------------------------------------------------------


def test_list_filters(world, admin, number):
    number("kolakaluru", "vro", "K. Srinivasa Rao", "9000010001")
    number("kaza", "official", "Kaza office")
    number("tenali", "other", "Anganwadi Tenali", custom_type="Anganwadi")
    number("ap", "health", "Ambulance", "108")

    def names(**params):
        return {r["name"] for r in admin.get(ADMIN_URL, {"page_size": 100, **params}).json()["results"]}

    assert names() == {"K. Srinivasa Rao", "Kaza office", "Anganwadi Tenali"}  # AREA by default
    assert names(section="HELPLINE") == {"Ambulance"}
    assert names(level="MANDAL", area_id=world.ref("tenali").id) == {"K. Srinivasa Rao", "Anganwadi Tenali"}
    assert names(type="vro") == {"K. Srinivasa Rao"}
    assert names(q="anganwadi") == {"Anganwadi Tenali"}
    assert names(q="9000 0100") == {"K. Srinivasa Rao"}  # digits of the phone
    assert admin.get(ADMIN_URL, {"section": "NOPE"}).status_code == 400
    assert admin.get(ADMIN_URL, {"level": "MANDAL"}).status_code == 400


def test_area_admins_see_helplines_read_only(world, api, number):
    number("ap", "health", "Ambulance", "108")
    rows = api(world.users["va_kolakaluru"]).get(ADMIN_URL, {"section": "HELPLINE"}).json()["results"]
    assert [(r["name"], r["allowed_actions"]) for r in rows] == [("Ambulance", [])]


@pytest.mark.parametrize(
    "actor, levels",
    [
        ("super", ["DISTRICT", "MANDAL", "VILLAGE"]),  # never STATE: state numbers are helplines
        ("da_guntur", ["DISTRICT", "MANDAL", "VILLAGE"]),
        ("ma_tenali", ["MANDAL", "VILLAGE"]),
        ("va_kolakaluru", ["VILLAGE"]),
        ("pm_kolakaluru", ["VILLAGE"]),
    ],
)
def test_targets(world, api, actor, levels):
    data = api(world.users[actor]).get(f"{ADMIN_URL}targets/").json()
    assert [entry["level"] for entry in data["levels"]] == levels
    if actor == "ma_tenali":
        villages = next(e for e in data["levels"] if e["level"] == "VILLAGE")
        assert {a["id"] for a in villages["areas"]} == {world.ref("kolakaluru").id, world.ref("nandivelugu").id}
    if actor == "pm_kolakaluru":
        assert data["levels"][0]["areas"][0]["id"] == world.ref("kolakaluru").id


# --- Audit ----------------------------------------------------------------------------------------------


def test_every_write_is_audited_with_masked_phones(world, api):
    client = api(world.users["ma_tenali"])
    body = area_body(world, "kolakaluru", type="vro", name="K. Srinivasa Rao", phone="9000010001", consent_recorded=True)
    contact_id = client.post(ADMIN_URL, body, format="json").json()["id"]
    client.patch(f"{ADMIN_URL}{contact_id}/", {"phone": "9000010009"}, format="json")
    client.patch(f"{ADMIN_URL}{contact_id}/", {"name": "K. Srinivasa Rao"}, format="json")  # no change: no entry
    client.delete(f"{ADMIN_URL}{contact_id}/")
    entries = list(AuditLog.objects.filter(action__startswith="contact.").order_by("created_at"))
    assert [e.action for e in entries] == ["contact.created", "contact.updated", "contact.removed"]
    assert entries[0].data == {"type": "vro", "phone": "******0001"}
    assert entries[1].data == {"type": "vro", "fields": ["phone"], "phone": {"from": "******0001", "to": "******0009"}}
    assert all(e.area_id == world.ref("kolakaluru").id and e.target_label == "K. Srinivasa Rao" for e in entries)
    assert not any("9000010" in str(e.data) for e in entries)


# --- Contact types ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_manages_types(world, api, actor):
    client = api(world.users[actor])
    assert client.get(TYPES_URL).status_code == 403
    body = {"key": "anganwadi", "section": "AREA", "name_en": "Anganwadi", "name_te": "అంగన్‌వాడీ"}
    assert client.post(TYPES_URL, body, format="json").status_code == 403


def test_super_admin_adds_renames_and_switches_off_types(world, admin, number):
    body = {"key": "anganwadi", "section": "AREA", "name_en": "Anganwadi", "name_te": "అంగన్‌వాడీ", "sort_order": 65}
    created = admin.post(TYPES_URL, body, format="json")
    assert created.status_code == 201 and created.json()["contact_count"] == 0
    assert admin.post(TYPES_URL, body, format="json").status_code == 409
    official = ContactType.objects.get(key="official")
    number("kolakaluru", "official")
    url = f"{TYPES_URL}{official.id}/"
    renamed = admin.patch(url, {"name_en": "Government office", "key": "changed", "section": "HELPLINE"}, format="json").json()
    assert (renamed["name_en"], renamed["key"], renamed["section"]) == ("Government office", "official", "AREA")
    assert admin.patch(url, {"is_active": False}, format="json").json()["is_active"] is False
    assert next(t for t in admin.get(TYPES_URL).json() if t["key"] == "official")["contact_count"] == 1
    assert admin.delete(url).status_code == 409  # in use: switched off, not deleted
    actions = list(AuditLog.objects.filter(action__startswith="contact_type.").values_list("action", flat=True))
    assert sorted(actions) == ["contact_type.created", "contact_type.updated", "contact_type.updated"]
