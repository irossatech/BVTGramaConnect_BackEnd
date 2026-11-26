"""Citizen wallet login, lockout and the forgotten password (BVTGC-124; contract section 21; FR-2, FR-7, FR-8,
FR-14).

Login by email (any case, Gmail variants) or mobile number with the password; the officials' limits apply
as they are, with the email and the number counting as one account. A forgotten password is reset with an
email code; the answer never tells whether a login is registered.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenAccount
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel
from apps.posts.models import CITIZEN_ROLE
from apps.wallet.models import Wallet
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/citizen/auth/login/"
FORGOT = "/api/v1/citizen/auth/password/forgot/"
RESET = "/api/v1/citizen/auth/password/reset/"
PASSWORD = "48213957"


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    return settings


def make_citizen(world, *, n: int = 1, email: str = "lakshmi.devi@gmail.com", mobile: str = "9876543210", **extra) -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.test{n}", name="లక్ష్మి", password=PASSWORD)
    village = world.ref("kolakaluru")
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, village.id))
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=village.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email),
        email_verified_at=now, mobile_hash=hash_mobile(mobile), mobile_last4=mobile[-4:], referral_code=f"CODE{n:04d}",
        **extra,
    )
    Wallet.objects.create(account=account)
    return account


@pytest.fixture
def citizen(world, wallet_on):
    return make_citizen(world)


def err(response) -> str:
    return response.json()["error"]["code"]


def log_in(api, login, password=PASSWORD):
    return api().post(LOGIN, {"login": login, "password": password}, format="json")


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


# --- The switch -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [LOGIN, FORGOT, RESET])
def test_hidden_while_the_wallet_is_off(api, world, url):
    response = api().post(url, {"login": "x@gmail.com", "password": "x", "code": "1", "new_password": "x"}, format="json")
    assert (response.status_code, err(response)) == (404, "not_found")


# --- Login ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "login",
    ["lakshmi.devi@gmail.com", "LAKSHMI.DEVI@gmail.com", "lakshmidevi+x@googlemail.com", "9876543210", "+91 98765-43210",
     "09876543210"],  # a leading 0 (BVTGC-153)
)
def test_a_citizen_logs_in_with_the_email_or_the_number(api, citizen, login):
    response = log_in(api, login)
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body) == {"access", "refresh", "account"}
    assert body["account"]["referral_code"] == "CODE0001" and body["account"]["email_hint"] == "l***@gmail.com"
    entry = AuditLog.objects.filter(action="auth.login").latest("created_at")
    assert entry.via == "citizen"


@pytest.mark.parametrize("login, password", [
    ("lakshmi.devi@gmail.com", "wrong-pass"),   # a wrong password
    ("nobody@gmail.com", PASSWORD),             # an unknown email
    ("9123456780", PASSWORD),                   # an unknown number
    ("not a login", PASSWORD),                  # neither
])
def test_wrong_or_unknown_get_the_same_answer(api, citizen, english, login, password):
    response = log_in(api, login, password)
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "invalid_credentials", "message": "Wrong email, mobile number or password."}


def test_a_deleted_account_cannot_log_in(api, citizen):
    CitizenAccount.objects.filter(pk=citizen.pk).update(email_key=None, mobile_hash=None, deleted_at=timezone.now())
    assert err(log_in(api, "lakshmi.devi@gmail.com")) == "invalid_credentials"
    assert err(log_in(api, "9876543210")) == "invalid_credentials"


def test_a_blocked_account_is_told_so_only_with_the_right_password(api, citizen):
    User.objects.filter(pk=citizen.user_id).update(is_active=False)
    response = log_in(api, "9876543210")
    assert (response.status_code, err(response)) == (403, "account_blocked")
    assert err(log_in(api, "9876543210", "wrong-pass")) == "invalid_credentials"


def test_an_officials_username_never_logs_in_here(api, world, wallet_on):
    # "~citizen:" keys never match a real username, so an official named like an email stays out.
    User.objects.create_user(username="ravi@gmail.com", name="Ravi", password=TEST_PASSWORD)
    assert err(log_in(api, "ravi@gmail.com", TEST_PASSWORD)) == "invalid_credentials"


def test_the_email_and_the_number_count_as_one_account_for_the_lockout(api, citizen):
    for login in ["lakshmi.devi@gmail.com", "9876543210"] * 2 + ["lakshmi.devi@gmail.com"]:
        log_in(api, login, "wrong-pass")
    response = log_in(api, "9876543210")  # the right password, but locked
    assert (response.status_code, err(response)) == (429, "locked_out")
    assert response.json()["error"]["retry_after_s"] > 0


def test_a_success_clears_the_failures(api, citizen):
    for _ in range(4):
        log_in(api, "9876543210", "wrong-pass")
    assert log_in(api, "9876543210").status_code == 200
    for _ in range(4):
        log_in(api, "9876543210", "wrong-pass")
    assert log_in(api, "9876543210").status_code == 200


def test_every_audit_via_fits_its_column(api, citizen):
    # AuditLog.via holds 10 characters; SQLite would not complain, MySQL and PostgreSQL would.
    log_in(api, "9876543210")
    log_in(api, "9876543210", "wrong-pass")
    assert all(len(via) <= 10 for via in AuditLog.objects.values_list("via", flat=True))


# --- Forgotten password -------------------------------------------------------------------------------------

def test_forgot_answers_the_same_for_every_login(api, world, wallet_on, mailoutbox):
    make_citizen(world)
    blocked = make_citizen(world, n=2, email="suresh@gmail.com", mobile="9123456780")
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    answers = [
        api().post(FORGOT, {"login": login}, format="json")
        for login in ["9876543210", "nobody@gmail.com", "suresh@gmail.com", "garbage"]
    ]
    assert {response.status_code for response in answers} == {200}
    assert {json.dumps(response.json(), sort_keys=True) for response in answers} == {'{"expires_in": 600, "resend_after": 60}'}
    assert len(mailoutbox) == 1 and mailoutbox[0].to == ["lakshmi.devi@gmail.com"]  # only the open account
    assert "మీ పాస్‌వర్డ్ మార్చడానికి" in mailoutbox[0].body  # its language: Telugu


def reset_code(api, mailoutbox, login="9876543210") -> str:
    assert api().post(FORGOT, {"login": login}, format="json").status_code == 200
    return code_in(mailoutbox[-1])


def test_a_reset_sets_the_new_password_and_ends_every_other_login(api, citizen, mailoutbox):
    old = issue_tokens(citizen.user)
    code = reset_code(api, mailoutbox)
    response = api().post(RESET, {"login": "lakshmi.devi@gmail.com", "code": code, "new_password": "71935284"}, format="json")
    assert response.status_code == 200 and set(response.json()) == {"access", "refresh", "account"}
    assert err(log_in(api, "9876543210")) == "invalid_credentials"  # the old password no longer works
    assert log_in(api, "9876543210", "71935284").status_code == 200
    refreshed = api().post("/api/v1/auth/refresh/", {"refresh": old["refresh"]}, format="json")
    assert (refreshed.status_code, err(refreshed)) == (401, "session_revoked")
    assert mailoutbox[-1].subject == "మీ గ్రామ కనెక్ట్ పాస్‌వర్డ్ మారింది"  # the notice
    assert AuditLog.objects.filter(action="citizen.password_reset", target_id=str(citizen.user_id)).exists()


def test_a_login_counts_the_day_and_a_refused_one_does_not(api, world, citizen):
    """BVTGC-154: a login is a day of using the app (the referral's active rule); a refused one is not."""
    assert err(log_in(api, "9876543210", "wrong-pass")) == "invalid_credentials"
    blocked = make_citizen(world, n=2, email="suresh@gmail.com", mobile="9876500002")
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    assert err(log_in(api, "suresh@gmail.com")) == "account_blocked"
    assert not citizen.active_days.exists() and not blocked.active_days.exists()
    assert log_in(api, "9876543210").status_code == 200
    assert log_in(api, "lakshmi.devi@gmail.com").status_code == 200
    assert list(citizen.active_days.values_list("day", flat=True)) == [timezone.localdate()]


def test_a_reset_counts_the_day(api, citizen, mailoutbox):
    """BVTGC-154: the reset signs the app in, so it is a day of using it."""
    code = reset_code(api, mailoutbox)
    response = api().post(RESET, {"login": "9876543210", "code": code, "new_password": "71935284"}, format="json")
    assert response.status_code == 200
    assert list(citizen.active_days.values_list("day", flat=True)) == [timezone.localdate()]


def test_a_reset_ends_a_lockout(api, citizen, mailoutbox):
    for _ in range(5):
        log_in(api, "9876543210", "wrong-pass")
    assert err(log_in(api, "9876543210")) == "locked_out"
    code = reset_code(api, mailoutbox)
    api().post(RESET, {"login": "9876543210", "code": code, "new_password": "71935284"}, format="json")
    assert log_in(api, "9876543210", "71935284").status_code == 200


def test_a_reset_confirms_an_email_the_super_admin_set(api, citizen, mailoutbox):
    CitizenAccount.objects.filter(pk=citizen.pk).update(email_verified_at=None)
    code = reset_code(api, mailoutbox)
    api().post(RESET, {"login": "9876543210", "code": code, "new_password": "71935284"}, format="json")
    citizen.refresh_from_db()
    assert citizen.email_verified_at is not None


@pytest.mark.parametrize("new_password", ["1234567", "12345678", "ab9876543210"])
def test_the_new_password_follows_the_citizen_rule(api, citizen, mailoutbox, new_password):
    # Too short; common; holds the mobile number, found by its keyed hash (the number is not stored).
    code = reset_code(api, mailoutbox)
    response = api().post(RESET, {"login": "9876543210", "code": code, "new_password": new_password}, format="json")
    assert response.status_code == 400 and err(response) == "validation_error"
    assert "new_password" in response.json()["error"]["fields"]


def test_a_wrong_code_counts_down_and_an_unknown_login_reads_as_expired(api, citizen, mailoutbox):
    code = reset_code(api, mailoutbox)
    wrong = "000000" if code != "000000" else "111111"
    response = api().post(RESET, {"login": "9876543210", "code": wrong, "new_password": "71935284"}, format="json")
    assert (err(response), response.json()["error"]["attempts_left"]) == ("code_wrong", 4)
    response = api().post(RESET, {"login": "nobody@gmail.com", "code": code, "new_password": "71935284"}, format="json")
    assert (response.status_code, err(response)) == (400, "code_expired")


def test_a_reset_code_works_once(api, citizen, mailoutbox):
    code = reset_code(api, mailoutbox)
    body = {"login": "9876543210", "code": code, "new_password": "71935284"}
    assert api().post(RESET, body, format="json").status_code == 200
    response = api().post(RESET, body, format="json")
    assert err(response) == "code_expired"


def test_nothing_personal_in_full_in_the_answers_or_the_audit(api, citizen, mailoutbox):
    log_in(api, "9876543210")
    code = reset_code(api, mailoutbox)
    answer = api().post(RESET, {"login": "9876543210", "code": code, "new_password": "71935284"}, format="json").json()
    stored = json.dumps(answer) + json.dumps(list(AuditLog.objects.values()), default=str, ensure_ascii=False)
    for secret in ("9876543210", "lakshmi.devi@gmail.com", code, "71935284", PASSWORD):
        assert secret not in stored, secret
