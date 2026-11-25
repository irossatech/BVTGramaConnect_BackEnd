"""The Super Admin's citizen wallet accounts (BVTGC-128; requirements D11, D16, FR-6, FR-9, FR-16, FR-35;
contract section 21, "Super Admin").

Only the Super Admin: every other official, a citizen and an anonymous caller are refused, whatever area
the account is in (D11: local officials see no wallets). Each action needs a reason and is audited.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens import services_account as accounts
from apps.citizens.models import CitizenAccount, VerificationCode
from apps.citizens.services import hash_android_id
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel
from apps.notifications.models import Device
from apps.posts.models import CITIZEN_ROLE
from apps.wallet.models import Wallet

pytestmark = pytest.mark.django_db

BASE = "/api/v1/admin/citizen-accounts/"
FIND = BASE + "find/"
PASSWORD = "48213957"
OFFICIALS = [
    "da_guntur", "da_krishna", "ma_tenali", "ma_mangalagiri", "ma_gannavaram", "va_kolakaluru", "va_nandivelugu",
    "va_kaza", "va_nidamanuru", "pm_kolakaluru", "pm_nandivelugu", "pm_kaza", "pm_nidamanuru",
]


def make_citizen(world, n=1, *, name="లక్ష్మి", email=None, mobile=None, village="kolakaluru") -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.adm{n}", name=name, password=PASSWORD)
    ref = world.ref(village)
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    email = email or f"citizen{n}@gmail.com"
    mobile = mobile or f"98765000{n:02d}"
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now,
        mobile_hash=hash_mobile(mobile), mobile_last4=mobile[-4:], referral_code=f"ADM{n:05d}",
        signup_android_id_hash=hash_android_id(f"phone-{n}"),
    )
    Wallet.objects.create(account=account)
    return account


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    return settings


@pytest.fixture
def citizen(world, wallet_on):
    return make_citizen(world, email="lakshmi.devi@gmail.com", mobile="9876543210")


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def err(response) -> str:
    return response.json()["error"]["code"]


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def routes(account_id: int) -> list[tuple[str, str, dict | None]]:
    one = f"{BASE}{account_id}/"
    return [
        ("get", BASE, None),
        ("post", FIND, {"mobile": "9876543210"}),
        ("post", one + "email/", {"email": "new.address@gmail.com", "reason": "Lost the email", "called_back": True}),
        ("post", one + "release-mobile/", {"reason": "Number taken", "called_back": True}),
        ("post", one + "block/", {"reason": "Fraud"}),
        ("post", one + "unblock/", {"reason": "Cleared"}),
        ("post", one + "delete/", {"reason": "Legal request"}),
    ]


def call(client, method, url, body):
    return client.get(url) if method == "get" else client.post(url, body, format="json")


# --- Who may call -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("index", range(7))
@pytest.mark.parametrize("name", OFFICIALS)
def test_no_other_official_may_call(api, world, citizen, name, index):
    """Even the Village Admin and Panchayat member of the citizen's own village: D11."""
    method, url, body = routes(citizen.pk)[index]
    response = call(api(world.users[name]), method, url, body)
    assert (response.status_code, err(response)) == (403, "permission_denied")
    citizen.refresh_from_db()
    assert citizen.deleted_at is None and citizen.email == "lakshmi.devi@gmail.com"


@pytest.mark.parametrize("index", range(7))
def test_a_citizen_and_an_anonymous_caller_are_refused(api, world, citizen, index):
    other = make_citizen(world, 2)
    method, url, body = routes(citizen.pk)[index]
    assert call(api(other.user), method, url, body).status_code == 403
    assert call(api(), method, url, body).status_code == 401
    assert not AuditLog.objects.filter(action__startswith="citizen.account_").exists()


@pytest.mark.parametrize("index", range(7))
def test_the_super_admin_may_call_each_one(admin, citizen, index):
    if index == 5:  # unblock needs a blocked account to change anything; it answers 200 either way
        User.objects.filter(pk=citizen.user_id).update(is_active=False)
    method, url, body = routes(citizen.pk)[index]
    assert call(admin, method, url, body).status_code == 200


