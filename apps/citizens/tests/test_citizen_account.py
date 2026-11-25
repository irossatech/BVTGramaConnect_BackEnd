"""The citizen's own wallet account (BVTGC-125; contract section 21; FR-4, FR-15).

GET /citizen/account/ records the day; PATCH moves the village (and the Citizen role) or the language;
the password, the email (with a code to the new address) and the mobile number change after the password.
A wrong password counts with the wrong tries on "Change password".
"""

from __future__ import annotations

import json
import re

import pytest
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenAccount, CitizenActiveDay
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from apps.posts.models import CITIZEN_ROLE
from apps.wallet.models import Wallet

pytestmark = pytest.mark.django_db

ACCOUNT = "/api/v1/citizen/account/"
PASSWORD_URL = "/api/v1/citizen/account/password/"
EMAIL = "/api/v1/citizen/account/email/"
EMAIL_VERIFY = "/api/v1/citizen/account/email/verify/"
MOBILE = "/api/v1/citizen/account/mobile/"
LOGIN = "/api/v1/citizen/auth/login/"
PASSWORD = "48213957"


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    return settings


def make_citizen(world, *, n=1, email="lakshmi@gmail.com", mobile="9876543210", village="kolakaluru") -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.test{n}", name="లక్ష్మి", password=PASSWORD)
    ref = world.ref(village)
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now,
        mobile_hash=hash_mobile(mobile), mobile_last4=mobile[-4:], referral_code=f"CODE{n:04d}",
    )
    Wallet.objects.create(account=account)
    return account


@pytest.fixture
def citizen(world, wallet_on):
    return make_citizen(world)


def err(response) -> str:
    return response.json()["error"]["code"]


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def citizen_role_area(account) -> int:
    return RoleAssignment.objects.get(user=account.user, is_active=True).area_id


# --- Who may come in --------------------------------------------------------------------------------------

def test_hidden_while_the_wallet_is_off_even_before_the_login(api, world):
    response = api().get(ACCOUNT)
    assert (response.status_code, err(response)) == (404, "not_found")


def test_a_login_is_needed(api, world, wallet_on):
    assert api().get(ACCOUNT).status_code == 401


def test_an_official_gets_403(api, world, wallet_on):
    response = api(world.users["va_kolakaluru"]).get(ACCOUNT)
    assert (response.status_code, err(response)) == (403, "permission_denied")


# --- Reading the account ------------------------------------------------------------------------------------

def test_reading_the_account_records_the_day_once(api, citizen):
    first = api(citizen.user).get(ACCOUNT)
    assert first.status_code == 200 and first.json()["referral_code"] == "CODE0001"
    api(citizen.user).get(ACCOUNT)
    assert CitizenActiveDay.objects.filter(account=citizen).count() == 1
    assert CitizenActiveDay.objects.get().day == timezone.localdate()
    citizen.refresh_from_db()
    assert citizen.last_active_at is not None


def test_an_account_without_a_number_says_so(api, citizen):
    CitizenAccount.objects.filter(pk=citizen.pk).update(mobile_hash=None, mobile_last4="")
    assert api(citizen.user).get(ACCOUNT).json()["mobile_missing"] is True


# --- Village and language -------------------------------------------------------------------------------------

def test_a_new_village_moves_the_citizen_role(api, world, citizen):
    other = world.ref("nandivelugu").id
    response = api(citizen.user).patch(ACCOUNT, {"village_id": other}, format="json")
    assert response.status_code == 200 and response.json()["village"]["id"] == other
    citizen.refresh_from_db()
    assert citizen.village_id == other and citizen_role_area(citizen) == other
    assert AuditLog.objects.get(action="citizen.village_changed").data == {"from": world.ref("kolakaluru").id, "to": other}


def test_a_village_outside_the_pilot_still_works_for_an_existing_account(api, world, citizen, wallet_on):
    wallet_on.WALLET_PILOT_MANDALS = [Village.objects.get(id=world.ref("kolakaluru").id).mandal_id]
    other = world.ref("nidamanuru").id  # in another district
    assert api(citizen.user).patch(ACCOUNT, {"village_id": other}, format="json").status_code == 200


