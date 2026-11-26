"""Citizen wallet sign-up with the email code (BVTGC-123; contract section 21; requirements FR-1, FR-3, FR-10,
FR-11, FR-13, D13, D14, D15).

POST /citizen/auth/signup/ checks the form and emails a code; /resend/ sends a new one; /verify/ makes the
account (user, Citizen role, account, wallet) and logs it in. The officials' password rule is untouched.
"""

from __future__ import annotations

import json
import re
import smtplib
from datetime import timedelta

import pytest
from django.core.mail import EmailMultiAlternatives
from django.db.models import F
from django.utils import timezone

from apps.accounts import authz
from apps.accounts.credentials import check_new_password
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenAccount, PendingSignup, VerificationCode
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.models import Village
from apps.wallet.models import Wallet
from grama.exceptions import ApiError

pytestmark = pytest.mark.django_db

SIGNUP = "/api/v1/citizen/auth/signup/"
RESEND = "/api/v1/citizen/auth/signup/resend/"
VERIFY = "/api/v1/citizen/auth/signup/verify/"


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    return settings


def form(world, **changes) -> dict:
    body = {
        "name": "లక్ష్మి దేవి",
        "mobile": "98765 43210",
        "email": "Lakshmi.Devi@gmail.com",
        "password": "48213957",  # numbers only, 8 long: allowed for citizens (D15)
        "village_id": world.ref("kolakaluru").id,
        "age_confirmed": True,
        "consent_version": "2026-11-25",
        "language": "te",
    }
    body.update(changes)
    return body


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def signed_up(api, world, mailoutbox, **changes) -> dict:
    started = api().post(SIGNUP, form(world, **changes), format="json")
    assert started.status_code == 200, started.content
    token = started.json()["signup_token"]
    done = api().post(VERIFY, {"signup_token": token, "code": code_in(mailoutbox[-1])}, format="json")
    assert done.status_code == 201, done.content
    return done.json()


def err(response) -> str:
    return response.json()["error"]["code"]


# --- The switch -----------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [SIGNUP, RESEND, VERIFY])
def test_every_sign_up_endpoint_is_hidden_while_the_wallet_is_off(api, world, url):
    response = api().post(url, form(world), format="json")
    assert (response.status_code, err(response)) == (404, "not_found")


# --- The whole sign-up ---------------------------------------------------------------------------------

def test_a_citizen_signs_up_with_the_email_code(api, world, wallet_on, mailoutbox):
    started = api().post(SIGNUP, form(world), format="json")
    body = started.json()
    assert started.status_code == 200
    assert set(body) == {"signup_token", "expires_in", "resend_after", "email_hint"}
    assert (body["expires_in"], body["resend_after"], body["email_hint"]) == (600, 60, "l***@gmail.com")
    assert not CitizenAccount.objects.exists()  # nothing until the code
    assert mailoutbox[0].to == ["lakshmi.devi@gmail.com"]

    done = api().post(VERIFY, {"signup_token": body["signup_token"], "code": code_in(mailoutbox[0])}, format="json")
    assert done.status_code == 201
    data = done.json()
    assert set(data) == {"access", "refresh", "account"}
    account = data["account"]
    assert account["name"] == "లక్ష్మి దేవి" and account["language"] == "te"
    assert account["village"]["id"] == world.ref("kolakaluru").id
    assert (account["email_hint"], account["email_verified"]) == ("l***@gmail.com", True)
    assert (account["mobile_last4"], account["mobile_missing"], account["mobile_verified"]) == ("3210", False, False)
    assert re.fullmatch(r"[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}", account["referral_code"])
    assert account["wallet"] == {"confirmed_points": 0, "held_points": 0, "next_release_at": None}
    assert account["referral"] is None

    row = CitizenAccount.objects.select_related("user").get()
    assert row.email_key == "lakshmidevi@gmail.com" and row.mobile_hash == hash_mobile("9876543210")
    assert row.email_verified_at is not None and row.consent_version == "2026-11-25"
    assert row.user.check_password("48213957") and row.user.name == "లక్ష్మి దేవి"
    assert authz.is_citizen_account(row.user) and authz.can(row.user, "wallet.use_wallet")
    assert Wallet.objects.filter(account=row).exists()
    assert not PendingSignup.objects.exists() and not VerificationCode.objects.exists()
    entry = AuditLog.objects.get(action="citizen.account_created")
    assert entry.data == {"village_id": world.ref("kolakaluru").id, "referral_code_used": False}


