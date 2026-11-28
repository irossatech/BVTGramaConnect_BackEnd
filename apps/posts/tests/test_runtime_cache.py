"""Runtime settings are read from the database once per RUNTIME_CACHE_SECONDS, and a save takes effect at
once in the saving process (audit finding BE-DB-027)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.posts.models import SettingOverride
from apps.posts.services import runtime

pytestmark = pytest.mark.django_db


def reads(name):
    with CaptureQueriesContext(connection) as ctx:
        value = runtime(name)
    return value, sum("posts_settingoverride" in q["sql"] for q in ctx.captured_queries)


def test_a_setting_is_read_once_then_kept(settings):
    settings.REVIEW_ESCALATION_HOURS = 24
    assert reads("REVIEW_ESCALATION_HOURS") == (24, 1)
    assert reads("REVIEW_ESCALATION_HOURS") == (24, 0)  # from the cache, no query


def test_saving_a_setting_takes_effect_at_once(settings, world):
    assert runtime("CITIZEN_POSTS_PER_DAY") == settings.CITIZEN_POSTS_PER_DAY  # now cached
    SettingOverride.objects.update_or_create(key="CITIZEN_POSTS_PER_DAY", defaults={"value": 9})
    assert runtime("CITIZEN_POSTS_PER_DAY") == 9
    SettingOverride.objects.filter(key="CITIZEN_POSTS_PER_DAY").get().delete()
    assert runtime("CITIZEN_POSTS_PER_DAY") == settings.CITIZEN_POSTS_PER_DAY


def test_the_console_screen_shows_the_new_value_at_once(api, world):
    client = api(world.users["super"])
    assert client.get("/api/v1/admin/settings/").json()["review_escalation_hours"] == 24
    client.patch("/api/v1/admin/settings/", {"review_escalation_hours": 48}, format="json")
    assert client.get("/api/v1/admin/settings/").json()["review_escalation_hours"] == 48
    assert runtime("REVIEW_ESCALATION_HOURS") == 48


def test_other_names_are_never_cached(settings):
    settings.MAX_PHOTOS_PER_POST = 5
    assert runtime("MAX_PHOTOS_PER_POST") == 5
    settings.MAX_PHOTOS_PER_POST = 7
    assert runtime("MAX_PHOTOS_PER_POST") == 7  # a plain Django setting, as before
