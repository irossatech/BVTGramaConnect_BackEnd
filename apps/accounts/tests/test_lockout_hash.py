"""django-axes' tables keep a keyed hash of the username, never what was typed (BE-SEC-007)."""

import importlib

import pytest
from axes.models import AccessAttempt, AccessFailureLog
from django.apps import apps as django_apps
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.lockout import locked_usernames, login_key
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.accounts.migrations.0010_hash_axes_usernames")
LOGIN = "/api/v1/auth/login/"


def login(username, password="wrong"):
    return APIClient().post(LOGIN, {"username": username, "password": password}, format="json")


def stored_usernames():
    return set(AccessAttempt.objects.values_list("username", flat=True)) | set(
        AccessFailureLog.objects.values_list("username", flat=True)
    )


def test_login_key_is_a_keyed_hash_of_the_normalised_username(settings):
    key = login_key("  VA_Kaza ")
    assert key == login_key("va_kaza") and len(key) == 64 and "va_kaza" not in key
    assert login_key("") == "" and login_key(None) == ""
    settings.SECRET_KEY = settings.SECRET_KEY + "-other"
    assert login_key("va_kaza") != key  # keyed: not a plain SHA-256 anyone can recompute


def test_a_password_typed_as_username_never_reaches_the_axes_tables(world):
    login("Grama@2026-MySecret")
    login("va_kolakaluru")
    stored = stored_usernames()
    assert stored == {login_key("Grama@2026-MySecret"), login_key("va_kolakaluru")}
    assert not any("secret" in name.lower() or "kolakaluru" in name for name in stored)


def test_lockout_unlock_and_audit_still_work_with_the_hash(api, world):
    target = world.users["va_kolakaluru"]
    for _ in range(5):
        login(target.username)
    assert login(target.username, "any").status_code == 429
    assert locked_usernames([target.username, "ma_tenali"]) == {target.username}
    entry = AuditLog.objects.get(action="auth.locked_out")
    assert entry.target_id == str(target.id) and entry.data["username"] == target.username

    response = api(world.users["super"]).post(f"/api/v1/users/{target.id}/unlock/", {}, format="json")
    assert response.status_code == 200
    assert not AccessAttempt.objects.filter(username=login_key(target.username)).exists()


def test_migration_hashes_rows_written_before_the_fix(db):
    now = timezone.now()
    common = {"user_agent": "ua", "http_accept": "", "path_info": "/", "attempt_time": now}
    AccessFailureLog.objects.create(username="grama@2026-mysecret", ip_address="10.0.0.1", **common)
    AccessFailureLog.objects.create(username=login_key("va_kaza"), ip_address="10.0.0.1", **common)
    AccessAttempt.objects.create(
        username="va_kaza", ip_address="10.0.0.2", failures_since_start=5, get_data="", post_data="", **common
    )
    # A plain row whose hashed twin already exists: the twin is kept, the plain row goes.
    AccessAttempt.objects.create(
        username="ma_tenali", ip_address="10.0.0.3", failures_since_start=2, get_data="", post_data="", **common
    )
    AccessAttempt.objects.create(
        username=login_key("ma_tenali"), ip_address="10.0.0.3", failures_since_start=3,
        get_data="", post_data="", **common,
    )

    migration.hash_usernames(django_apps, None)
    migration.hash_usernames(django_apps, None)  # twice changes nothing more

    assert set(AccessFailureLog.objects.values_list("username", flat=True)) == {
        login_key("grama@2026-mysecret"), login_key("va_kaza"),
    }
    assert sorted(AccessAttempt.objects.values_list("username", "failures_since_start")) == sorted(
        [(login_key("va_kaza"), 5), (login_key("ma_tenali"), 3)]
    )
    assert locked_usernames(["va_kaza"]) == {"va_kaza"}
