"""When every login of an official ends, their phones stop getting their alerts (BVTGC-94).

A password reset, "log out everywhere", a block, too many wrong current passwords and the official's own
password change end every session; they also remove the official's push links, as the app's own logout
does. After its own password change the app links the phone in hand again at once. Other officials' and
citizens' phones are never touched.
"""

import pytest
from django.conf import settings

from apps.notifications.models import Device
from apps.notifications.tasks import send_push_to_user
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db
NEW_PASSWORD = "Grama-Changed-2026!"


def linked(name: str) -> bool:
    return Device.objects.filter(fcm_token=f"tok-{name}", user__isnull=False).exists()


def others_untouched(devices, name):
    return all(linked(other) for other in devices if other != name)


def test_a_password_reset_unlinks_the_officials_phones(api, world, devices, push_outbox):
    target = world.users["pm_kolakaluru"]
    Device.objects.create(fcm_token="tok-second-phone", platform="ios", user=target)
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json")
    assert response.status_code == 200, response.json()
    assert not Device.objects.filter(user=target).exists()
    assert others_untouched(devices, "pm_kolakaluru")
    # Nothing reaches the old phones any more.
    assert send_push_to_user(target.id, "T", "B", {}) == 0
    assert push_outbox == []


def test_log_out_everywhere_unlinks_the_officials_phones(api, world, devices):
    response = api(world.users["ma_tenali"]).post("/api/v1/auth/logout/", {"all_devices": True}, format="json")
    assert response.status_code == 204
    assert not linked("ma_tenali")
    assert others_untouched(devices, "ma_tenali")


def test_logging_out_this_phone_only_keeps_the_other_links(api, world, devices):
    # The app removes its own device itself; the server leaves the rest alone.
    response = api(world.users["ma_tenali"]).post("/api/v1/auth/logout/", {}, format="json")
    assert response.status_code == 204
    assert linked("ma_tenali")


def test_blocking_unlinks_the_officials_phones(api, world, devices):
    target = world.users["pm_kolakaluru"]
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/block/", {"reason": "x"}, format="json")
    assert response.status_code == 200, response.json()
    assert not linked("pm_kolakaluru")
    assert others_untouched(devices, "pm_kolakaluru")


def test_too_many_wrong_current_passwords_unlink_the_phones(api, world, devices):
    client = api(world.users["va_kolakaluru"])
    for _ in range(settings.PASSWORD_CHANGE_MAX_FAILURES):
        client.post(
            "/api/v1/auth/password/change/",
            {"current_password": "Wrong-Password-1!", "new_password": NEW_PASSWORD},
            format="json",
        )
    assert not linked("va_kolakaluru")
    assert others_untouched(devices, "va_kolakaluru")


def test_an_own_password_change_on_the_console_unlinks_every_phone(world, devices, client):
    user = world.users["super"]
    client.get("/api/v1/auth/csrf/")
    csrf = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
    login = client.post("/api/v1/auth/session/login/", {"username": "super", "password": TEST_PASSWORD},
                        content_type="application/json", **csrf)
    assert login.status_code == 200, login.json()
    changed = client.post("/api/v1/auth/password/change/",
                          {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
                          content_type="application/json", **csrf)
    assert changed.status_code == 200, changed.json()
    assert not Device.objects.filter(user=user).exists()
    assert others_untouched(devices, "super")


def test_an_own_password_change_in_the_app_keeps_only_the_phone_in_hand(api, world, devices):
    user = world.users["va_kolakaluru"]
    Device.objects.create(fcm_token="tok-other-phone", platform="android", user=user)
    response = api(user).post(
        "/api/v1/auth/password/change/", {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert not Device.objects.filter(user=user).exists()
    # The app links the phone in hand again at once with its new token (mobile: syncPush after the change).
    phone = api()
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
    again = phone.post("/api/v1/devices/", {"fcm_token": "tok-va_kolakaluru", "platform": "android"}, format="json")
    assert again.status_code in (200, 201), again.json()
    assert list(Device.objects.filter(user=user).values_list("fcm_token", flat=True)) == ["tok-va_kolakaluru"]
    assert others_untouched(devices, "va_kolakaluru")


def test_the_phone_links_again_at_the_next_login(api, world, devices):
    target = world.users["pm_kolakaluru"]
    reset = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json")
    assert not linked("pm_kolakaluru")
    target.refresh_from_db()
    changed = api(target).post(
        "/api/v1/auth/password/change/",
        {"current_password": reset.json()["temporary_password"], "new_password": NEW_PASSWORD},
        format="json",
    )
    assert changed.status_code == 200, changed.json()
    target.refresh_from_db()
    registered = api(target).post("/api/v1/devices/", {"fcm_token": "tok-pm_kolakaluru", "platform": "android"}, format="json")
    assert registered.status_code == 201, registered.json()
    assert Device.objects.get(fcm_token="tok-pm_kolakaluru").user == target


def test_a_citizen_phone_is_never_touched(api, world, devices):
    citizen = Device.objects.create(fcm_token="tok-citizen", platform="android", village_id=1)
    target = world.users["pm_kolakaluru"]
    api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json")
    assert Device.objects.filter(pk=citizen.pk).exists()
