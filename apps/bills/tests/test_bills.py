"""Bills and recharges the Super Admin manages on Home (contract section 18). A list of its own:
nothing here touches the services (section 16)."""

import importlib
import io
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.bills.models import Bill
from apps.service_requests.models import Service

pytestmark = pytest.mark.django_db

BILLS = "/api/v1/bills/"
ADMIN = "/api/v1/admin/bills/"
SEEDED = ["electricity", "water_bill", "mobile_recharge", "dth_recharge", "broadband"]
NEW = {"name_en": "Gas Booking", "name_te": "గ్యాస్ బుకింగ్", "icon": "gas", "color": "orange"}


def code(response):
    return response.json()["error"]["code"]


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def logo(size=(512, 512), fmt="PNG", name="logo.png", mode="RGBA") -> SimpleUploadedFile:
    image = Image.new(mode, size, (31, 138, 59, 128) if mode == "RGBA" else (31, 138, 59))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def on_disk(media_root, url: str) -> Path:
    return Path(media_root) / url.split("/media/", 1)[1]


# --- 1, 14. Public list and the seed ---------------------------------------------------------------


def test_the_public_list_has_the_five_seeded_bills_in_order_without_login(world):
    response = APIClient().get(BILLS)
    assert response.status_code == 200 and "max-age=300" in response["Cache-Control"]
    data = response.json()
    assert [row["key"] for row in data] == SEEDED
    assert data[0] == {"key": "electricity", "name_en": "Electricity Bill", "name_te": "కరెంట్ బిల్లు",
                       "icon": "power", "logo": None, "color": "orange", "is_active": True}


def test_the_seed_runs_again_without_duplicates(world):
    Bill.objects.filter(key="electricity").update(name_en="Power Bill")
    migration = importlib.import_module("apps.bills.migrations.0002_seed_bills")
    migration.seed(django_apps, None)
    assert Bill.objects.count() == 5 and Bill.objects.get(key="electricity").name_en == "Power Bill"


# --- 2. Who may manage -----------------------------------------------------------------------------


def test_the_admin_list_has_hidden_bills_too(admin, world):
    Bill.objects.filter(key="dth_recharge").update(is_active=False)
    rows = admin.get(ADMIN).json()
    assert [row["key"] for row in rows] == SEEDED and {"order", "created_at", "updated_at"} <= set(rows[0])