def test_an_inactive_village_is_refused(api, world, citizen):
    other = world.ref("nandivelugu").id
    Village.objects.filter(id=other).update(is_active=False)
    response = api(citizen.user).patch(ACCOUNT, {"village_id": other}, format="json")
    assert err(response) == "validation_error" and citizen_role_area(citizen) == world.ref("kolakaluru").id


def test_the_language_changes(api, citizen):
    response = api(citizen.user).patch(ACCOUNT, {"language": "en"}, format="json")
    assert response.json()["language"] == "en"
    assert User.objects.get(pk=citizen.user_id).preferred_language == "en"


# --- Password -----------------------------------------------------------------------------------------------

def test_the_password_changes_and_every_other_login_ends(api, citizen, mailoutbox):
    old = issue_tokens(citizen.user)
    response = api(citizen.user).post(PASSWORD_URL, {"current_password": PASSWORD, "new_password": "71935284"}, format="json")
    assert response.status_code == 200 and set(response.json()) == {"access", "refresh"}
    refreshed = api().post("/api/v1/auth/refresh/", {"refresh": old["refresh"]}, format="json")
    assert err(refreshed) == "session_revoked"
    assert api().post(LOGIN, {"login": "9876543210", "password": "71935284"}, format="json").status_code == 200
    assert mailoutbox[-1].subject == "మీ గ్రామ కనెక్ట్ పాస్‌వర్డ్ మారింది"
    assert AuditLog.objects.filter(action="citizen.password_changed").exists()


@pytest.mark.parametrize("body, code, field", [
    ({"current_password": "wrong-pass", "new_password": "71935284"}, "invalid_credentials", None),
    ({"current_password": PASSWORD, "new_password": PASSWORD}, "validation_error", "new_password"),
    ({"current_password": PASSWORD, "new_password": "1234567"}, "validation_error", "new_password"),
    ({"current_password": PASSWORD, "new_password": "x9876543210"}, "validation_error", "new_password"),
])
def test_the_password_change_is_checked(api, citizen, english, body, code, field):
    response = api(citizen.user).post(PASSWORD_URL, body, format="json")
    assert response.status_code == 400 and err(response) == code
    if field:
        assert field in response.json()["error"]["fields"]
    else:
        assert response.json()["error"]["message"] == "Wrong password."


def test_guessing_the_password_ends_every_session(api, citizen):
    client = api(citizen.user)
    answers = [
        client.post(PASSWORD_URL, {"current_password": f"wrong-{n}", "new_password": "71935284"}, format="json")
        for n in range(5)
    ]
    assert [err(answer) for answer in answers] == ["invalid_credentials"] * 4 + ["session_revoked"]


# --- Email -----------------------------------------------------------------------------------------------------

def test_the_email_changes_with_a_code_to_the_new_address(api, citizen, mailoutbox):
    client = api(citizen.user)
    started = client.post(EMAIL, {"email": "Lakshmi.New@yahoo.com", "password": PASSWORD}, format="json")
    assert started.status_code == 200 and started.json()["email_hint"] == "l***@yahoo.com"
    assert mailoutbox[-1].to == ["lakshmi.new@yahoo.com"]
    done = client.post(EMAIL_VERIFY, {"code": code_in(mailoutbox[-1])}, format="json")
    assert done.status_code == 200 and done.json()["email_hint"] == "l***@yahoo.com"
    citizen.refresh_from_db()
    assert (citizen.email, citizen.email_key) == ("lakshmi.new@yahoo.com", "lakshmi.new@yahoo.com")
    notice = mailoutbox[-1]
    assert notice.to == ["lakshmi@gmail.com"] and "l***@yahoo.com" in notice.body  # the old address told, masked
    assert AuditLog.objects.get(action="citizen.email_changed").data == {"from": "l***@gmail.com", "to": "l***@yahoo.com"}
    assert api().post(LOGIN, {"login": "lakshmi.new@yahoo.com", "password": PASSWORD}, format="json").status_code == 200
    assert err(api().post(LOGIN, {"login": "lakshmi@gmail.com", "password": PASSWORD}, format="json")) == "invalid_credentials"


