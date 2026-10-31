"""GET /app-config/: the public app settings, before any login (contract section 5)."""

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
URL = "/api/v1/app-config/"
SETTINGS = "/api/v1/admin/settings/"


def test_without_a_login_both_languages_are_on_by_default(db):
    response = APIClient().get(URL)
    assert response.status_code == 200
    assert response.json() == {"languages": {"te": True, "en": True}}
    assert "public" in response["Cache-Control"] and "max-age=300" in response["Cache-Control"]


def test_the_super_admins_english_switch_reaches_it(world, api):
    admin = api(world.users["super"])
    assert admin.patch(SETTINGS, {"languages": {"en": False}}, format="json").status_code == 200
    assert APIClient().get(URL).json() == {"languages": {"te": True, "en": False}}
    admin.patch(SETTINGS, {"languages": {"en": True}}, format="json")
    assert APIClient().get(URL).json()["languages"]["en"] is True


def test_a_stale_or_bad_token_never_breaks_it(db):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
    assert client.get(URL).status_code == 200


def test_it_carries_only_what_the_apps_need(world, api):
    data = APIClient().get(URL).json()
    assert set(data) == {"languages"}  # escalation hours and the daily limit stay Super Admin only
