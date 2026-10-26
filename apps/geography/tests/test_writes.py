"""Super Admin area writes: one class per URL, services.py as the only writer, warnings, audit, uniqueness.

Expected answers come from the rules (owner decision Q13: Super Admin only;
public reads need no login), not from the code under test.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.accounts.tests.test_auth import console_session
from apps.audit.models import AuditLog
from apps.geography.models import District, Mandal, Village

pytestmark = pytest.mark.django_db


def codes(response) -> list[str]:
    return [w["code"] for w in response.json()["warnings"]]


def village_body(world, **extra) -> dict:
    return {"mandal_id": world.areas["tenali"].id, "name_en": "Chebrolu", "name_te": "చేబ్రోలు",
            "latitude": "16.196000", "longitude": "80.525000", "pin_code": "522212", **extra}


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


# --- Creating ------------------------------------------------------------------------------------


def test_add_district_mandal_and_village_return_the_row_and_no_warnings(admin, world):
    district = admin.post("/api/v1/geo/districts/", {"state_id": world.state.id, "name_en": "Bapatla", "name_te": "బాపట్ల"}, format="json")
    assert district.status_code == 201, district.json()
    assert {k: district.json()[k] for k in ("level", "name_en", "name_te", "state_id", "is_active", "warnings")} == {
        "level": "DISTRICT", "name_en": "Bapatla", "name_te": "బాపట్ల", "state_id": world.state.id,
        "is_active": True, "warnings": [],
    }
    mandal = admin.post("/api/v1/geo/mandals/", {"district_id": district.json()["id"], "name_en": "Ponnur", "name_te": "పొన్నూరు"}, format="json")
    assert mandal.status_code == 201 and mandal.json()["district_id"] == district.json()["id"]
    village = admin.post("/api/v1/geo/villages/", village_body(world, mandal_id=mandal.json()["id"]), format="json")
    assert village.status_code == 201
    assert (village.json()["latitude"], village.json()["pin_code"], village.json()["warnings"]) == ("16.196000", "522212", [])
    assert Village.objects.get(name_en="Chebrolu").name_te == "చేబ్రోలు"


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"latitude": "80.525000", "longitude": "16.196000"}, ["coordinates_swapped"]),
        ({"latitude": "28.613900", "longitude": "77.209000"}, ["outside_state"]),  # Delhi
        ({"pin_code": "110001"}, ["pin_outside_state"]),
        ({"name_te": "Chebrolu village"}, ["name_te_not_telugu"]),
        ({"name_te": "Chebrolu"}, ["name_te_missing"]),  # same as English: what is stored when none is given
        ({"name_te": ""}, ["name_te_missing"]),
    ],
)
def test_warnings_never_block_the_save(admin, world, change, expected):
    response = admin.post("/api/v1/geo/villages/", village_body(world, **change), format="json")
    assert response.status_code == 201 and codes(response) == expected


def test_a_missing_telugu_name_warns_on_create_too(admin, world):
    body = village_body(world)
    del body["name_te"]
    response = admin.post("/api/v1/geo/villages/", body, format="json")
    assert response.status_code == 201 and codes(response) == ["name_te_missing"]


def test_adding_under_a_switched_off_parent_warns(admin, world):
    Mandal.objects.filter(pk=world.areas["tenali"].id).update(is_active=False)
    response = admin.post("/api/v1/geo/villages/", village_body(world), format="json")
    assert response.status_code == 201 and codes(response) == ["parent_inactive"]


@pytest.mark.parametrize(
    "url, body, field",
    [
        ("/api/v1/geo/districts/", {"state_id": 999999, "name_en": "X"}, "state_id"),
        ("/api/v1/geo/mandals/", {"name_en": "X"}, "district_id"),
        ("/api/v1/geo/villages/", {"mandal_id": 1, "name_en": "X"}, "latitude"),
        ("/api/v1/geo/districts/", {"state_id": 1, "name_en": "   "}, "name_en"),
        ("/api/v1/geo/districts/", {"state_id": 1, "name_en": "X", "lgd_code": "AB12"}, "lgd_code"),
    ],
)
def test_bad_requests_name_the_field(admin, world, url, body, field):
    if "state_id" in body and body["state_id"] == 1:
        body["state_id"] = world.state.id
    response = admin.post(url, body, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["fields"]


# --- Uniqueness ----------------------------------------------------------------------------------


def test_same_name_under_the_same_parent_is_409_but_fine_elsewhere(admin, world):
    tenali = {"mandal_id": world.areas["tenali"].id}
    duplicate = admin.post("/api/v1/geo/villages/", village_body(world, name_en=" KOLAKALURU ", **tenali), format="json")
    assert duplicate.status_code == 409 and "name_en" in duplicate.json()["error"]["fields"]
    elsewhere = village_body(world, name_en="Kolakaluru", mandal_id=world.areas["gannavaram"].id)
    assert admin.post("/api/v1/geo/villages/", elsewhere, format="json").status_code == 201


def test_lgd_code_is_unique(admin, world):
    District.objects.filter(pk=world.areas["guntur"].id).update(lgd_code="505")
    response = admin.post("/api/v1/geo/districts/", {"state_id": world.state.id, "name_en": "Bapatla", "lgd_code": "505"}, format="json")
    assert response.status_code == 409 and "lgd_code" in response.json()["error"]["fields"]


def test_the_database_rejects_duplicate_siblings(world):
    tenali = world.areas["tenali"]
    Village.objects.create(mandal=tenali, name_en="Twin", latitude=16.2, longitude=80.6)
    with pytest.raises(IntegrityError), transaction.atomic():
        Village.objects.create(mandal=tenali, name_en=" twin ", latitude=16.2, longitude=80.6)


def test_a_lost_race_answers_409(admin, world, monkeypatch):
    """The friendly check passes, but another admin's insert wins at the database."""
    from apps.geography import services

    monkeypatch.setattr(services, "_check_unique", lambda *args, **kwargs: None)
    Village.objects.create(mandal=world.areas["tenali"], name_en="Race", latitude=16.2, longitude=80.6)
    response = admin.post("/api/v1/geo/villages/", village_body(world, name_en="Race"), format="json")
    assert response.status_code == 409 and response.json()["error"]["fields"]["name_en"]


