"""A full session writes no password, token, full mobile number or full email to the server log
(CLAUDE.md "Always"; project rule "No personal data in URLs or logs"; audit stage S6, BVTGC-17).

One test walks the paths that handle secrets and personal data: app login (a wrong password first,
then the right one), refresh and logout; console login; a password change; an admin creating an
official with a typed temporary password and resetting another one's password; a failed login with
a password typed into the username box; a citizen phone registering, posting and asking for a
service with its name, mobile, email and address. Every log record of every logger, at every level,
is caught while it runs. None of the secrets or personal values may appear in any of them, nor in a
traceback attached to one.
"""

import logging

import pytest
from rest_framework.test import APIClient

from apps.citizens.tests.conftest import Phone, register
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

V1 = "/api/v1"
WRONG_PASSWORD = "Wrong#Guess-2026"
NEW_PASSWORD = "Nava#Pass-2026x"
TYPED_TEMPORARY = "Temp#Pass-2026x"
TYPED_INTO_USERNAME = "Secret#Typed-2026"
NEW_MOBILE = "9123456780"
NEW_EMAIL = "kotha.sabhyudu@example.org"
CITIZEN = {"name": "Lakshmi Devi", "phone": "9876543210", "email": "lakshmi.devi@example.com",
           "address": "4-12, Temple Street, Kolakaluru"}


@pytest.fixture
def log_records(monkeypatch):
    """Every LogRecord any logger handles while the test runs, whatever its level or handlers."""
    caught = []
    handle = logging.Logger.handle

    def catch(logger, record):
        caught.append(record)
        return handle(logger, record)

    monkeypatch.setattr(logging.Logger, "handle", catch)
    loggers = [logging.getLogger()] + [
        logger for logger in logging.Logger.manager.loggerDict.values() if isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        monkeypatch.setattr(logger, "level", logging.DEBUG)
        monkeypatch.setattr(logger, "disabled", False)
    previous = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    logging.Logger.manager._clear_cache()
    yield caught
    logging.disable(previous)
    logging.Logger.manager._clear_cache()


def rendered(record: logging.LogRecord) -> str:
    """The record as a handler would write it, traceback included, plus its raw arguments."""
    text = logging.Formatter("%(name)s %(levelname)s %(message)s").format(record)
    return f"{text} {record.args!r} {getattr(record, '__dict__', {})!r}"


def test_a_full_session_leaves_no_secret_or_personal_data_in_the_log(
    world, log_records, django_capture_on_commit_callbacks,
):
    secrets = {"password": TEST_PASSWORD, "wrong password": WRONG_PASSWORD, "new password": NEW_PASSWORD,
               "typed temporary password": TYPED_TEMPORARY, "text typed as a username": TYPED_INTO_USERNAME,
               "new official's mobile": NEW_MOBILE, "new official's email": NEW_EMAIL,
               "citizen's mobile": CITIZEN["phone"], "citizen's email": CITIZEN["email"]}
    app = APIClient()

    # App login: a wrong password, then the right one; refresh; logout.
    assert app.post(f"{V1}/auth/login/", {"username": "va_kolakaluru", "password": WRONG_PASSWORD},
                    format="json").status_code == 400  # invalid_credentials
    tokens = app.post(f"{V1}/auth/login/", {"username": "va_kolakaluru", "password": TEST_PASSWORD},
                      format="json").json()
    secrets["access token"], secrets["refresh token"] = tokens["access"], tokens["refresh"]
    fresh = app.post(f"{V1}/auth/refresh/", {"refresh": tokens["refresh"]}, format="json").json()
    secrets["refreshed access token"], secrets["rotated refresh token"] = fresh["access"], fresh["refresh"]
    admin = APIClient(HTTP_AUTHORIZATION=f"Bearer {fresh['access']}")

    # A password typed into the username box (audit BE-SEC-007).
    assert app.post(f"{V1}/auth/login/", {"username": TYPED_INTO_USERNAME, "password": TEST_PASSWORD},
                    format="json").status_code == 400  # invalid_credentials

    # Console login with a session.
    console = APIClient()
    console.get(f"{V1}/auth/csrf/")
    assert console.post(f"{V1}/auth/session/login/", {"username": "super", "password": TEST_PASSWORD},
                        format="json").status_code == 200
    secrets["console session id"] = console.cookies["sessionid"].value

    # An admin creates an official with a typed temporary password, and resets another one's.
    created = admin.post(f"{V1}/users/", {
        "name": "Kotha Sabhyudu", "username": "kotha.sabhyudu", "role": "PANCHAYAT_MEMBER",
        "area": world.area_json("kolakaluru"), "password": TYPED_TEMPORARY,
        "mobile": NEW_MOBILE, "email": NEW_EMAIL,
    }, format="json")
    assert created.status_code == 201, created.json()
    reset = admin.post(f"{V1}/users/{world.users['pm_kolakaluru'].id}/reset-password/", {}, format="json")
    assert reset.status_code == 200, reset.json()
    secrets["generated temporary password"] = reset.json()["temporary_password"]

    # The official changes their own password, then logs out.
    member = APIClient(HTTP_AUTHORIZATION=f"Bearer {APIClient().post(f'{V1}/auth/login/', {'username': 'pm_kaza', 'password': TEST_PASSWORD}, format='json').json()['access']}")
    assert member.post(f"{V1}/auth/password/change/", {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
                       format="json").status_code == 200
    assert admin.post(f"{V1}/auth/logout/", {"refresh": fresh["refresh"]}, format="json").status_code in (200, 204)

    # A citizen phone registers, posts and asks for a service.
    registered = register(world).json()
    secrets["citizen posting token"] = registered["token"]
    phone = Phone(registered["token"], registered["device"]["id"])
    with django_capture_on_commit_callbacks(execute=True):
        assert phone.post().status_code == 201
        asked = phone.client.post(f"{V1}/citizen/service-requests/", {"service": "aadhaar", **CITIZEN}, format="json")
    assert asked.status_code == 201, asked.json()

    # Something was logged (the 401s at least), so the check below is not empty.
    assert log_records, "no log record was caught: the capture is broken"
    text = "\n".join(rendered(record) for record in log_records)
    leaked = sorted(name for name, value in secrets.items() if value and value in text)
    assert not leaked, f"found in the log: {leaked}"
