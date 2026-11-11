"""The console lists handled contact messages only for a while (decision of 2026-11-11, like the audit log).

Unhandled messages are always listed; handled ones for CONTACT_MESSAGE_HANDLED_VISIBLE_DAYS after they
were handled. Nothing is deleted.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.web_public.models import ContactMessage

pytestmark = pytest.mark.django_db

LIST = "/api/v1/admin/contact-messages/"


def message(name, *, created_days=0, handled_days=None, handled_at_missing=False):
    row = ContactMessage.objects.create(name=name, mobile="9876543210", message="మా గ్రామాన్ని చేర్చండి")
    now = timezone.now()
    values = {"created_at": now - timedelta(days=created_days)}
    if handled_days is not None or handled_at_missing:
        values["handled"] = True
        values["handled_at"] = None if handled_at_missing else now - timedelta(days=handled_days)
    ContactMessage.objects.filter(pk=row.pk).update(**values)
    return row.pk


@pytest.fixture
def messages(settings):
    settings.CONTACT_MESSAGE_HANDLED_VISIBLE_DAYS = 180
    return {
        "old_unhandled": message("పాతది", created_days=500),
        "recent_handled": message("Ravi", created_days=30, handled_days=20),
        "old_handled": message("Sita", created_days=400, handled_days=200),
        "handled_no_date": message("Old row", created_days=300, handled_at_missing=True),
    }


def names(api, world, query=""):
    data = api(world.users["super"]).get(LIST + query).json()
    return {row["id"] for row in data["results"]}, data


def test_unhandled_always_handled_only_while_recent(api, world, messages):
    shown, data = names(api, world)
    # A handled message without a date (older data) cannot be aged, so it stays listed.
    assert shown == {messages["old_unhandled"], messages["recent_handled"], messages["handled_no_date"]}
    assert data["count"] == 3
    assert data["handled_visible_days"] == 180


def test_filters_and_search_never_bring_back_old_handled_messages(api, world, messages):
    assert names(api, world, "?status=handled")[0] == {messages["recent_handled"], messages["handled_no_date"]}
    assert names(api, world, "?status=new")[0] == {messages["old_unhandled"]}
    assert names(api, world, "?q=Sita")[0] == set()


def test_nothing_is_deleted_and_it_can_be_opened_again_by_id(api, world, messages):
    assert ContactMessage.objects.count() == 4
    response = api(world.users["super"]).patch(f"{LIST}{messages['old_handled']}/", {"handled": False}, format="json")
    assert response.status_code == 200
    assert messages["old_handled"] in names(api, world)[0]  # unhandled again: listed again


def test_the_days_come_from_the_setting(api, world, messages, settings):
    settings.CONTACT_MESSAGE_HANDLED_VISIBLE_DAYS = 365
    shown, data = names(api, world)
    assert shown == set(messages.values())
    assert data["handled_visible_days"] == 365