# --- Editing and audit ---------------------------------------------------------------------------


def test_rename_audits_old_and_new_values(admin, world):
    village = world.areas["kolakaluru"]
    response = admin.patch(f"/api/v1/geo/villages/{village.id}/", {"name_en": "Kolakaluru Town", "pin_code": "522306"}, format="json")
    assert response.status_code == 200 and response.json()["name_en"] == "Kolakaluru Town"
    entry = AuditLog.objects.get(action="area.updated")
    assert entry.data["changes"] == {"name_en": ["Kolakaluru", "Kolakaluru Town"]}  # pin_code was already 522306


def test_an_edit_that_changes_nothing_writes_no_audit(admin, world):
    village = world.areas["kolakaluru"]
    assert admin.patch(f"/api/v1/geo/villages/{village.id}/", {"pin_code": village.pin_code}, format="json").status_code == 200
    assert not AuditLog.objects.filter(action__startswith="area.").exists()


def test_switching_off_and_on_is_audited_as_such(admin, world):
    url = f"/api/v1/geo/mandals/{world.areas['tenali'].id}/"
    admin.patch(url, {"is_active": False}, format="json")
    admin.patch(url, {"is_active": True}, format="json")
    assert list(AuditLog.objects.order_by("created_at").values_list("action", flat=True)) == ["area.deactivated", "area.reactivated"]


def test_patching_latitude_alone_still_checks_the_pair(admin, world):
    village = world.areas["kolakaluru"]
    response = admin.patch(f"/api/v1/geo/villages/{village.id}/", {"latitude": "80.592400"}, format="json")
    assert response.status_code == 200 and "outside_state" in codes(response)


def test_create_is_audited_with_parent_fields_and_warnings(admin, world):
    admin.post("/api/v1/geo/villages/", village_body(world, pin_code="110001"), format="json")
    entry = AuditLog.objects.get(action="area.created")
    assert entry.data["parent"] == ["MANDAL", world.areas["tenali"].id]
    assert entry.data["fields"]["name_te"] == "చేబ్రోలు" and entry.data["warnings"] == ["pin_outside_state"]


