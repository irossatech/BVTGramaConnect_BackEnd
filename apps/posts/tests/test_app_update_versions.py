"""App update rules in GET /app-config/ (BVTGC-90, audit CROSS-REL-001 / BVTGC-25).

The app compares its own build number (the 24 in 1.0.0+24) with min_version (blocking "Please update
the app") and latest_version ("Update available", with Later). Both come from the server's settings
and are null when not set, so nothing shows by default. A null, missing or malformed value shows
nothing in the app, so a slip on the server never locks anyone out.
"""

import importlib
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.notifications.models import Device

pytestmark = pytest.mark.django_db
CONFIG = "/api/v1/app-config/"


def test_nothing_to_show_by_default(api):
    response = api().get(CONFIG)
    assert response.status_code == 200
    data = response.json()
    assert data["min_version"] is None and data["latest_version"] is None
    assert data["languages"]["te"] is True  # what was there stays


def test_set_values_come_back_as_given(api, settings):
    settings.APP_LATEST_VERSION = "1.0.0+24"
    settings.APP_MIN_VERSION = "1.0.0+22"
    data = api().get(CONFIG).json()
    assert (data["latest_version"], data["min_version"]) == ("1.0.0+24", "1.0.0+22")


def test_still_public_and_cached(api, world):
    # No login needed, and a stale or wrong token is ignored, as before.
    response = api().get(CONFIG, HTTP_AUTHORIZATION="Bearer not-a-token")
    assert response.status_code == 200
    assert "public" in response["Cache-Control"] and "max-age=300" in response["Cache-Control"]


# --- Settings: a slip is caught at start, not hidden in the app -------------------------------------


def _reload_with(monkeypatch, **env):
    """(APP_LATEST_VERSION, APP_MIN_VERSION) as the settings read them with `env`; reset afterwards."""
    import grama.settings as base

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    try:
        loaded = importlib.reload(base)
        return loaded.APP_LATEST_VERSION, loaded.APP_MIN_VERSION
    finally:
        for name in env:
            monkeypatch.delenv(name)
        importlib.reload(base)


def test_settings_accept_a_version_with_its_build_number(monkeypatch):
    assert _reload_with(monkeypatch, APP_LATEST_VERSION="1.0.0+24", APP_MIN_VERSION="1.0.0+22") == ("1.0.0+24", "1.0.0+22")


@pytest.mark.parametrize("value", ["1.0.0", "24", "1.0.0+", "v1.0.0+24", "1.0.0+24 beta"])
def test_settings_refuse_a_version_without_a_build_number(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured):
        _reload_with(monkeypatch, APP_MIN_VERSION=value)


def test_settings_refuse_a_minimum_above_the_latest(monkeypatch):
    with pytest.raises(ImproperlyConfigured):
        _reload_with(monkeypatch, APP_LATEST_VERSION="1.0.0+24", APP_MIN_VERSION="1.0.1+25")


# --- Dashboard: how many old installs are left before raising APP_MIN_VERSION -------------------


def test_the_dashboard_counts_phones_by_app_version(api, world):
    def phone(token, version, user=None):
        return Device.objects.create(fcm_token=token, platform="android", app_version=version, user=user)

    phone("a", "1.0.0+24")
    phone("b", "1.0.0+24", world.users["va_kolakaluru"])
    phone("c", "1.0.0+21")
    phone("d", "")  # a build from before the app sent its version
    old = phone("e", "1.0.0+19")
    Device.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=31))  # not seen lately

    rows = api(world.users["super"]).get("/api/v1/admin/dashboard/").json()["app_versions"]
    assert rows == [
        {"version": "1.0.0+24", "phones": 2},
        {"version": "1.0.0+21", "phones": 1},
        {"version": "", "phones": 1},
    ]


def test_a_version_any_phone_can_send_never_breaks_the_dashboard(api, world):
    # BE-ERR-201: POST /devices/ needs no login, and "²".isdigit() is True while int("²") raises.
    from rest_framework.test import APIClient

    for token, version in (("x1", "1.0+²"), ("x2", "+"), ("x3", "9" * 20), ("x4", "1.0.0+24")):
        answer = APIClient().post("/api/v1/devices/", {"fcm_token": token, "platform": "android",
                                                       "app_version": version}, format="json")
        assert answer.status_code in (200, 201), answer.content
    answer = api(world.users["super"]).get("/api/v1/admin/dashboard/")
    assert answer.status_code == 200, answer.content
    rows = answer.json()["app_versions"]
    assert rows[0] == {"version": "1.0.0+24", "phones": 1}  # odd versions sort after the real builds
    assert {row["version"] for row in rows} == {"1.0+²", "+", "9" * 20, "1.0.0+24"}


def test_only_the_super_admin_sees_the_dashboard(api, world):
    assert api(world.users["da_guntur"]).get("/api/v1/admin/dashboard/").status_code == 403