def test_finishing_the_sign_up_counts_today_for_the_active_rule(api, world, wallet_on, mailoutbox):
    """BVTGC-154: the sign-up day is a day of using the app (the referral's active rule)."""
    signed_up(api, world, mailoutbox)
    account = CitizenAccount.objects.get()
    assert list(account.active_days.values_list("day", flat=True)) == [timezone.localdate()]


def test_the_new_account_is_logged_in_with_a_citizen_token(api, world, wallet_on, mailoutbox):
    data = signed_up(api, world, mailoutbox)
    client = api()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    assert client.get("/api/v1/me/").status_code == 403  # a citizen account is never an official (BVTGC-121)
    assert client.post("/api/v1/auth/logout/", {"refresh": data["refresh"]}, format="json").status_code == 204


def test_nothing_personal_in_full_in_the_answer_the_audit_or_the_database_number(api, world, wallet_on, mailoutbox):
    data = signed_up(api, world, mailoutbox)
    stored = json.dumps(data, ensure_ascii=False) + json.dumps(list(AuditLog.objects.values()), default=str, ensure_ascii=False)
    stored += json.dumps(list(CitizenAccount.objects.values()), default=str)
    assert "9876543210" not in stored and "98765 43210" not in stored
    assert "lakshmi.devi@gmail.com" not in json.dumps(data) and "lakshmi.devi@gmail.com" not in json.dumps(
        list(AuditLog.objects.values()), default=str
    )


# --- Checks of the form ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "changes, code, field",
    [
        ({"age_confirmed": False}, "age_required", None),
        ({"consent_version": "2025-01-01"}, "validation_error", "consent_version"),
        ({"mobile": "12345"}, "validation_error", "mobile"),
        ({"mobile": "5876543210"}, "validation_error", "mobile"),
        ({"email": "not-an-email"}, "validation_error", "email"),
        ({"password": "4821395"}, "validation_error", "password"),  # 7 characters
        ({"password": "12345678"}, "validation_error", "password"),  # a common password
        ({"password": "pw9876543210"}, "validation_error", "password"),  # the mobile number inside
        ({"name": ""}, "validation_error", "name"),
    ],
)
def test_the_form_is_checked(api, world, wallet_on, mailoutbox, changes, code, field):
    response = api().post(SIGNUP, form(world, **changes), format="json")
    assert response.status_code == 400 and err(response) == code
    if field:
        assert field in response.json()["error"]["fields"]
    assert not PendingSignup.objects.exists() and not mailoutbox


def test_an_inactive_village_is_refused(api, world, wallet_on):
    Village.objects.filter(id=world.ref("kolakaluru").id).update(is_active=False)
    response = api().post(SIGNUP, form(world), format="json")
    assert err(response) == "validation_error" and "village_id" in response.json()["error"]["fields"]


def test_mobile_with_plus_91_and_spaces_is_accepted(api, world, wallet_on, mailoutbox):
    signed_up(api, world, mailoutbox, mobile="+91 98765-43210")
    assert CitizenAccount.objects.get().mobile_last4 == "3210"


def test_mobile_with_a_leading_0_is_the_same_number(api, world, wallet_on, mailoutbox):
    """BVTGC-153: the app and the website accept "0" + 10 digits; the server stores the 10 digits."""
    signed_up(api, world, mailoutbox, mobile="098765 43210")
    account = CitizenAccount.objects.get()
    assert account.mobile_last4 == "3210" and account.mobile_hash == hash_mobile("9876543210")


