"""Which contact messages the console lists (decision of 2026-11-20).

Every message, however old, handled or not; nothing is hidden by age and nothing is deleted. (Before:
a handled message dropped off 90 days after it was handled, since 2026-11-19; every message 90 days
after it arrived before that.)
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


def test_handled_messages_stay_however_long_ago(world, api):
    handled_recently = aged(200, handled_days_ago=10)
    handled_long_ago = aged(500, handled_days_ago=400)
    without_date = aged(300)
    ContactMessage.objects.filter(pk=without_date).update(handled=True, handled_at=None)
    everything = {handled_recently, handled_long_ago, without_date}
    assert listed(api, world) == (everything, 3)
    assert listed(api, world, status="handled") == (everything, 3)
    assert listed(api, world, q="500")[1] == 1


def test_marking_and_reopening_keep_an_old_message_listed(world, api):
    pk = aged(400)
    client = api(world.users["super"])
    assert client.patch(f"{INBOX}{pk}/", {"handled": True}, format="json").status_code == 200
    assert pk in listed(api, world, status="handled")[0]
    assert client.patch(f"{INBOX}{pk}/", {"handled": False}, format="json").status_code == 200
    assert pk in listed(api, world, status="new")[0]
