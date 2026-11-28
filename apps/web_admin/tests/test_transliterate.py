"""GET /admin/transliterate/: Telugu name suggestions through our server (telugu-name-suggestions plan)."""

import logging
import os

import pytest
import requests
from django.core.cache import caches

from apps.accounts.models import User
from apps.web_admin import transliteration
from apps.web_admin.transliteration import LocmemTransliterator

pytestmark = pytest.mark.django_db
URL = "/api/v1/admin/transliterate/"
cache = caches["names"]  # shared by every server process (BE-PERF-202)


@pytest.fixture(autouse=True)
def locmem(settings):
    settings.TRANSLITERATION_PROVIDER = "apps.web_admin.transliteration.LocmemTransliterator"
    cache.clear()
    LocmemTransliterator.calls = []
    LocmemTransliterator.failures = []
    LocmemTransliterator.result = "రవి కుమార్"
    yield LocmemTransliterator
    cache.clear()


@pytest.fixture
def admin(world, api):
    return api(world.users["super"])


def ask(client, text):
    return client.get(URL, {"text": text})


def test_super_admin_gets_the_telugu_spelling(admin, locmem):
    response = ask(admin, "  Ravi Kumar ")
    assert response.status_code == 200
    assert response.json() == {"te": "రవి కుమార్"}
    assert locmem.calls == ["Ravi Kumar"]  # trimmed before it leaves the server


@pytest.mark.parametrize("actor", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_every_official_role_may_ask(world, api, actor):
    assert ask(api(world.users[actor]), "Kothapalem").status_code == 200


def test_an_account_without_a_role_is_403_and_no_login_is_401(api):
    nobody = User.objects.create_user(username="norole", name="No Role", password="x-Strong-Pass-123")
    refused = ask(api(nobody), "Ravi Kumar")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "permission_denied"
    anonymous = ask(api(), "Ravi Kumar")
    assert anonymous.status_code == 401
    assert LocmemTransliterator.calls == []


@pytest.mark.parametrize("params", [{}, {"text": ""}, {"text": "   "}, {"text": "a" * 151}])
def test_bad_text_is_400(admin, params):
    response = admin.get(URL, params)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error" and "text" in error["fields"]
    assert LocmemTransliterator.calls == []


def test_150_characters_are_fine(admin):
    assert ask(admin, "a" * 150).status_code == 200


def test_the_same_text_in_another_case_comes_from_the_cache(admin, locmem):
    assert ask(admin, "Ravi Kumar").json() == {"te": "రవి కుమార్"}
    assert ask(admin, "RAVI KUMAR").json() == {"te": "రవి కుమార్"}
    assert locmem.calls == ["Ravi Kumar"]


def test_the_cache_never_holds_the_name_in_clear(admin):
    ask(admin, "Ravi Kumar")
    keys = list(cache._cache.keys())  # locmem cache internals, test only
    assert keys and not any("ravi" in key.lower() for key in keys)


@pytest.mark.parametrize("failure", [requests.Timeout("Ravi Kumar"), ValueError("bad reply"), RuntimeError("x")])
def test_a_provider_failure_is_an_empty_suggestion_and_is_not_cached(admin, locmem, failure):
    locmem.failures.append(failure)
    first = ask(admin, "Ravi Kumar")
    assert first.status_code == 200 and first.json() == {"te": ""}
    assert ask(admin, "Ravi Kumar").json() == {"te": "రవి కుమార్"}  # asked again
    assert len(locmem.calls) == 2


def test_off_answers_empty_and_never_calls_the_provider(admin, locmem, settings):
    settings.TRANSLITERATION_PROVIDER = "off"
    assert ask(admin, "Ravi Kumar").json() == {"te": ""}
    assert locmem.calls == []


def test_the_61st_call_in_a_minute_is_429(admin):
    for index in range(60):
        assert ask(admin, f"Name {index}").status_code == 200
    refused = ask(admin, "One more")
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "throttled"
    assert refused.json()["error"]["retry_after_s"] > 0
    assert "Retry-After" in refused


def test_a_failure_log_never_carries_the_text(admin, locmem):
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("grama.transliteration")
    logger.addHandler(handler)
    try:
        locmem.failures.append(requests.ConnectionError("could not reach for Ravi Kumar"))
        ask(admin, "Ravi Kumar")
    finally:
        logger.removeHandler(handler)
    assert len(records) == 1
    logged = records[0].getMessage()
    assert "locmem" in logged and "ConnectionError" in logged
    assert "Ravi" not in logged and "రవి" not in logged


@pytest.mark.skipif(not os.environ.get("LIVE_TRANSLITERATION"), reason="calls Google; set LIVE_TRANSLITERATION=1")
def test_live_google_input_tools(settings):
    settings.TRANSLITERATION_PROVIDER = "google_input_tools"
    assert transliteration.to_telugu("Ravi Kumar") == "రవి కుమార్"


def test_suggestions_are_kept_in_the_shared_names_cache_not_per_process(admin, locmem, settings):
    """On servers CACHES["names"] is a database table every worker reads (BE-PERF-202), so a name
    typed in one worker is not sent to Google again by another; the per-process default cache is unused."""
    from django.core.cache import caches as all_caches

    all_caches["default"].clear()
    ask(admin, "Ravi Kumar")
    assert list(cache._cache.keys())  # in "names"
    assert not list(all_caches["default"]._cache.keys())
    assert "names" in settings.CACHES


def test_on_servers_the_names_cache_is_a_database_table_that_keeps_telugu(settings):
    """The server setting (DEBUG off): a DatabaseCache table, made by migration web_admin 0002."""
    from django.core.cache import caches as all_caches
    from django.core.management import call_command
    from django.db import connection

    settings.CACHES = {**settings.CACHES, "names": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "grama_names_cache"}}
    call_command("createcachetable", verbosity=0)
    assert "grama_names_cache" in connection.introspection.table_names()
    names = all_caches["names"]
    names.set("translit:te:abc", "రవి కుమార్", 60)
    assert names.get("translit:te:abc") == "రవి కుమార్"
