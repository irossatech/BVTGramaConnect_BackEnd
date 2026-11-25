"""GET /app-config/: the public app settings, before any login (contract section 5)."""

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
URL = "/api/v1/app-config/"
SETTINGS = "/api/v1/admin/settings/"


def test_without_a_login_both_languages_are_on_by_default(db):
    response = APIClient().get(URL)
    assert response.status_code == 200
    # min_version / latest_version (BVTGC-90): null until the server sets them, so no update prompt.
    # topic_prefix (BVTGC-69): "" by default, as in production. ads_banner_limit (BVTGC-92): 0 = no limit.
    # The wallet fields (BVTGC-129): off, everywhere, and the consent notice's version.
    assert response.json() == {
        "languages": {"te": True, "en": True}, "min_version": None, "latest_version": None, "topic_prefix": "",
        "ads_banner_limit": 0, "wallet_enabled": False, "wallet_pilot_mandals": [], "wallet_consent_version": "2026-11-25",
    }
    assert "public" in response["Cache-Control"] and "max-age=300" in response["Cache-Control"]


def test_the_super_admins_english_switch_reaches_it(world, api):
    admin = api(world.users["super"])
    assert admin.patch(SETTINGS, {"languages": {"en": False}}, format="json").status_code == 200
    assert APIClient().get(URL).json()["languages"] == {"te": True, "en": False}
    admin.patch(SETTINGS, {"languages": {"en": True}}, format="json")
    assert APIClient().get(URL).json()["languages"]["en"] is True


def test_a_stale_or_bad_token_never_breaks_it(db):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
    assert client.get(URL).status_code == 200


def test_it_carries_only_what_the_apps_need(world, api):
    data = APIClient().get(URL).json()
    # Escalation hours and the daily limit stay Super Admin only; the update rules (BVTGC-90) and the
    # topic prefix (BVTGC-69) and the banner limit (BVTGC-92) are for every app.
    assert set(data) == {
        "languages", "min_version", "latest_version", "topic_prefix", "ads_banner_limit",
        "wallet_enabled", "wallet_pilot_mandals", "wallet_consent_version",
    }


# --- Push topic prefix (BVTGC-69) ------------------------------------------------------------------


def test_the_topic_prefix_is_the_one_the_server_sends_with(db, settings):
    """qa: the app is told "qa_" and subscribes to qa_village_12, the very topic qa sends to."""
    from apps.notifications.push import topic_for

    settings.FCM_TOPIC_PREFIX = "qa_"
    prefix = APIClient().get(URL).json()["topic_prefix"]
    assert prefix == "qa_"
    assert prefix + "village_12" == topic_for("VILLAGE", 12)


def test_production_tells_the_app_plain_topics(db, settings):
    from apps.notifications.push import topic_for

    settings.FCM_TOPIC_PREFIX = ""
    prefix = APIClient().get(URL).json()["topic_prefix"]
    assert prefix == ""
    assert prefix + "district_3" == topic_for("DISTRICT", 3) == "district_3"