@pytest.mark.parametrize("mobile", ["05876543210", "0987654321", "009876543210", "+910987654321"])
def test_a_leading_0_does_not_make_a_wrong_number_right(api, world, wallet_on, mobile):
    response = api().post(SIGNUP, form(world, mobile=mobile), format="json")
    assert err(response) == "validation_error" and "mobile" in response.json()["error"]["fields"]


def test_the_officials_password_rule_is_unchanged():
    with pytest.raises(ApiError):
        check_new_password("48213957")  # fine for a citizen, not for an official


# --- One account per number and per email ------------------------------------------------------------------

@pytest.mark.parametrize(
    "second, code",
    [
        ({"email": "other@gmail.com"}, "mobile_in_use"),
        ({"mobile": "9123456780"}, "email_in_use"),
        ({"mobile": "9123456780", "email": "lakshmidevi+wallet@googlemail.com"}, "email_in_use"),  # a Gmail variant
    ],
)
def test_a_number_or_email_already_registered_is_refused(api, world, wallet_on, mailoutbox, second, code):
    signed_up(api, world, mailoutbox)
    response = api().post(SIGNUP, form(world, **second), format="json")
    assert (response.status_code, err(response)) == (409, code)


def test_two_sign_ups_racing_for_one_email_one_wins(api, world, wallet_on, mailoutbox):
    first = api().post(SIGNUP, form(world), format="json").json()
    first_code = code_in(mailoutbox[0])
    VerificationCode.objects.update(created_at=F("created_at") - timedelta(seconds=61))
    second = api().post(SIGNUP, form(world, mobile="9123456780"), format="json").json()
    second_code = code_in(mailoutbox[1])
    assert api().post(VERIFY, {"signup_token": first["signup_token"], "code": first_code}, format="json").status_code == 201
    response = api().post(VERIFY, {"signup_token": second["signup_token"], "code": second_code}, format="json")
    assert (response.status_code, err(response)) == (409, "email_in_use")
    assert CitizenAccount.objects.count() == 1


def test_canonical_email_counts_gmail_variants_as_one():
    assert canonical_email(" L.a.k.s.h.m.i+x@GoogleMail.com ") == "lakshmi@gmail.com"
    assert canonical_email("first.last+tag@yahoo.com") == "first.last+tag@yahoo.com"


# --- The pilot ----------------------------------------------------------------------------------------------

def test_the_pilot_opens_sign_up_only_in_its_mandals(api, world, wallet_on, mailoutbox):
    mandal = Village.objects.get(id=world.ref("kolakaluru").id).mandal_id
    wallet_on.WALLET_PILOT_MANDALS = [mandal + 1000]
    response = api().post(SIGNUP, form(world), format="json")
    assert (response.status_code, err(response)) == (403, "wallet_not_in_area")
    wallet_on.WALLET_PILOT_MANDALS = [mandal]
    assert api().post(SIGNUP, form(world), format="json").status_code == 200


# --- Codes and tokens -------------------------------------------------------------------------------------------

def test_a_wrong_code_counts_down(api, world, wallet_on, mailoutbox):
    token = api().post(SIGNUP, form(world), format="json").json()["signup_token"]
    wrong = "000000" if code_in(mailoutbox[0]) != "000000" else "111111"
    response = api().post(VERIFY, {"signup_token": token, "code": wrong}, format="json")
    assert (response.status_code, err(response), response.json()["error"]["attempts_left"]) == (400, "code_wrong", 4)
    assert not CitizenAccount.objects.exists()


def test_resend_waits_a_minute_then_sends_a_new_code(api, world, wallet_on, mailoutbox):
    token = api().post(SIGNUP, form(world), format="json").json()["signup_token"]
    response = api().post(RESEND, {"signup_token": token}, format="json")
    assert (response.status_code, err(response)) == (429, "code_cooldown")
    VerificationCode.objects.update(created_at=F("created_at") - timedelta(seconds=61))
    response = api().post(RESEND, {"signup_token": token}, format="json")
    assert response.status_code == 200 and response.json() == {"expires_in": 600, "resend_after": 60}
    assert len(mailoutbox) == 2
    done = api().post(VERIFY, {"signup_token": token, "code": code_in(mailoutbox[1])}, format="json")
    assert done.status_code == 201