def test_the_email_stays_until_the_code_is_typed(api, citizen, mailoutbox):
    api(citizen.user).post(EMAIL, {"email": "new@yahoo.com", "password": PASSWORD}, format="json")
    citizen.refresh_from_db()
    assert citizen.email == "lakshmi@gmail.com"


@pytest.mark.parametrize("body, status, code", [
    ({"email": "new@yahoo.com", "password": "wrong-pass"}, 400, "invalid_credentials"),
    ({"email": "l.akshmi@gmail.com", "password": PASSWORD}, 400, "validation_error"),  # already theirs (Gmail variant)
    ({"email": "suresh@gmail.com", "password": PASSWORD}, 409, "email_in_use"),
])
def test_the_email_change_is_checked(api, world, citizen, body, status, code):
    make_citizen(world, n=2, email="suresh@gmail.com", mobile="9123456780")
    response = api(citizen.user).post(EMAIL, body, format="json")
    assert (response.status_code, err(response)) == (status, code)


def test_an_email_taken_meanwhile_is_refused_at_the_code(api, world, citizen, mailoutbox):
    api(citizen.user).post(EMAIL, {"email": "new@yahoo.com", "password": PASSWORD}, format="json")
    code = code_in(mailoutbox[-1])
    make_citizen(world, n=2, email="new@yahoo.com", mobile="9123456780")
    response = api(citizen.user).post(EMAIL_VERIFY, {"code": code}, format="json")
    assert (response.status_code, err(response)) == (409, "email_in_use")


# --- Mobile number -------------------------------------------------------------------------------------------------

def test_the_number_changes_after_the_password(api, citizen):
    response = api(citizen.user).post(MOBILE, {"mobile": "+91 91234 56780", "password": PASSWORD}, format="json")
    assert response.status_code == 200
    assert (response.json()["mobile_last4"], response.json()["mobile_verified"]) == ("6780", False)
    assert AuditLog.objects.get(action="citizen.mobile_changed").data == {"from": "3210", "to": "6780"}
    assert api().post(LOGIN, {"login": "9123456780", "password": PASSWORD}, format="json").status_code == 200


@pytest.mark.parametrize("body, status, code", [
    ({"mobile": "9123456780", "password": "wrong-pass"}, 400, "invalid_credentials"),
    ({"mobile": "98765 43210", "password": PASSWORD}, 400, "validation_error"),  # already theirs
    ({"mobile": "12345", "password": PASSWORD}, 400, "validation_error"),
    ({"mobile": "9000000001", "password": PASSWORD}, 409, "mobile_in_use"),
])
def test_the_number_change_is_checked(api, world, citizen, body, status, code):
    make_citizen(world, n=2, email="suresh@gmail.com", mobile="9000000001")
    response = api(citizen.user).post(MOBILE, body, format="json")
    assert (response.status_code, err(response)) == (status, code)


def test_an_account_whose_number_was_removed_adds_a_new_one(api, citizen):
    CitizenAccount.objects.filter(pk=citizen.pk).update(mobile_hash=None, mobile_last4="")
    response = api(citizen.user).post(MOBILE, {"mobile": "9123456780", "password": PASSWORD}, format="json")
    assert response.status_code == 200 and response.json()["mobile_missing"] is False


def test_the_audit_holds_no_full_email_or_number(api, citizen, mailoutbox):
    client = api(citizen.user)
    client.post(EMAIL, {"email": "new@yahoo.com", "password": PASSWORD}, format="json")
    client.post(EMAIL_VERIFY, {"code": code_in(mailoutbox[-1])}, format="json")
    client.post(MOBILE, {"mobile": "9123456780", "password": PASSWORD}, format="json")
    stored = json.dumps(list(AuditLog.objects.values()), default=str, ensure_ascii=False)
    for secret in ("new@yahoo.com", "lakshmi@gmail.com", "9123456780", "9876543210"):
        assert secret not in stored, secret
