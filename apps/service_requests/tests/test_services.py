"""The services list the Super Admin manages and citizens see on Home. Contract section 16."""

import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
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
                       "hint_en": "", "hint_te": "",  # empty: the app shows its own example
                       "icon": "aadhaar", "logo": None, "color": "orange", "is_active": True}


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
    assert entry.data == {"key": "ration_card", "logo": False}
    assert data["logo"] is None


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
     ({"name_en": "x" * 61}, "name_en"), ({"hint_en": "x" * 101}, "hint_en"), ({"hint_te": "క" * 101}, "hint_te")],
)
def test_bad_fields_are_refused(admin, world, changes, field):
    response = admin.post(ADMIN, {**NEW, **changes}, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["fields"]


# --- The request example (hint_en, hint_te) -----------------------------------------------------------

HINTS = {"hint_en": "e.g. apply for a new ration card", "hint_te": "ఉదా: కొత్త రేషన్ కార్డు కోసం దరఖాస్తు"}


def test_a_new_service_keeps_its_request_example(admin, world):
    data = admin.post(ADMIN, {**NEW, **HINTS}, format="json").json()
    assert (data["hint_en"], data["hint_te"]) == (HINTS["hint_en"], HINTS["hint_te"])
    public = next(row for row in APIClient().get(SERVICES).json() if row["key"] == "ration_card")
    assert (public["hint_en"], public["hint_te"]) == (HINTS["hint_en"], HINTS["hint_te"])
    admin_row = next(row for row in admin.get(ADMIN).json() if row["key"] == "ration_card")
    assert admin_row["hint_te"] == HINTS["hint_te"]


def test_without_an_example_both_are_empty(admin, world):
    data = admin.post(ADMIN, NEW, format="json").json()
    assert (data["hint_en"], data["hint_te"]) == ("", "")
    assert set(Service.objects.values_list("hint_en", "hint_te")) == {("", "")}  # the seeded 8 too


def test_changing_only_the_telugu_example(admin, world):
    admin.patch(f"{ADMIN}pan/", {"hint_en": "e.g. apply for a new PAN card"}, format="json")
    AuditLog.objects.all().delete()
    response = admin.patch(f"{ADMIN}pan/", {"hint_te": "ఉదా: కొత్త పాన్ కార్డు"}, format="json")
    assert response.status_code == 200
    assert (response.json()["hint_en"], response.json()["hint_te"]) == ("e.g. apply for a new PAN card", "ఉదా: కొత్త పాన్ కార్డు")
    assert AuditLog.objects.get(action="service.updated").data == {"key": "pan", "changed": ["hint_te"]}


def test_an_example_is_cleaned_of_extra_spaces_and_can_be_cleared(admin, world):
    data = admin.post(ADMIN, {**NEW, "hint_en": "  e.g.   new  card ", "hint_te": " ఉదా:   కొత్త  "}, format="json").json()
    assert (data["hint_en"], data["hint_te"]) == ("e.g. new card", "ఉదా: కొత్త")
    cleared = admin.patch(f"{ADMIN}ration_card/", {"hint_en": ""}, format="json").json()
    assert cleared["hint_en"] == "" and cleared["hint_te"] == "ఉదా: కొత్త"


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


# --- Logos -----------------------------------------------------------------------------------------


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def logo(size=(512, 512), fmt="PNG", name="logo.png", mode="RGBA") -> SimpleUploadedFile:
    image = Image.new(mode, size, (31, 138, 59, 128) if mode == "RGBA" else (31, 138, 59))  # see-through
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def stored(media_root, url: str) -> Image.Image:
    return Image.open(Path(media_root) / url.split("/media/", 1)[1])


def test_a_new_service_with_a_logo_needs_no_icon(admin, world, media_root):
    body = {"name_en": "Ration Card", "name_te": "రేషన్ కార్డు", "color": "teal", "logo": logo()}
    response = admin.post(ADMIN, body, format="multipart")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["icon"] == "services"  # the fallback for app versions that only draw icons
    assert data["logo"].startswith("http://testserver/media/services/ration_card-") and data["logo"].endswith("-256.webp")
    image = stored(media_root, data["logo"])
    assert image.format == "WEBP" and image.size == (256, 256) and image.mode == "RGBA"  # transparency kept
    public = next(row for row in APIClient().get(SERVICES).json() if row["key"] == "ration_card")
    assert public["logo"] == data["logo"]
    assert AuditLog.objects.get(action="service.created").data == {"key": "ration_card", "logo": True}


def test_a_multipart_create_with_a_logo_keeps_the_example(admin, world, media_root):
    body = {**NEW, **HINTS, "logo": logo()}
    data = admin.post(ADMIN, body, format="multipart").json()
    assert data["logo"] and (data["hint_en"], data["hint_te"]) == (HINTS["hint_en"], HINTS["hint_te"])


def test_a_logo_is_added_replaced_and_removed(admin, world, media_root):
    first = admin.patch(f"{ADMIN}pan/", {"logo": logo(fmt="JPEG", name="pan.jpg", mode="RGB")}, format="multipart")
    assert first.status_code == 200 and first.json()["logo"]
    old_file = Path(media_root) / first.json()["logo"].split("/media/", 1)[1]
    assert old_file.exists()

    with_name = admin.patch(f"{ADMIN}pan/", {"name_en": "PAN Card", "logo": logo()}, format="multipart")
    assert with_name.json()["name_en"] == "PAN Card" and with_name.json()["logo"] != first.json()["logo"]
    changes = [entry.data["changed"] for entry in AuditLog.objects.filter(action="service.updated")]
    assert sorted(changes) == [["logo"], ["name_en", "logo"]]

    removed = admin.patch(f"{ADMIN}pan/", {"logo": None}, format="json")
    assert removed.status_code == 200 and removed.json()["logo"] is None and removed.json()["icon"] == "id_badge"
    assert admin.patch(f"{ADMIN}pan/", {"logo": None}, format="json").status_code == 200  # nothing left to remove
    assert AuditLog.objects.filter(action="service.updated").count() == 3


def test_old_logo_files_are_deleted_after_the_change(admin, world, media_root, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = admin.patch(f"{ADMIN}pan/", {"logo": logo()}, format="multipart").json()["logo"]
    old_file = Path(media_root) / first.split("/media/", 1)[1]
    with django_capture_on_commit_callbacks(execute=True):
        admin.patch(f"{ADMIN}pan/", {"logo": logo()}, format="multipart")
    assert not old_file.exists()


def test_a_wide_logo_keeps_its_shape(admin, world, media_root):
    url = admin.patch(f"{ADMIN}pan/", {"logo": logo(size=(800, 400))}, format="multipart").json()["logo"]
    assert stored(media_root, url).size == (256, 128)


@pytest.mark.parametrize(
    "upload, why",
    [
        (SimpleUploadedFile("logo.png", b"just some text, not a picture", content_type="image/png"), "not an image"),
        (SimpleUploadedFile("logo.gif", b"GIF89a" + b"\x00" * 100, content_type="image/gif"), "a GIF"),
        (None, "too small"),
        (SimpleUploadedFile("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml"), "SVG"),
    ],
)
def test_bad_logos_are_refused(admin, world, media_root, upload, why):
    upload = upload or logo(size=(63, 200))
    response = admin.post(ADMIN, {**NEW, "logo": upload}, format="multipart")
    assert response.status_code == 400 and "logo" in response.json()["error"]["fields"], why
    assert not Service.objects.filter(name_en="Ration Card").exists()


def test_a_too_large_logo_is_refused(admin, world, media_root, settings):
    settings.SERVICE_LOGO_MAX_UPLOAD_MB = 0
    response = admin.patch(f"{ADMIN}pan/", {"logo": logo()}, format="multipart")
    assert response.status_code == 400 and "logo" in response.json()["error"]["fields"]


def test_only_the_super_admin_uploads_logos(api, world, media_root):
    response = api(world.users["da_guntur"]).patch(f"{ADMIN}pan/", {"logo": logo()}, format="multipart")
    assert response.status_code == 403 and not Service.objects.get(key="pan").logo