def test_it_works_while_the_wallet_is_off(admin, world, settings):
    settings.WALLET_ENABLED = False
    make_citizen(world)
    assert admin.get(BASE).json()["count"] == 1


def test_an_unknown_account_is_404(admin, citizen):
    response = admin.post(f"{BASE}{uuid.uuid4()}/block/", {"reason": "x"}, format="json")
    assert (response.status_code, err(response)) == (404, "not_found")


# --- The list -----------------------------------------------------------------------------------------------------

def test_rows_show_no_full_email_or_number(admin, citizen):
    citizen.wallet.confirmed_points = 70
    citizen.wallet.save()
    response = admin.get(BASE)
    assert response.status_code == 200 and response.json()["count"] == 1
    row = response.json()["results"][0]
    assert row["id"] == str(citizen.pk) and row["name"] == "లక్ష్మి"
    assert row["village"]["id"] == citizen.village_id and row["village"]["name"]
    assert (row["mobile_last4"], row["email_hint"], row["referral_code"]) == ("3210", "l***@gmail.com", "ADM00001")
    assert (row["status"], row["confirmed_points"], row["held_points"], row["mobile_missing"]) == ("active", 70, 0, False)
    assert row["allowed_actions"] == ["set_email", "release_mobile", "block", "delete", "adjust"]
    assert "lakshmi.devi" not in response.content.decode() and "9876543210" not in response.content.decode()


def test_search_by_name_last4_or_code_and_filters(admin, world, citizen):
    ravi = make_citizen(world, 2, name="Ravi Kumar", village="nandivelugu")
    blocked = make_citizen(world, 3, name="Sita")
    User.objects.filter(pk=blocked.user_id).update(is_active=False)

    def ids(query: str) -> set[str]:
        return {row["id"] for row in admin.get(BASE + query).json()["results"]}

    def as_ids(*accounts_) -> set[str]:
        return {str(a.pk) for a in accounts_}

    assert ids("?q=ravi") == as_ids(ravi)
    assert ids("?q=లక్ష్మి") == as_ids(citizen)
    assert ids("?q=3210") == as_ids(citizen)
    assert ids("?q=adm00003") == as_ids(blocked)
    assert ids(f"?village_id={world.ref('nandivelugu').id}") == as_ids(ravi)
    assert ids("?status=blocked") == as_ids(blocked)
    assert ids("?status=active") == as_ids(citizen, ravi)
    for bad in ("?status=gone", "?village_id=abc"):
        assert err(admin.get(BASE + bad)) == "validation_error"


def test_allowed_actions_follow_the_state(admin, world, citizen):
    blocked, gone = make_citizen(world, 2), make_citizen(world, 3)
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    accounts.delete_account(gone, via="app")
    rows = {row["id"]: row for row in admin.get(BASE).json()["results"]}
    assert rows[str(blocked.pk)]["allowed_actions"] == ["set_email", "release_mobile", "unblock", "delete", "adjust"]
    gone_row = rows[str(gone.pk)]
    assert gone_row["allowed_actions"] == [] and gone_row["status"] == "deleted"
    assert gone_row["name"] == accounts.DELETED_NAME and gone_row["mobile_missing"] is False


# --- Finding the account a caller names ------------------------------------------------------------------------------

def test_find_by_the_full_number_is_audited_without_it(admin, world, citizen):
    response = admin.post(FIND, {"mobile": "+91 98765 43210"}, format="json")
    assert [row["id"] for row in response.json()["results"]] == [str(citizen.pk)]
    entry = AuditLog.objects.get(action="citizen.account_found")
    assert entry.data == {"by": "mobile", "mobile": "3210", "found": 1}
    assert entry.actor_id == world.users["super"].pk and entry.target_id == str(citizen.user_id)


