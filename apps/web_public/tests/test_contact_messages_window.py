"""Which contact messages the console lists (2026-11-19, the rule service requests follow).

Unhandled messages stay listed, however old, until someone marks them handled. A handled message is
listed for CONTACT_MESSAGES_VISIBLE_DAYS (90) after it was handled, then kept but not listed. Nothing is
deleted. (Before 2026-11-19 every message dropped off 90 days after it arrived, handled or not.)
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.web_public.models import ContactMessage

from .test_showcase_and_console import INBOX, message

pytestmark = pytest.mark.django_db


def aged(days, handled_days_ago=None):
    """A message that arrived `days` ago; handled `handled_days_ago` days ago when given."""
    item = message(text=f"{days} రోజుల క్రితం")
    now = timezone.now()
    fields = {"created_at": now - timedelta(days=days)}
    if handled_days_ago is not None:
        fields.update(handled=True, handled_at=now - timedelta(days=handled_days_ago))
    ContactMessage.objects.filter(pk=item.pk).update(**fields)
    return item.pk


def listed(api, world, **params):
    response = api(world.users["super"]).get(INBOX, params)
    assert response.status_code == 200
    return {row["id"] for row in response.json()["results"]}, response.json()["count"]


def test_unhandled_messages_stay_however_old(world, api):
    recent, old, very_old = aged(1), aged(91), aged(400)
    assert listed(api, world) == ({recent, old, very_old}, 3)
    assert listed(api, world, status="new") == ({recent, old, very_old}, 3)
    assert listed(api, world, q="400")[1] == 1  # search finds an old unhandled one too


def test_handled_messages_drop_off_90_days_after_handling(world, api):
    handled_recently = aged(200, handled_days_ago=10)  # old message, handled 10 days ago: still listed
    handled_long_ago = aged(200, handled_days_ago=95)
    assert listed(api, world) == ({handled_recently}, 1)
    assert listed(api, world, status="handled") == ({handled_recently}, 1)
    assert ContactMessage.objects.filter(pk=handled_long_ago).exists()  # kept, not deleted


def test_marking_an_old_message_handled_does_not_hide_it_at_once(world, api):
    pk = aged(150)
    response = api(world.users["super"]).patch(f"{INBOX}{pk}/", {"handled": True}, format="json")
    assert response.status_code == 200
    assert pk in listed(api, world, status="handled")[0]


def test_reopening_brings_a_handled_message_back(world, api):
    pk = aged(200, handled_days_ago=120)
    assert pk not in listed(api, world)[0]
    api(world.users["super"]).patch(f"{INBOX}{pk}/", {"handled": False}, format="json")
    assert pk in listed(api, world, status="new")[0]


def test_a_handled_message_without_a_date_counts_from_its_arrival(world, api):
    pk = aged(120)
    ContactMessage.objects.filter(pk=pk).update(handled=True, handled_at=None)
    assert pk not in listed(api, world)[0]


def test_the_days_follow_the_setting(world, api, settings):
    pk = aged(100, handled_days_ago=40)
    settings.CONTACT_MESSAGES_VISIBLE_DAYS = 30
    assert pk not in listed(api, world)[0]
    settings.CONTACT_MESSAGES_VISIBLE_DAYS = 90
    assert pk in listed(api, world)[0]
