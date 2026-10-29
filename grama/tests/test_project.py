"""Smoke tests for the project scaffold: settings, URL layout, web serving, user model."""

from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from grama.settings.base import database_config

TELUGU_NAME = "సురేష్ బాబు"


# --- API ---------------------------------------------------------------------


@pytest.mark.django_db
def test_health_is_public_and_reports_ok(client):
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.django_db
def test_openapi_schema_is_served(client):
    response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "BVT Grama Connect API"


@pytest.mark.django_db
def test_every_time_goes_out_in_india_standard_time(client):
    """Times a view returns directly (health) and through serializers both carry +05:30, never Z."""
    from datetime import datetime, timedelta

    body = client.get("/api/v1/health/").json()
    sent = datetime.fromisoformat(body["time"])
    assert body["time"].endswith("+05:30")
    assert sent.utcoffset() == timedelta(hours=5, minutes=30)


def test_the_renderer_converts_utc_to_ist():
    from datetime import datetime, timezone

    from grama.renderers import JSONRenderer

    noon_utc = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    assert JSONRenderer().render({"at": noon_utc}) == b'{"at":"2026-10-26T17:30:00+05:30"}'


def test_api_sends_telugu_as_raw_utf8():
    from rest_framework.renderers import JSONRenderer

    body = JSONRenderer().render({"name": TELUGU_NAME})
    assert TELUGU_NAME.encode("utf-8") in body
    assert b"\\u" not in body


# --- URL layout ----------------------------------------------------------------


@pytest.mark.django_db
def test_django_admin_lives_under_django_admin(client):
    response = client.get("/django-admin/login/")
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("bare, target", [("/console", "/console/"), ("/django-admin", "/django-admin/")])
def test_reserved_prefixes_are_not_swallowed_by_the_website(client, bare, target):
    response = client.get(bare)
    assert response.status_code == 301
    assert response["Location"] == target


# --- Ported web prototypes, served by Django only while SERVE_FILES is on --------


@pytest.fixture
def web_dirs(settings, tmp_path):
    public = tmp_path / "public"
    console = tmp_path / "console"
    public.mkdir()
    console.mkdir()
    settings.SERVE_FILES = True
    settings.WEB_PUBLIC_DIR = public
    settings.WEB_ADMIN_DIR = console
    return public, console


def test_placeholder_until_the_website_is_ported(client, web_dirs):
    response = client.get("/")
    assert response.status_code == 200
    assert b"not ported yet" in response.content


def test_website_files_are_served_byte_for_byte(client, web_dirs):
    public, _ = web_dirs
    page = f'<!doctype html><meta charset="utf-8"><x-dc>{TELUGU_NAME} {{{{ t.title }}}}</x-dc>'
    (public / "index.html").write_text(page, encoding="utf-8")
    (public / "support.js").write_text("console.log(1)", encoding="utf-8")

    index = client.get("/")
    assert index.status_code == 200
    assert b"".join(index.streaming_content) == page.encode("utf-8")

    script = client.get("/support.js")
    assert script.status_code == 200
    assert script["Content-Type"].startswith("text/javascript")


