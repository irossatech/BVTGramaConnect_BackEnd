"""Edit and delete a service only while it is hidden from Home (contract section 16, 2026-11-26).

DELETE /admin/services/{key}/ removes a hidden service no request uses, such as one added by mistake:
204, audited as service.deleted, its logo file removed after the delete is saved. Shown on Home:
409 switch_off_first. Used by any request, cancelled ones included: 409 service_in_use with `count`,
and it stays (requests are kept permanently and keep their service).
PATCH changes names, examples, icon, colour and logo only while hidden; shown, it answers 409
switch_off_first. The show/hide switch works any time. Only the Super Admin (manage_services) may.
"""

import io
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from apps.service_requests import services as service_writes
from apps.service_requests.models import Service, ServiceRequest
from conftest import OFFICIALS

from .conftest import ADMIN

pytestmark = pytest.mark.django_db

NEW = {"name_en": "Added by mistake", "name_te": "తప్పు", "icon": "ration", "color": "teal"}


def logo() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), (31, 138, 59)).save(buffer, "PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")


def hide(key: str) -> None:
    Service.objects.filter(key=key).update(is_active=False)


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


# --- Delete ------------------------------------------------------------------------------------------

def test_a_hidden_unused_service_is_deleted_and_audited(admin, world):
    key = admin.post(ADMIN, NEW, format="json").json()["key"]
    assert admin.patch(f"{ADMIN}{key}/", {"is_active": False}, format="json").status_code == 200
    response = admin.delete(f"{ADMIN}{key}/")
    assert response.status_code == 204
    assert not Service.objects.filter(key=key).exists()
    assert key not in [row["key"] for row in admin.get(ADMIN).json()]
    entry = AuditLog.objects.get(action="service.deleted")
    assert entry.target_label == "Added by mistake"
    assert entry.data == {"key": key, "name_en": "Added by mistake", "name_te": "తప్పు"}


def test_a_shown_service_is_not_deleted(admin, world, english):
    key = admin.post(ADMIN, NEW, format="json").json()["key"]
    response = admin.delete(f"{ADMIN}{key}/")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "switch_off_first"
    assert Service.objects.filter(key=key).exists()
    assert not AuditLog.objects.filter(action="service.deleted").exists()


def test_a_hidden_service_with_requests_is_kept(admin, world, request_from, english):
    request_from("kolakaluru")
    citizen, request_id = request_from("kaza", ip="10.20.30.41")
    citizen.client.post(f"/api/v1/citizen/service-requests/{request_id}/cancel/", {}, format="json")
    hide("aadhaar")
    response = admin.delete(f"{ADMIN}aadhaar/")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "service_in_use" and error["count"] == 2  # the cancelled one counts too
    assert error["message"].startswith("2 requests use this service")
    assert Service.objects.filter(key="aadhaar").exists() and ServiceRequest.objects.count() == 2


def test_a_request_sent_at_the_same_moment_stops_the_delete(admin, world, request_from, monkeypatch):
    request_from("kolakaluru")
    hide("aadhaar")
    real = ServiceRequest.objects
    calls = {"n": 0}

    class FirstCountSaysNone:
        def filter(self, **kwargs):
            calls["n"] += 1
            return real.none() if calls["n"] == 1 else real.filter(**kwargs)

    monkeypatch.setattr(service_writes, "ServiceRequest", SimpleNamespace(objects=FirstCountSaysNone()))
    response = admin.delete(f"{ADMIN}aadhaar/")
    assert response.status_code == 409 and response.json()["error"]["code"] == "service_in_use"
    assert Service.objects.filter(key="aadhaar").exists()
    assert not AuditLog.objects.filter(action="service.deleted").exists()


def test_the_logo_file_goes_after_the_delete(admin, world, media_root, django_capture_on_commit_callbacks):
    key = admin.post(ADMIN, NEW, format="json").json()["key"]
    hide(key)
    url = admin.patch(f"{ADMIN}{key}/", {"logo": logo()}, format="multipart").json()["logo"]
    path = media_root / url.split("/media/", 1)[1]
    assert path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        assert admin.delete(f"{ADMIN}{key}/").status_code == 204
    assert not path.exists()


def test_an_unknown_service_answers_404(admin, world):
    assert admin.delete(f"{ADMIN}no_such_service/").status_code == 404


@pytest.mark.parametrize("name", list(OFFICIALS))
def test_only_the_super_admin_may_delete(api, world, name):
    expected = 204 if OFFICIALS[name][0] == Role.SUPER_ADMIN else 403
    Service.objects.create(key="mistake", name_en="Mistake", color="teal", is_active=False)
    assert api(world.users[name]).delete(f"{ADMIN}mistake/").status_code == expected
    assert Service.objects.filter(key="mistake").exists() is (expected != 204)


def test_not_logged_in_cannot_delete(world):
    hide("aadhaar")
    assert APIClient().delete(f"{ADMIN}aadhaar/").status_code == 401


# --- Edit only while hidden --------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"name_en": "Aadhaar Seva"}, {"name_te": "ఆధార్ సేవ"}, {"hint_en": "e.g. update"},
                                    {"icon": "ration"}, {"color": "teal"}, {"logo": None}])
def test_a_shown_service_is_not_edited(admin, world, change):
    before = Service.objects.get(key="aadhaar")
    if "logo" in change:  # removing a logo is an edit too
        Service.objects.filter(key="aadhaar").update(logo="services/aadhaar-x.webp")
    response = admin.patch(f"{ADMIN}aadhaar/", change, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    after = Service.objects.get(key="aadhaar")
    assert (after.name_en, after.name_te, after.hint_en, after.icon, after.color) == (
        before.name_en, before.name_te, before.hint_en, before.icon, before.color)
    assert not AuditLog.objects.filter(action="service.updated").exists()


def test_a_shown_service_keeps_its_logo(admin, world, media_root):
    response = admin.patch(f"{ADMIN}aadhaar/", {"logo": logo()}, format="multipart")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    assert not Service.objects.get(key="aadhaar").logo


def test_a_hidden_service_is_edited(admin, world):
    hide("aadhaar")
    response = admin.patch(f"{ADMIN}aadhaar/", {"name_en": "Aadhaar Seva"}, format="json")
    assert response.status_code == 200 and response.json()["name_en"] == "Aadhaar Seva"


def test_the_switch_works_any_time_and_unchanged_values_are_fine(admin, world):
    shown = admin.get(ADMIN).json()[0]
    body = {"name_en": shown["name_en"], "color": shown["color"], "is_active": False}
    assert admin.patch(f"{ADMIN}{shown['key']}/", body, format="json").status_code == 200
    assert admin.patch(f"{ADMIN}{shown['key']}/", {"is_active": True}, format="json").status_code == 200


def test_a_stale_copy_that_says_hidden_cannot_edit_or_delete_a_service_shown_meanwhile(world):
    # BE-QUA-203: the switch is read again under a row lock, not from the copy the request loaded.
    from grama.exceptions import ApiError

    stale = Service.objects.get(key="aadhaar")
    stale.is_active = False  # loaded while hidden; switched on by another admin since (saved: shown)
    with pytest.raises(ApiError) as refused:
        service_writes.update_service(world.users["super"], stale, {"name_en": "Changed"})
    assert refused.value.error_code == "switch_off_first"
    with pytest.raises(ApiError) as refused:
        service_writes.delete_service(world.users["super"], stale)
    assert refused.value.error_code == "switch_off_first"
    assert Service.objects.get(key="aadhaar").name_en != "Changed"