def test_find_by_email_matches_gmail_variants(admin, citizen):
    response = admin.post(FIND, {"email": "Lakshmi.Devi+wallet@gmail.com"}, format="json")
    assert [row["id"] for row in response.json()["results"]] == [str(citizen.pk)]
    data = AuditLog.objects.get(action="citizen.account_found").data
    assert data["by"] == "email" and "lakshmi.devi" not in str(data).lower()


def test_find_nothing_is_still_audited(admin, citizen):
    assert admin.post(FIND, {"mobile": "9000000000"}, format="json").json() == {"results": []}
    assert AuditLog.objects.get(action="citizen.account_found").data["found"] == 0


@pytest.mark.parametrize("body", [{}, {"mobile": "9876543210", "email": "a@gmail.com"}, {"mobile": "12345"}])
def test_find_needs_one_good_value(admin, citizen, body):
    assert err(admin.post(FIND, body, format="json")) == "validation_error"


# --- A new email after a call-back (D16) --------------------------------------------------------------------------

def set_email(admin, account, **body):
    return admin.post(f"{BASE}{account.pk}/email/", {"email": "new.address@gmail.com", "reason": "Lost the email",
                                                     "called_back": True, **body}, format="json")


@pytest.mark.parametrize("change", [{"called_back": False}, {"called_back": None}, {"reason": ""}])
def test_a_new_email_needs_the_call_back_and_a_reason(admin, citizen, change):
    response = set_email(admin, citizen, **change)
    assert err(response) == "validation_error"
    citizen.refresh_from_db()
    assert citizen.email == "lakshmi.devi@gmail.com"


def test_the_new_email_is_confirmed_through_forgot_password(admin, api, citizen, mailoutbox, django_capture_on_commit_callbacks):
    api().post("/api/v1/citizen/auth/password/forgot/", {"login": "9876543210"}, format="json")
    old_code = code_in(mailoutbox[-1])
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = set_email(admin, citizen)
    row = response.json()
    assert response.status_code == 200 and (row["email_hint"], row["email_verified"]) == ("n***@gmail.com", False)
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]  # the "email changed" notice to the old one
    entry = AuditLog.objects.get(action="citizen.email_set_by_admin")
    assert entry.data == {"from": "l***@gmail.com", "to": "n***@gmail.com", "reason": "Lost the email"}

    reset = {"login": "9876543210", "code": old_code, "new_password": "71935284"}
    stale = api().post("/api/v1/citizen/auth/password/reset/", reset, format="json")
    assert err(stale) == "code_expired"  # codes sent before stop working

    # Past the 60-second wait between codes of one account.
    VerificationCode.objects.filter(account=citizen).update(created_at=timezone.now() - timedelta(minutes=2))
    api().post("/api/v1/citizen/auth/password/forgot/", {"login": "new.address@gmail.com"}, format="json")
    assert mailoutbox[-1].to == ["new.address@gmail.com"]
    done = api().post("/api/v1/citizen/auth/password/reset/", {**reset, "code": code_in(mailoutbox[-1])}, format="json")
    assert done.status_code == 200
    citizen.refresh_from_db()
    assert citizen.email_verified_at is not None


def test_a_new_email_of_another_account_is_409(admin, world, citizen):
    make_citizen(world, 2, email="new.address@gmail.com")
    assert err(set_email(admin, citizen, email="NewAddress@gmail.com")) == "email_in_use"


def test_the_same_email_is_refused(admin, citizen):
    assert err(set_email(admin, citizen, email="lakshmi.devi@gmail.com")) == "validation_error"


# --- Removing a number after a call-back (D16) ----------------------------------------------------------------------

