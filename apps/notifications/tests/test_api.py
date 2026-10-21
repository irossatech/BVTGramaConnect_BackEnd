"""Devices, the officials' inbox and the notification-text settings."""

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.notifications.models import Device, Notification, NotificationTemplate

pytestmark = pytest.mark.django_db


# --- Devices ---------------------------------------------------------------------------


def register(client, **body):
    payload = {"fcm_token": "tok-1", "platform": "android", "language": "te"}
    payload.update(body)
    return client.post("/api/v1/devices/", payload, format="json")


def test_citizen_registers_without_login_and_updates_by_token(geo):
    village = geo.areas["kolakaluru"]
    first = register(APIClient(), village_id=village.id)
    assert first.status_code == 201
    device = Device.objects.get()
    assert device.user is None and device.village_id == village.id
    again = register(APIClient(), village_id=geo.areas["kaza"].id, language="en")
    assert again.status_code == 200
    device.refresh_from_db()
    assert Device.objects.count() == 1
    assert (device.village_id, device.language) == (geo.areas["kaza"].id, "en")


def test_official_device_is_linked_and_unlinked_by_anonymous_use(api, world):
    user = world.users["ma_tenali"]
    register(api(user))
    assert Device.objects.get().user == user
    register(APIClient())
    assert Device.objects.get().user is None


def test_unknown_village_is_rejected(geo):
    response = register(APIClient(), village_id=999999)
    assert response.status_code == 400
    assert "village_id" in response.json()["error"]["fields"]


def test_patch_and_delete_a_device(api, world):
    device_id = register(APIClient()).json()["id"]
    patched = APIClient().patch(f"/api/v1/devices/{device_id}/", {"village_id": world.areas["kaza"].id}, format="json")
    assert patched.status_code == 200
    assert Device.objects.get().village_id == world.areas["kaza"].id
    assert APIClient().delete(f"/api/v1/devices/{device_id}/").status_code == 204
    assert not Device.objects.exists()


def test_someone_elses_official_device_is_hidden(api, world):
    device_id = register(api(world.users["ma_tenali"])).json()["id"]
    assert APIClient().patch(f"/api/v1/devices/{device_id}/", {"language": "en"}, format="json").status_code == 404
    assert api(world.users["da_guntur"]).delete(f"/api/v1/devices/{device_id}/").status_code == 404
    assert api(world.users["ma_tenali"]).delete(f"/api/v1/devices/{device_id}/").status_code == 204


# --- Inbox --------------------------------------------------------------------------------------


@pytest.fixture
def notes(world):
    user = world.users["va_kolakaluru"]
    rows = [Notification.objects.create(user=user, kind="REVIEW_PENDING", title=f"n{i}") for i in range(3)]
    Notification.objects.create(user=world.users["ma_tenali"], kind="REVIEW_PENDING", title="other")
    return user, rows


def test_inbox_lists_only_own_items_newest_first(api, notes):
    user, rows = notes
    data = api(user).get("/api/v1/notifications/").json()
    assert [row["title"] for row in data["results"]] == ["n2", "n1", "n0"]
    assert data["unread_count"] == 3
    assert set(data["results"][0]) == {"id", "kind", "title", "body", "post_id", "area", "created_at", "read_at"}


def test_mark_one_and_all_read(api, notes):
    user, rows = notes
    client = api(user)
    assert client.post(f"/api/v1/notifications/{rows[0].id}/read/").status_code == 204
    data = client.get("/api/v1/notifications/?unread=1").json()
    assert data["unread_count"] == 2 and len(data["results"]) == 2
    assert client.post("/api/v1/notifications/read-all/").status_code == 204
    assert client.get("/api/v1/notifications/").json()["unread_count"] == 0


def test_cannot_read_someone_elses_notification(api, world, notes):
    _, rows = notes
    assert api(world.users["ma_tenali"]).post(f"/api/v1/notifications/{rows[0].id}/read/").status_code == 404


def test_inbox_needs_login(db):
    assert APIClient().get("/api/v1/notifications/").status_code == 401


# --- Notification texts -----------------------------------------------------------------------


def test_templates_are_seeded_and_super_admin_only(api, world):
    data = api(world.users["super"]).get("/api/v1/admin/notification-templates/").json()
    keys = [row["key"] for row in data]
    assert keys[:3] == ["post_published", "post_approved", "post_recheck"]
    assert not {"welcome_sms", "otp_sms"} & set(keys)  # SMS was dropped
    assert all(row["channel"] == "push" and row["editable"] for row in data)
    assert api(world.users["da_guntur"]).get("/api/v1/admin/notification-templates/").status_code == 403


def test_super_admin_edits_a_push_text_and_it_is_audited(api, world):
    response = api(world.users["super"]).patch(
        "/api/v1/admin/notification-templates/post_recheck/",
        {"text_te": "సరిచేయండి: {comment}", "text_en": "Please fix: {comment}"},
        format="json",
    )
    assert response.status_code == 200
    assert NotificationTemplate.objects.get(key="post_recheck").text_en == "Please fix: {comment}"
    assert AuditLog.objects.filter(action="template.updated").exists()


def test_unknown_variables_are_rejected(api, world):
    response = api(world.users["super"]).patch(
        "/api/v1/admin/notification-templates/post_recheck/",
        {"text_te": "{otp}", "text_en": "Please fix: {comment}"},
        format="json",
    )
    assert response.status_code == 400
    assert "text_te" in response.json()["error"]["fields"]