@pytest.mark.parametrize("url", ["/api/v1/geo/villages/999999/", "/api/v1/geo/districts/999999/", "/api/v1/geo/mandals/999999/"])
def test_editing_an_unknown_area_is_404(admin, world, url):
    assert admin.patch(url, {"name_en": "X"}, format="json").status_code == 404


# --- Who may write, and the public reads ---------------------------------------------------------

WRITES = [
    ("post", "/api/v1/geo/districts/"),
    ("post", "/api/v1/geo/mandals/"),
    ("post", "/api/v1/geo/villages/"),
    ("patch", "/api/v1/geo/districts/{guntur}/"),
    ("patch", "/api/v1/geo/mandals/{tenali}/"),
    ("patch", "/api/v1/geo/villages/{kolakaluru}/"),
]


@pytest.mark.parametrize("name", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
@pytest.mark.parametrize("method, url", WRITES)
def test_only_the_super_admin_writes(api, world, name, method, url):
    url = url.format(**{k: world.areas[k].id for k in ("guntur", "tenali", "kolakaluru")})
    response = getattr(api(world.users[name]), method)(url, {"name_en": "X"}, format="json")
    assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


@pytest.mark.parametrize("method, url", WRITES)
def test_writes_need_a_login(api, world, method, url):
    url = url.format(**{k: world.areas[k].id for k in ("guntur", "tenali", "kolakaluru")})
    assert getattr(api(), method)(url, {"name_en": "X"}, format="json").status_code == 401


@pytest.mark.parametrize(
    "url", ["/api/v1/geo/districts/?state={state}", "/api/v1/geo/mandals/?district={guntur}",
            "/api/v1/geo/villages/?mandal={tenali}", "/api/v1/geo/villages/{kolakaluru}/"],
)
def test_public_reads_ignore_a_stale_token_and_are_cached(api, world, url):
    url = url.format(state=world.state.id, **{k: world.areas[k].id for k in ("guntur", "tenali", "kolakaluru")})
    client = api()
    client.credentials(HTTP_AUTHORIZATION="Bearer expired.token.here")
    response = client.get(url)
    assert response.status_code == 200
    assert "public" in response["Cache-Control"] and "max-age=600" in response["Cache-Control"]


def test_write_answers_are_never_publicly_cached(admin, world):
    created = admin.post("/api/v1/geo/villages/", village_body(world), format="json")
    edited = admin.patch(f"/api/v1/geo/villages/{created.json()['id']}/", {"pin_code": "522213"}, format="json")
    for response in (created, edited):
        assert "public" not in response.get("Cache-Control", "")


def test_console_session_writes_need_the_csrf_token(world):
    client, _ = console_session("super")
    body = {"state_id": world.state.id, "name_en": "Bapatla"}
    assert client.post("/api/v1/geo/districts/", body, format="json").status_code == 403
    response = client.post("/api/v1/geo/districts/", body, format="json", HTTP_X_CSRFTOKEN=client.csrf)
    assert response.status_code == 201


# --- The CSV importer uses the same writer ----------------------------------------------------------


def upload(client, text):
    return client.post("/api/v1/geo/villages/import/",
                       {"file": SimpleUploadedFile("v.csv", text.encode("utf-8"), "text/csv")}, format="multipart")


def test_import_writes_through_the_service_with_one_audit_entry_and_its_warnings(admin, world):
    text = ("state,district,mandal,village,village_te,latitude,longitude,pin_code\n"
            "Andhra Pradesh,Guntur,Tenali,Chebrolu,చేబ్రోలు,16.196000,80.525000,110001\n")
    response = upload(admin, text)
    assert response.status_code == 200, response.json()
    assert response.json()["counts"]["village"]["created"] == 1
    assert any("Andhra Pradesh PIN codes start with 5" in w for w in response.json()["warnings"])
    assert Village.objects.filter(name_en="Chebrolu").exists()
    assert list(AuditLog.objects.values_list("action", flat=True)) == ["area.imported"]  # not one per row
