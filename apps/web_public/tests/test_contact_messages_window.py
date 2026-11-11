"""The console lists contact messages from the last CONTACT_MESSAGES_VISIBLE_DAYS (90) only.

Older messages stay in the database, as the owner decided (2026-11-11, like the audit log).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.web_public.models import ContactMessage

from .test_showcase_and_console import INBOX, message

pytestmark = pytest.mark.django_db


def aged(days):
    item = message(text=f"{days} రోజుల క్రితం")
    ContactMessage.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=days))
    return item.pk


def listed(api, world, **params):
    response = api(world.users["super"]).get(INBOX, params)
    assert response.status_code == 200
    return {row["id"] for row in response.json()["results"]}, response.json()["count"]


def test_only_the_last_90_days_are_listed_and_older_ones_are_kept(world, api):
    recent, edge, old = aged(1), aged(89), aged(91)
    assert listed(api, world) == ({recent, edge}, 2)
    assert listed(api, world, status="new") == ({recent, edge}, 2)
    assert listed(api, world, q="91")[1] == 0  # search does not reach older messages either
    assert ContactMessage.objects.filter(pk=old).exists()


def test_the_days_follow_the_setting(world, api, settings):
    recent, older = aged(10), aged(40)
    settings.CONTACT_MESSAGES_VISIBLE_DAYS = 30
    assert listed(api, world) == ({recent}, 1)
    settings.CONTACT_MESSAGES_VISIBLE_DAYS = 90
    assert listed(api, world) == ({recent, older}, 2)