def test_releasing_the_number_frees_it(admin, api, world, citizen):
    response = admin.post(f"{BASE}{citizen.pk}/release-mobile/", {"reason": "Recycled SIM", "called_back": True}, format="json")
    row = response.json()
    assert response.status_code == 200 and (row["mobile_last4"], row["mobile_missing"]) == ("", True)
    assert "release_mobile" not in row["allowed_actions"]
    assert AuditLog.objects.get(action="citizen.mobile_released").data == {"mobile": "3210", "reason": "Recycled SIM"}
    assert api(citizen.user).get("/api/v1/citizen/account/").json()["mobile_missing"] is True

    other = make_citizen(world, 2)
    moved = api(other.user).post(
        "/api/v1/citizen/account/mobile/", {"mobile": "9876543210", "password": PASSWORD}, format="json"
    )
    assert moved.status_code == 200

    again = admin.post(f"{BASE}{citizen.pk}/release-mobile/", {"reason": "x", "called_back": True}, format="json")
    assert (again.status_code, err(again)) == (409, "invalid_transition")


def test_releasing_needs_the_call_back(admin, citizen):
    response = admin.post(f"{BASE}{citizen.pk}/release-mobile/", {"reason": "x"}, format="json")
    assert err(response) == "validation_error"
    citizen.refresh_from_db()
    assert citizen.mobile_hash


# --- Block, unblock, delete ------------------------------------------------------------------------------------------

def test_a_block_ends_every_session_and_the_login(admin, api, citizen):
    client = api(citizen.user)
    Device.objects.create(user=citizen.user, platform="android", fcm_token="push-1")
    response = admin.post(f"{BASE}{citizen.pk}/block/", {"reason": "Fake referrals"}, format="json")
    assert response.json()["status"] == "blocked"
    assert client.get("/api/v1/citizen/account/").status_code == 401
    assert not Device.objects.filter(user=citizen.user).exists()
    login = api().post("/api/v1/citizen/auth/login/", {"login": "9876543210", "password": PASSWORD}, format="json")
    assert (login.status_code, err(login)) == (403, "account_blocked")
    entry = AuditLog.objects.get(action="citizen.account_blocked")
    assert (entry.data, entry.target_label) == ({"reason": "Fake referrals"}, "లక్ష్మి")

    # Blocking again changes nothing and writes nothing.
    admin.post(f"{BASE}{citizen.pk}/block/", {"reason": "again"}, format="json")
    assert AuditLog.objects.filter(action="citizen.account_blocked").count() == 1

    unblocked = admin.post(f"{BASE}{citizen.pk}/unblock/", {"reason": "Checked"}, format="json")
    assert unblocked.json()["status"] == "active"
    assert AuditLog.objects.get(action="citizen.account_unblocked").data == {"reason": "Checked"}
    login = api().post("/api/v1/citizen/auth/login/", {"login": "9876543210", "password": PASSWORD}, format="json")
    assert login.status_code == 200


@pytest.mark.parametrize("action", ["block", "unblock", "delete"])
def test_a_reason_is_required(admin, citizen, action):
    assert err(admin.post(f"{BASE}{citizen.pk}/{action}/", {}, format="json")) == "validation_error"


def test_the_super_admin_deletes_with_a_reason(admin, world, citizen, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = admin.post(f"{BASE}{citizen.pk}/delete/", {"reason": "Legal request"}, format="json")
    row = response.json()
    assert (row["status"], row["name"], row["email_hint"], row["allowed_actions"]) == (
        "deleted", accounts.DELETED_NAME, "", []
    )
    entry = AuditLog.objects.get(action="citizen.account_deleted")
    assert (entry.via, entry.actor_id, entry.data) == ("admin", world.users["super"].pk, {"via": "admin", "reason": "Legal request"})
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]
    assert not VerificationCode.objects.filter(account=citizen).exists()


@pytest.mark.parametrize("index", range(2, 7))
def test_nothing_changes_a_deleted_account(admin, citizen, index):
    accounts.delete_account(citizen, via="app")
    method, url, body = routes(citizen.pk)[index]
    response = call(admin, method, url, body)
    assert (response.status_code, err(response)) == (409, "invalid_transition")
    user = User.objects.get(pk=citizen.user_id)
    assert user.is_active is False and user.name == accounts.DELETED_NAME