@pytest.mark.parametrize("name", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_manages_bills(api, world, name):
    client = api(world.users[name])
    for response in (
        client.get(ADMIN),
        client.post(ADMIN, NEW, format="json"),
        client.patch(f"{ADMIN}electricity/", {"is_active": False}, format="json"),
        client.post(f"{ADMIN}order/", {"keys": SEEDED}, format="json"),
    ):
        assert response.status_code == 403 and code(response) == "permission_denied"
    assert Bill.objects.get(key="electricity").is_active


def test_the_admin_list_needs_a_login(world):
    assert APIClient().get(ADMIN).status_code == 401


# --- 3, 4, 10. Adding ------------------------------------------------------------------------------


def test_a_new_bill_gets_a_key_from_its_name_and_goes_last(admin, world):
    response = admin.post(ADMIN, NEW, format="json")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert (data["key"], data["order"], data["logo"]) == ("gas_booking", 5, None)
    assert [row["key"] for row in APIClient().get(BILLS).json()][-1] == "gas_booking"
    assert AuditLog.objects.get(action="bill.created").data == {"key": "gas_booking", "logo": False}


def test_a_taken_key_gets_a_number(admin, world):
    Bill.objects.filter(key="electricity").update(name_en="Electricity (old)")
    assert admin.post(ADMIN, {**NEW, "name_en": "Electricity"}, format="json").json()["key"] == "electricity_2"


def test_names_are_unique_among_bills_only(admin, world):
    again = admin.post(ADMIN, {**NEW, "name_en": "water  BILL"}, format="json")
    assert again.status_code == 400 and "name_en" in again.json()["error"]["fields"]
    Service.objects.filter(key="pan").update(name_en="Gas Booking")
    assert admin.post(ADMIN, NEW, format="json").status_code == 201  # a service may have the same name


def test_the_icon_defaults_to_bill(admin, world):
    body = {key: value for key, value in NEW.items() if key != "icon"}
    assert admin.post(ADMIN, body, format="json").json()["icon"] == "bill"


@pytest.mark.parametrize(
    "icon", ["power", "water", "gas", "mobile", "landline", "dth", "broadband", "fastag", "credit_card", "bill"]
)
def test_every_icon_the_clients_send_is_accepted(admin, world, icon):
    response = admin.post(ADMIN, {**NEW, "icon": icon}, format="json")
    assert response.status_code == 201 and response.json()["icon"] == icon


@pytest.mark.parametrize(
    "changes, field",
    [({"icon": "aadhaar"}, "icon"), ({"icon": "card"}, "icon"), ({"icon": "services"}, "icon"),
     ({"color": "red"}, "color"), ({"name_en": "A"}, "name_en"),
     ({"name_en": "x" * 61}, "name_en"), ({"color": None}, "color")],
)
def test_bad_fields_are_refused(admin, world, changes, field):
    response = admin.post(ADMIN, {**NEW, **changes}, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["fields"]


# --- 5-9. Logos ------------------------------------------------------------------------------------


def test_a_new_bill_with_a_logo(admin, world, media_root):
    response = admin.post(ADMIN, {**NEW, "logo": logo()}, format="multipart")
    assert response.status_code == 201, response.json()
    url = response.json()["logo"]
    assert url.startswith("http://testserver/media/bills/gas_booking-") and url.endswith("-256.webp")
    image = Image.open(on_disk(media_root, url))
    assert image.format == "WEBP" and image.size == (256, 256) and image.mode == "RGBA"
    assert AuditLog.objects.get(action="bill.created").data == {"key": "gas_booking", "logo": True}


def test_a_logo_is_replaced_kept_and_removed(admin, world, media_root, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = admin.patch(f"{ADMIN}electricity/", {"logo": logo(fmt="JPEG", name="e.jpg", mode="RGB")},
                            format="multipart").json()["logo"]
    old_file = on_disk(media_root, first)
    with django_capture_on_commit_callbacks(execute=True):
        second = admin.patch(f"{ADMIN}electricity/", {"logo": logo()}, format="multipart").json()["logo"]
    assert second != first and not old_file.exists() and on_disk(media_root, second).exists()

    kept = admin.patch(f"{ADMIN}electricity/", {"name_te": "విద్యుత్ బిల్లు"}, format="json").json()
    assert kept["logo"] == second

    with django_capture_on_commit_callbacks(execute=True):
        removed = admin.patch(f"{ADMIN}electricity/", {"logo": None}, format="json").json()
    assert removed["logo"] is None and removed["icon"] == "power" and not on_disk(media_root, second).exists()
    changes = sorted(entry.data["changed"] for entry in AuditLog.objects.filter(action="bill.updated"))
    assert changes == [["logo"], ["logo"], ["logo"], ["name_te"]]


@pytest.mark.parametrize(
    "upload, why",
    [
        (SimpleUploadedFile("logo.png", b"just some text, not a picture", content_type="image/png"), "not an image"),
        (SimpleUploadedFile("logo.gif", b"GIF89a" + b"\x00" * 100, content_type="image/gif"), "a GIF"),
        (None, "too small"),
    ],
)
def test_bad_logos_are_refused(admin, world, media_root, upload, why):
    response = admin.post(ADMIN, {**NEW, "logo": upload or logo(size=(63, 200))}, format="multipart")
    assert response.status_code == 400 and "logo" in response.json()["error"]["fields"], why
    assert not Bill.objects.filter(name_en="Gas Booking").exists()


def test_a_too_large_logo_is_refused(admin, world, media_root, settings):
    settings.SERVICE_LOGO_MAX_UPLOAD_MB = 0
    response = admin.patch(f"{ADMIN}electricity/", {"logo": logo()}, format="multipart")
    assert response.status_code == 400 and "logo" in response.json()["error"]["fields"]


# --- 11. Hiding ------------------------------------------------------------------------------------


def test_hiding_takes_it_off_home_only(admin, world):
    assert admin.patch(f"{ADMIN}broadband/", {"is_active": False}, format="json").json()["is_active"] is False
    assert "broadband" not in [row["key"] for row in APIClient().get(BILLS).json()]
    assert "broadband" in [row["key"] for row in admin.get(ADMIN).json()]
    admin.patch(f"{ADMIN}broadband/", {"is_active": True}, format="json")
    assert [e.action for e in AuditLog.objects.filter(action__in=["bill.hidden", "bill.shown"]).order_by("created_at")] \
        == ["bill.hidden", "bill.shown"]


def test_an_unknown_bill_is_not_found(admin, world):
    assert admin.patch(f"{ADMIN}aadhaar/", {"is_active": False}, format="json").status_code == 404  # a service key


# --- 12, 13. Order ---------------------------------------------------------------------------------


def test_reordering_needs_every_bill_key_once(admin, world):
    new_order = list(reversed(SEEDED))
    response = admin.post(f"{ADMIN}order/", {"keys": new_order}, format="json")
    assert response.status_code == 200 and [row["key"] for row in response.json()] == new_order
    assert AuditLog.objects.get(action="bill.reordered").data == {"keys": new_order}
    for keys in (SEEDED[:-1], [*SEEDED, "aadhaar"], [*SEEDED[:-1], "electricity"], [*SEEDED[:-1], "aadhaar"]):
        refused = admin.post(f"{ADMIN}order/", {"keys": keys}, format="json")
        assert refused.status_code == 400 and "keys" in refused.json()["error"]["fields"], keys


def test_bills_and_services_keep_their_own_order(admin, world):
    services_before = [row["key"] for row in APIClient().get("/api/v1/services/").json()]
    admin.post(f"{ADMIN}order/", {"keys": list(reversed(SEEDED))}, format="json")
    assert [row["key"] for row in APIClient().get("/api/v1/services/").json()] == services_before
    admin.post("/api/v1/admin/services/order/", {"keys": list(reversed(services_before))}, format="json")
    assert [row["key"] for row in APIClient().get(BILLS).json()] == list(reversed(SEEDED))