def test_console_is_served_under_console(client, web_dirs):
    _, console = web_dirs
    (console / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (console / "assets").mkdir()
    (console / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert client.get("/console/").status_code == 200
    logo = client.get("/console/assets/logo.png")
    assert logo.status_code == 200
    assert logo["Content-Type"] == "image/png"


def test_paths_cannot_escape_the_site_folder(client, web_dirs):
    public, _ = web_dirs
    (public.parent / "secret.txt").write_text("do not serve", encoding="utf-8")
    response = client.get("/assets/../../secret.txt")
    assert response.status_code in (400, 404)
    assert b"do not serve" not in response.content


def test_nothing_is_served_where_nginx_serves_files(client, web_dirs, settings, tmp_path):
    public, _ = web_dirs
    (public / "index.html").write_text("<!doctype html>", encoding="utf-8")
    settings.MEDIA_ROOT = tmp_path / "media"
    (settings.MEDIA_ROOT / "posts").mkdir(parents=True)
    (settings.MEDIA_ROOT / "posts" / "p.webp").write_bytes(b"RIFF")
    settings.SERVE_FILES = False  # production: prod.py
    assert client.get("/").status_code == 404
    assert client.get("/console/").status_code == 404
    assert client.get("/media/posts/p.webp").status_code == 404
    assert client.get("/static/admin/css/base.css").status_code == 404


# --- The qa server: DEBUG off, files still served by Django -----------------------


@pytest.fixture
def qa(settings, tmp_path):
    """DEBUG off with SERVE_FILES on, like the qa server's .env (pytest-django already runs with DEBUG off)."""
    settings.DEBUG = False
    settings.SERVE_FILES = True
    settings.MEDIA_ROOT = tmp_path / "media"
    (settings.MEDIA_ROOT / "services").mkdir(parents=True)
    return settings


def test_qa_keeps_serving_uploads_static_files_and_the_console(client, web_dirs, qa):
    _, console = web_dirs
    (console / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (qa.MEDIA_ROOT / "services" / "aadhaar-1-256.webp").write_bytes(b"RIFF....WEBP")
    logo = client.get("/media/services/aadhaar-1-256.webp")
    assert logo.status_code == 200 and logo["Content-Type"] == "image/webp"
    assert b"".join(logo.streaming_content) == b"RIFF....WEBP"
    assert client.get("/static/admin/css/base.css").status_code == 200  # Django admin and Swagger styles
    assert client.get("/console/").status_code == 200


def test_media_paths_cannot_escape_the_media_folder(client, qa, tmp_path):
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    response = client.get("/media/../secret.txt")
    assert response.status_code in (400, 404)
    assert b"do not serve" not in b"".join(getattr(response, "streaming_content", [response.content]))


@pytest.mark.django_db
def test_an_unknown_api_address_is_the_plain_json_404(client, qa):
    response = client.get("/api/v1/no-such-page/")
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": {"code": "not_found", "message": "No such address."}}
    assert b"urlpatterns" not in response.content and b"health" not in response.content


@pytest.mark.django_db
def test_a_crash_shows_nothing_internal(qa):
    from django.test import Client

    client = Client(raise_request_exception=False)
    with mock.patch("grama.views.connection.ensure_connection", side_effect=RuntimeError("db password=hunter2")):
        response = client.get("/api/v1/health/")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "server_error", "message": "Something went wrong on the server."}}
    assert b"hunter2" not in response.content and b"Traceback" not in response.content


def test_an_unknown_page_elsewhere_is_a_plain_404(client, qa):
    response = client.get("/django-admin-nope/x/")  # not under /api/: the website's address space
    assert response.status_code == 404
    assert b"urlpatterns" not in response.content and b"Using the URLconf" not in response.content


# --- Database URL helper -----------------------------------------------------------


def test_mysql_gets_utf8mb4_options():
    config = database_config("mysql://grama:pw@127.0.0.1:3306/grama")
    assert config["ENGINE"] == "django.db.backends.mysql"
    assert config["OPTIONS"]["charset"] == "utf8mb4"
    assert "STRICT_TRANS_TABLES" in config["OPTIONS"]["init_command"]
    assert config["TEST"]["CHARSET"] == "utf8mb4"


@pytest.mark.parametrize(
    "url, engine",
    [
        ("postgres://grama:pw@127.0.0.1:5432/grama", "django.db.backends.postgresql"),
        ("sqlite:////tmp/grama.sqlite3", "django.db.backends.sqlite3"),
    ],
)
def test_other_engines_get_no_mysql_options(url, engine):
    config = database_config(url)
    assert config["ENGINE"] == engine
    assert "charset" not in config.get("OPTIONS", {})
    assert "TEST" not in config


# --- User model ------------------------------------------------------------------


@pytest.mark.django_db
def test_user_logs_in_by_username_and_keeps_telugu_name():
    User = get_user_model()
    user = User.objects.create_user(
        username="Suresh.Babu", email="Suresh@Example.org", mobile="9000000003", name="Suresh Babu", name_te=TELUGU_NAME,
    )
    user.refresh_from_db()
    assert User.USERNAME_FIELD == "username"
    assert (user.username, user.email) == ("suresh.babu", "suresh@example.org")
    assert user.name_te == TELUGU_NAME
    assert user.preferred_language == "te"
    assert user.is_active and user.status == "ACTIVE"
    assert not user.has_usable_password()


@pytest.mark.django_db
def test_superuser_can_use_django_admin(client):
    User = get_user_model()
    admin = User.objects.create_superuser(
        username="dev", email="dev@example.org", name="Dev Admin", password="a-long-dev-password",
    )
    client.force_login(admin)
    assert client.get("/django-admin/accounts/user/add/").status_code == 200


@pytest.mark.parametrize("mobile", ["12345", "5123456789", "98765432101", "98765abcde"])
def test_mobile_must_be_ten_digits_starting_6_to_9(mobile):
    User = get_user_model()
    with pytest.raises(ValidationError):
        User(mobile=mobile, name="X").clean_fields(exclude=["password"])


# --- Settings ----------------------------------------------------------------------


def test_platform_rule_defaults_match_claude_md():
    assert settings.ADMIN_POSTS_AUTO_PUBLISH is True
    assert settings.REVIEW_FIRST_DECISION_WINS is True
    assert settings.REVIEW_ESCALATION_HOURS == 24
    assert settings.SUPER_ADMIN_CAN_POST is False
    assert settings.LANGUAGE_CODE == "te"
    assert [code for code, _ in settings.LANGUAGES] == ["te", "en"]
