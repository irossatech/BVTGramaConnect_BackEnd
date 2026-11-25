"""Category and settings changes are audited with the channel they really came from (BVTGC-99).

The Super Admin changes categories and settings from the console and from the app. The audit entry says
"console" for a console session and "app" for the app's bearer token; the server decides, never the client.
"""

import pytest

from apps.audit.models import AuditLog
from apps.posts.models import Category

pytestmark = pytest.mark.django_db
ADMIN = "/api/v1/admin/categories/"
SETTINGS = "/api/v1/admin/settings/"
CHANNELS = ["app", "console"]


def client_for(channel, api, console, user):
    return api(user) if channel == "app" else console(user)


def last(action):
    return AuditLog.objects.filter(action=action).latest("created_at")


@pytest.mark.parametrize("channel", CHANNELS)
def test_a_new_category_records_its_channel(api, console, world, channel):
    body = {"key": f"events_{channel}", "name_te": "కార్యక్రమాలు", "name_en": "Events", "short_name_te": "కార్య",
            "short_name_en": "Events", "order": 9}
    response = client_for(channel, api, console, world.users["super"]).post(ADMIN, body, format="json")
    assert response.status_code == 201, response.json()
    assert last("category.created").via == channel


@pytest.mark.parametrize("channel", CHANNELS)
def test_a_changed_category_records_its_channel(api, console, world, channel):
    news = Category.objects.get(key="news")
    response = client_for(channel, api, console, world.users["super"]).patch(
        f"{ADMIN}{news.id}/", {"name_te": "వార్తలు"}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert last("category.updated").via == channel


@pytest.mark.parametrize("channel", CHANNELS)
def test_a_settings_change_records_its_channel(api, console, world, channel):
    response = client_for(channel, api, console, world.users["super"]).patch(
        SETTINGS, {"review_escalation_hours": 48}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert last("setting.changed").via == channel


def test_a_client_header_cannot_claim_the_console(api, world):
    client = api(world.users["super"])
    news = Category.objects.get(key="news")
    client.patch(f"{ADMIN}{news.id}/", {"name_te": "వార్తలు"}, format="json", HTTP_X_CLIENT="console")
    assert last("category.updated").via == "app"
