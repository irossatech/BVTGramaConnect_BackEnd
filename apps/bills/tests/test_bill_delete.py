"""Edit and delete a bill / recharge only while it is hidden from Home (contract section 18, 2026-11-26).

DELETE /admin/bills/{key}/ removes a hidden bill: 204, audited as bill.deleted, its logo file removed
after the delete is saved. Nothing refers to a bill yet (no payment or request behind it), so any hidden
bill may go. Shown on Home: 409 switch_off_first. PATCH changes names, icon, colour and logo only while
hidden; shown, it answers 409 switch_off_first. The show/hide switch and the order work any time.
Only the Super Admin (manage_bills) may.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from apps.bills.models import Bill
from conftest import OFFICIALS

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/bills/"
PUBLIC = "/api/v1/bills/"
NEW = {"name_en": "Added by mistake", "name_te": "తప్పు", "icon": "gas", "color": "orange"}


def logo() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), (31, 138, 59)).save(buffer, "PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")


def hide(key: str) -> None:
    Bill.objects.filter(key=key).update(is_active=False)


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


# --- Delete ------------------------------------------------------------------------------------------

def test_a_hidden_bill_is_deleted_and_audited(admin, world):
    key = admin.post(ADMIN, NEW, format="json").json()["key"]
    assert admin.patch(f"{ADMIN}{key}/", {"is_active": False}, format="json").status_code == 200
    assert admin.delete(f"{ADMIN}{key}/").status_code == 204
    assert not Bill.objects.filter(key=key).exists()
    assert key not in [row["key"] for row in admin.get(ADMIN).json()]
    entry = AuditLog.objects.get(action="bill.deleted")
    assert entry.target_label == "Added by mistake"
    assert entry.data == {"key": key, "name_en": "Added by mistake", "name_te": "తప్పు"}


def test_a_seeded_bill_can_go_too_once_hidden(admin, world):
    hide("broadband")
    assert admin.delete(f"{ADMIN}broadband/").status_code == 204
    assert "broadband" not in [row["key"] for row in APIClient().get(PUBLIC).json()]


def test_a_shown_bill_is_not_deleted(admin, world):
    response = admin.delete(f"{ADMIN}electricity/")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    assert Bill.objects.filter(key="electricity").exists()
    assert not AuditLog.objects.filter(action="bill.deleted").exists()


def test_the_logo_file_goes_after_the_delete(admin, world, media_root, django_capture_on_commit_callbacks):
    hide("electricity")
    url = admin.patch(f"{ADMIN}electricity/", {"logo": logo()}, format="multipart").json()["logo"]
    path = media_root / url.split("/media/", 1)[1]
    assert path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        assert admin.delete(f"{ADMIN}electricity/").status_code == 204
    assert not path.exists()


def test_an_unknown_bill_answers_404(admin, world):
    assert admin.delete(f"{ADMIN}no_such_bill/").status_code == 404


@pytest.mark.parametrize("name", list(OFFICIALS))
def test_only_the_super_admin_may_delete(api, world, name):
    expected = 204 if OFFICIALS[name][0] == Role.SUPER_ADMIN else 403
    hide("broadband")
    assert api(world.users[name]).delete(f"{ADMIN}broadband/").status_code == expected
    assert Bill.objects.filter(key="broadband").exists() is (expected != 204)


def test_not_logged_in_cannot_delete(world):
    hide("broadband")
    assert APIClient().delete(f"{ADMIN}broadband/").status_code == 401


# --- Edit only while hidden --------------------------------------------------------------------------

@pytest.mark.parametrize("change", [{"name_en": "Power bill"}, {"name_te": "కరెంట్"}, {"icon": "gas"},
                                    {"color": "teal"}])
def test_a_shown_bill_is_not_edited(admin, world, change):
    before = Bill.objects.get(key="electricity")
    response = admin.patch(f"{ADMIN}electricity/", change, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    after = Bill.objects.get(key="electricity")
    assert (after.name_en, after.name_te, after.icon, after.color) == (before.name_en, before.name_te, before.icon, before.color)
    assert not AuditLog.objects.filter(action="bill.updated").exists()


def test_a_shown_bill_keeps_its_logo(admin, world, media_root):
    response = admin.patch(f"{ADMIN}electricity/", {"logo": logo()}, format="multipart")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    assert not Bill.objects.get(key="electricity").logo


def test_a_hidden_bill_is_edited(admin, world):
    hide("electricity")
    response = admin.patch(f"{ADMIN}electricity/", {"name_en": "Power bill"}, format="json")
    assert response.status_code == 200 and response.json()["name_en"] == "Power bill"


def test_the_switch_works_any_time_and_unchanged_values_are_fine(admin, world):
    shown = Bill.objects.get(key="electricity")
    body = {"name_en": shown.name_en, "color": shown.color, "is_active": False}
    assert admin.patch(f"{ADMIN}electricity/", body, format="json").status_code == 200
    assert admin.patch(f"{ADMIN}electricity/", {"is_active": True}, format="json").status_code == 200