@pytest.mark.parametrize("token", ["", "garbage", "eyJwIjoiMSJ9:1x:bad"])
def test_a_bad_token_is_refused(api, world, wallet_on, token):
    response = api().post(VERIFY, {"signup_token": token or "x", "code": "123456"}, format="json")
    assert (response.status_code, err(response)) == (401, "signup_token_invalid")


def test_a_sign_up_older_than_24_hours_is_forgotten(api, world, wallet_on, mailoutbox):
    token = api().post(SIGNUP, form(world), format="json").json()["signup_token"]
    PendingSignup.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = api().post(VERIFY, {"signup_token": token, "code": code_in(mailoutbox[0])}, format="json")
    assert (response.status_code, err(response)) == (401, "signup_token_invalid")


def test_a_token_works_once(api, world, wallet_on, mailoutbox):
    token = api().post(SIGNUP, form(world), format="json").json()["signup_token"]
    body = {"signup_token": token, "code": code_in(mailoutbox[0])}
    assert api().post(VERIFY, body, format="json").status_code == 201
    response = api().post(VERIFY, body, format="json")
    assert (response.status_code, err(response)) == (401, "signup_token_invalid")


def test_the_email_service_down_keeps_nothing(api, world, wallet_on, monkeypatch):
    def down(self, fail_silently=False):
        raise smtplib.SMTPServerDisconnected("down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", down)
    response = api().post(SIGNUP, form(world), format="json")
    assert (response.status_code, err(response)) == (503, "email_unavailable")
    assert not PendingSignup.objects.exists() and not VerificationCode.objects.exists()


# --- Referral codes typed at sign-up (the referral itself: BVTGC-126) ------------------------------------------

def test_referral_codes_are_checked_at_sign_up(api, world, wallet_on, mailoutbox):
    referrer = signed_up(api, world, mailoutbox)["account"]["referral_code"]
    other = {"mobile": "9123456780", "email": "suresh@gmail.com"}
    VerificationCode.objects.all().delete()

    response = api().post(SIGNUP, form(world, referral_code="ZZZZZZZZ", **other), format="json")
    assert (response.status_code, err(response)) == (400, "referral_code_invalid")

    response = api().post(SIGNUP, form(world, referral_code=referrer, mobile="9123456780", email="lakshmidevi@gmail.com"), format="json")
    assert err(response) == "email_in_use"  # the same person: refused as a registered email first

    User.objects.filter(citizen_account__referral_code=referrer).update(is_active=False)
    response = api().post(SIGNUP, form(world, referral_code=referrer.lower(), **other), format="json")
    assert (response.status_code, err(response)) == (400, "referral_not_open")

    User.objects.filter(citizen_account__referral_code=referrer).update(is_active=True)
    response = api().post(SIGNUP, form(world, referral_code=referrer, **other), format="json")
    assert response.status_code == 200
    assert PendingSignup.objects.get().referral_code == referrer


@pytest.mark.parametrize("same", ["mobile", "email"])
def test_a_referrer_with_the_same_number_or_email_is_the_citizen_themselves(api, world, wallet_on, mailoutbox, same):
    # Through the API the "already registered" answer comes first; the service still refuses it on its own.
    from apps.citizens.services_account import check_referral

    referrer = CitizenAccount.objects.get(referral_code=signed_up(api, world, mailoutbox)["account"]["referral_code"])
    keys = {"mobile_hash": "other", "email_key": "other@gmail.com"}
    keys[f"{same}_hash" if same == "mobile" else "email_key"] = referrer.mobile_hash if same == "mobile" else referrer.email_key
    with pytest.raises(ApiError) as excinfo:
        check_referral(referrer.referral_code, **keys)
    assert excinfo.value.error_code == "referral_code_own"
