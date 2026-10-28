"""The services list the Super Admin manages and citizens see on Home. Contract section 16."""

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.service_requests.models import Service

from .conftest import ADMIN, SERVICES

pytestmark = pytest.mark.django_db

SEEDED = ["aadhaar", "pan", "voter_id", "certificates", "loans_insurance", "govt_schemes", "land_records", "farmer"]
NEW = {"name_en": "Ration Card", "name_te": "రేషన్ కార్డు", "icon": "ration", "color": "teal"}


def code(response):
    return response.json()["error"]["code"]


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def test_the_migration_seeds_the_apps_eight_services_in_order(world):
    data = APIClient().get(SERVICES).json()
    assert [row["key"] for row in data] == SEEDED
    assert data[0] == {"key": "aadhaar", "name_en": "Aadhaar Services", "name_te": "ఆధార్ సేవలు",
                       "icon": "aadhaar", "color": "orange", "is_active": True}


def test_the_public_list_is_cached_and_needs_no_login(world):
    response = APIClient().get(SERVICES)
    assert response.status_code == 200 and "max-age=300" in response["Cache-Control"]


def test_hiding_a_service_removes_it_from_home_only(admin, world):
    response = admin.patch(f"{ADMIN}pan/", {"is_active": False}, format="json")
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert "pan" not in [row["key"] for row in APIClient().get(SERVICES).json()]
    assert "pan" in [row["key"] for row in admin.get(ADMIN).json()]
    assert AuditLog.objects.filter(action="service.hidden").count() == 1
    admin.patch(f"{ADMIN}pan/", {"is_active": True}, format="json")
    assert AuditLog.objects.filter(action="service.shown").count() == 1


def test_a_new_service_gets_a_key_from_its_name_and_goes_last(admin, world):
    response = admin.post(ADMIN, NEW, format="json")
    assert response.status_code == 201
    data = response.json()
    assert data["key"] == "ration_card" and data["order"] == len(SEEDED) and data["is_active"] is True
    assert [row["key"] for row in APIClient().get(SERVICES).json()][-1] == "ration_card"
    entry = AuditLog.objects.get(action="service.created")
    assert entry.data == {"key": "ration_card"}


def test_names_are_unique_whatever_the_case(admin, world):
    admin.post(ADMIN, NEW, format="json")
    again = admin.post(ADMIN, {**NEW, "name_en": "ration  CARD"}, format="json")
    assert again.status_code == 400 and "name_en" in again.json()["error"]["fields"]
    renamed = admin.patch(f"{ADMIN}pan/", {"name_en": "Ration card"}, format="json")
    assert renamed.status_code == 400


def test_a_taken_key_gets_a_number(admin, world):
    Service.objects.filter(key="aadhaar").update(name_en="Aadhaar (old)")
    response = admin.post(ADMIN, {**NEW, "name_en": "Aadhaar"}, format="json")
    assert response.json()["key"] == "aadhaar_2"


def test_the_key_never_changes(admin, world):
    response = admin.patch(f"{ADMIN}aadhaar/", {"name_en": "Aadhaar Seva", "key": "other"}, format="json")
    assert response.status_code == 200
    assert response.json()["key"] == "aadhaar" and response.json()["name_en"] == "Aadhaar Seva"
    assert AuditLog.objects.get(action="service.updated").data == {"key": "aadhaar", "changed": ["name_en"]}


@pytest.mark.parametrize(
    "changes, field",
    [({"icon": "rocket"}, "icon"), ({"color": "red"}, "color"), ({"name_en": "A"}, "name_en"),
     ({"name_en": "x" * 61}, "name_en")],
)
def test_bad_fields_are_refused(admin, world, changes, field):
    response = admin.post(ADMIN, {**NEW, **changes}, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["fields"]


def test_reordering_needs_every_key_once(admin, world):
    new_order = list(reversed(SEEDED))
    response = admin.post(f"{ADMIN}order/", {"keys": new_order}, format="json")
    assert response.status_code == 200 and [row["key"] for row in response.json()] == new_order
    assert [row["key"] for row in APIClient().get(SERVICES).json()] == new_order
    assert AuditLog.objects.get(action="service.reordered").data == {"keys": new_order}
    for keys in (SEEDED[:-1], [*SEEDED, "ration_card"], [*SEEDED[:-1], "aadhaar"]):
        refused = admin.post(f"{ADMIN}order/", {"keys": keys}, format="json")
        assert refused.status_code == 400 and "keys" in refused.json()["error"]["fields"], keys


def test_an_unknown_service_is_not_found(admin, world):
    assert admin.patch(f"{ADMIN}passport/", {"is_active": False}, format="json").status_code == 404


@pytest.mark.parametrize("name", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_manages_services(api, world, name):
    client = api(world.users[name])
    for response in (
        client.get(ADMIN),
        client.post(ADMIN, NEW, format="json"),
        client.patch(f"{ADMIN}pan/", {"is_active": False}, format="json"),
        client.post(f"{ADMIN}order/", {"keys": SEEDED}, format="json"),
    ):
        assert response.status_code == 403 and code(response) == "permission_denied"
    assert Service.objects.get(key="pan").is_active
