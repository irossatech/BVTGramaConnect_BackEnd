"""Fixes from the security gap review of 2026-10-27 (docs/security-review.md): G4, G9, G11, G13, G14, G15."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.audit.services import record
from conftest import TEST_PASSWORD
from grama.csvsafe import safe_cell, safe_writer

REPO = Path(__file__).resolve().parents[2]
CHANGE = "/api/v1/auth/password/change/"
NEW_PASSWORD = "Kolakaluru-Panchayat-77"


def code(response):
    return response.json()["error"]["code"]


# --- G15: CSV cells never run as formulas -----------------------------------------------------------


@pytest.mark.parametrize("value", ['=HYPERLINK("http://evil","click")', "+91 cmd", "-2+3", "@SUM(A1)", "\tx", "\rx"])
def test_formula_starts_get_an_apostrophe(value):
    assert safe_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["గ్రామ సభ", "Tenali", "", "a=b", 42, -5, None])
def test_other_cells_stay_as_they_are(value):
    assert safe_cell(value) == value  # numbers, even negative ones, still add up in Excel


def test_safe_writer_writes_rows():
    buffer = io.StringIO()
    writer = safe_writer(buffer)
    writer.writerow(["=1+1", "కొలకలూరు", 3])
    writer.writerows([["@x"], ["ok"]])
    assert buffer.getvalue().splitlines() == ["'=1+1,కొలకలూరు,3", "'@x", "ok"]


@pytest.mark.django_db
def test_audit_export_neutralises_formulas(world, api):
    record("post.published", target_label='=HYPERLINK("http://evil","click")')
    response = api(world.users["super"]).get("/api/v1/audit/export/")
    body = response.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in body and ',=HYPERLINK' not in body


# --- G14: the export's audit entry keeps no search text --------------------------------------------------


@pytest.mark.django_db
def test_audit_export_entry_has_no_search_text(world, api):
    api(world.users["super"]).get("/api/v1/audit/export/", {"q": "9000010001", "type": "users", "from": "2026-10-01"})
    entry = AuditLog.objects.filter(action="report.exported").latest("created_at")
    assert entry.data == {"report": "audit", "filters": {"type": "users", "from": "2026-10-01", "q": "(given)"}}
    assert "9000010001" not in str(entry.data)


# --- G13: "via" comes from the authenticator, never a header -----------------------------------------------


@pytest.mark.django_db
def test_a_header_cannot_label_an_app_request_as_console(world, api):
    client = api(world.users["super"])  # JWT, like the app
    client.get("/api/v1/audit/export/", HTTP_X_CLIENT="web_admin", HTTP_X_GC_CLIENT="console")
    assert AuditLog.objects.filter(action="report.exported").latest("created_at").via == "app"


# --- G4: API schema and Swagger -------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_docs_are_open_while_api_docs_public(client, settings):
    settings.API_DOCS_PUBLIC = True  # dev.py: laptops and the qa server
    assert client.get("/api/schema/", HTTP_ACCEPT="application/json").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/schema/", "/api/docs/"])
def test_api_docs_are_for_console_users_in_production(world, api, settings, path):
    settings.API_DOCS_PUBLIC = False  # base.py and prod.py
    assert api().get(path).status_code == 401
    assert api(world.users["va_kolakaluru"]).get(path).status_code == 403  # an official, but not a console user
    assert api(world.users["super"]).get(path, HTTP_ACCEPT="application/json, text/html").status_code == 200


# --- G9: wrong current password on "Change password" ----------------------------------------------------


def change(client, current):
    return client.post(CHANGE, {"current_password": current, "new_password": NEW_PASSWORD}, format="json")


def app_login(username, password=TEST_PASSWORD) -> APIClient:
    tokens = APIClient().post("/api/v1/auth/login/", {"username": username, "password": password}, format="json").json()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


@pytest.mark.django_db
def test_five_wrong_current_passwords_end_every_session(world, settings):
    user = world.users["va_kolakaluru"]
    client = app_login(user.username)
    other_phone = app_login(user.username)
    for _ in range(settings.PASSWORD_CHANGE_MAX_FAILURES - 1):
        wrong = change(client, "Not-The-Password-1")
        assert wrong.status_code == 400 and code(wrong) == "invalid_credentials"
    ended = change(client, "Not-The-Password-1")
    assert ended.status_code == 401 and code(ended) == "session_revoked"
    assert other_phone.get("/api/v1/me/").status_code == 401  # every session, not only this one
    user.refresh_from_db()
    assert user.check_password(TEST_PASSWORD)  # nothing changed
    actions = list(AuditLog.objects.filter(target_id=str(user.pk), action__startswith="auth.password_change")
                   .values_list("action", flat=True))
    assert actions.count("auth.password_change_failed") == 5 and actions.count("auth.password_change_locked") == 1
    assert not any("Not-The-Password" in str(e.data) for e in AuditLog.objects.all())


@pytest.mark.django_db
def test_after_logging_in_again_the_count_starts_over(world, settings):
    user = world.users["va_kolakaluru"]
    client = app_login(user.username)
    for _ in range(settings.PASSWORD_CHANGE_MAX_FAILURES):
        change(client, "Not-The-Password-1")
    client = app_login(user.username)
    assert code(change(client, "Not-The-Password-1")) == "invalid_credentials"  # 1 of 5 again, not ended
    assert change(client, TEST_PASSWORD).status_code == 200  # the right one still works


@pytest.mark.django_db
def test_old_wrong_tries_leave_the_window(world, settings):
    from datetime import timedelta

    from django.utils import timezone

    user = world.users["va_kolakaluru"]
    client = app_login(user.username)
    for _ in range(settings.PASSWORD_CHANGE_MAX_FAILURES - 1):
        change(client, "Not-The-Password-1")
    long_ago = timezone.now() - timedelta(minutes=settings.PASSWORD_CHANGE_WINDOW_MINUTES + 1)
    AuditLog.objects.filter(action="auth.password_change_failed").update(created_at=long_ago)
    assert code(change(client, "Not-The-Password-1")) == "invalid_credentials"


# --- G11 and the secret key: production settings ---------------------------------------------------------


def import_prod(secret_key: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "SECRET_KEY": secret_key,
        "ALLOWED_HOSTS": "example.in",
        "DATABASE_URL": "sqlite:///prod-check.sqlite3",
        "MEDIA_ROOT": "/tmp/media",
        "STATIC_ROOT": "/tmp/static",
    }
    return subprocess.run(
        [sys.executable, "-c", "import grama.settings.prod as p; print(sorted(p.SILENCED_SYSTEM_CHECKS))"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("key", [
    "change-me-to-a-long-random-string",
    "django-insecure-dev-only-0f3a9c1e7b5d4f2a8e6c9b1d3f5a7c9e",
    "django-insecure-" + "a1b2c3d4e5" * 5,
    "short-but-random-Q7x9",
    "x" * 60,
])
def test_production_refuses_known_or_weak_keys(key):
    result = import_prod(key)
    assert result.returncode != 0 and "SECRET_KEY is an example value or too weak" in result.stderr


def test_production_starts_with_a_random_key():
    import secrets

    result = import_prod(secrets.token_urlsafe(50))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['security.W004', 'security.W008']"  # only what Nginx does instead
