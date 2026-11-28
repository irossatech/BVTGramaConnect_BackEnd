"""Deleting a citizen wallet account, and the wallet accounts' housekeeping (BVTGC-127; requirements FR-9,
D12, Q8, Q9; contract section 21).

In the app with the password, or on the website with a DELETE code sent to the account's email. The
account closes at once and its personal details go; confirmed points and decided referrals stay without
them. The fingerprints of the number and the email stop a second welcome bonus for a year.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens import services_account as accounts
from apps.citizens.models import CitizenAccount, CitizenActiveDay, PendingSignup, VerificationCode
from apps.citizens.services import hash_android_id
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel
from apps.notifications.models import Device
from apps.posts.models import CITIZEN_ROLE
from apps.wallet import services as wallet
from apps.wallet.models import Referral, Wallet, WalletEntry
from grama.housekeeping import run_housekeeping

pytestmark = pytest.mark.django_db

DELETE = "/api/v1/citizen/account/delete/"
DELETE_CODE = "/api/v1/citizen/auth/delete/code/"
DELETE_WITH_CODE = "/api/v1/citizen/auth/delete/"
FORGOT = "/api/v1/citizen/auth/password/forgot/"
ACCOUNT = "/api/v1/citizen/account/"
LOGIN = "/api/v1/citizen/auth/login/"
PASSWORD = "48213957"
K, S = WalletEntry.Kind, WalletEntry.Status


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    return settings


def make_citizen(world, n=1, *, email=None, mobile=None, phone=None) -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.del{n}", name="లక్ష్మి", password=PASSWORD)
    ref = world.ref("kolakaluru")
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    email = email or f"lakshmi{n}@gmail.com"
    mobile = mobile or f"98765432{n:02d}"
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now,
        mobile_hash=hash_mobile(mobile), mobile_last4=mobile[-4:], referral_code=f"DEL{n:05d}",
        signup_android_id_hash=hash_android_id(phone or f"phone-{n}"),
    )
    Wallet.objects.create(account=account)
    return account


@pytest.fixture
def citizen(world, wallet_on):
    return make_citizen(world, email="lakshmi.devi@gmail.com", mobile="9876543210")


def err(response) -> str:
    return response.json()["error"]["code"]


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def cache(account) -> tuple[int, int]:
    row = Wallet.objects.get(account=account)
    return row.held_points, row.confirmed_points


def entries(account) -> list[tuple]:
    return list(WalletEntry.objects.filter(wallet__account=account).values_list("kind", "points", "status", "reason_key"))


def assert_deleted(account) -> None:
    """Every personal detail is gone; the fingerprints are kept (Q9)."""
    account.refresh_from_db()
    user = User.objects.get(pk=account.user_id)
    assert account.deleted_at is not None
    assert (account.email, account.email_key, account.mobile_hash, account.mobile_last4) == ("", None, None, "")
    assert account.deleted_email_key and account.deleted_mobile_hash
    assert (user.name, user.name_te, user.email, user.is_active) == (accounts.DELETED_NAME, "", None, False)
    assert user.username.startswith("deleted.") and not user.has_usable_password()
    assert not RoleAssignment.objects.filter(user=user, is_active=True).exists()
    assert not user.groups.exists()
    assert not VerificationCode.objects.filter(account=account).exists()


# --- Who may come in ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [DELETE, DELETE_CODE, DELETE_WITH_CODE])
def test_hidden_while_the_wallet_is_off(api, world, url):
    response = api().post(url, {}, format="json")
    assert (response.status_code, err(response)) == (404, "not_found")


def test_the_app_deletion_needs_a_citizen_login(api, world, wallet_on):
    assert api().post(DELETE, {"password": PASSWORD}, format="json").status_code == 401
    response = api(world.users["va_kolakaluru"]).post(DELETE, {"password": PASSWORD}, format="json")
    assert (response.status_code, err(response)) == (403, "permission_denied")
    assert User.objects.get(pk=world.users["va_kolakaluru"].pk).is_active


# --- In the app, with the password ----------------------------------------------------------------------------

def test_a_wrong_password_deletes_nothing(api, citizen):
    response = api(citizen.user).post(DELETE, {"password": "wrong-one"}, format="json")
    assert (response.status_code, err(response)) == (400, "invalid_credentials")
    citizen.refresh_from_db()
    assert citizen.deleted_at is None and citizen.email == "lakshmi.devi@gmail.com"
    assert not AuditLog.objects.filter(action="citizen.account_deleted").exists()


def test_the_password_is_required(api, citizen):
    response = api(citizen.user).post(DELETE, {}, format="json")
    assert (response.status_code, err(response)) == (400, "validation_error")
    citizen.refresh_from_db()
    assert citizen.deleted_at is None


def test_deleting_in_the_app_removes_the_personal_details(api, citizen, mailoutbox, django_capture_on_commit_callbacks):
    Device.objects.create(user=citizen.user, platform="android", fcm_token="push-token-1")
    client = api(citizen.user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(DELETE, {"password": PASSWORD}, format="json")
    assert response.status_code == 204
    assert_deleted(citizen)
    assert citizen.deleted_email_key == canonical_email("lakshmi.devi@gmail.com")
    assert citizen.deleted_mobile_hash == hash_mobile("9876543210")
    assert not Device.objects.filter(user=citizen.user).exists()

    # The notice went to the old address, once, after the deletion.
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]

    # The audit entry names nobody.
    entry = AuditLog.objects.get(action="citizen.account_deleted")
    assert (entry.via, entry.actor_id, entry.actor_name, entry.target_label) == ("app", None, "", accounts.DELETED_NAME)
    assert entry.data == {"via": "app"}
    assert "lakshmi" not in str(entry.data) and "9876543210" not in str(entry.data)

    # The old token ends at once; neither login works any more.
    assert client.get(ACCOUNT).status_code == 401
    for login in ("lakshmi.devi@gmail.com", "9876543210"):
        answer = api().post(LOGIN, {"login": login, "password": PASSWORD}, format="json")
        assert (answer.status_code, err(answer)) == (400, "invalid_credentials")


def test_the_number_and_the_email_are_free_again_without_a_second_welcome_bonus(world, citizen):
    referrer = make_citizen(world, 2)
    wallet.start_referral(citizen, referrer)
    accounts.delete_account(citizen, via="app")
    again = make_citizen(world, 3, email="lakshmi.devi@gmail.com", mobile="9876543210", phone="new-phone")
    wallet.start_referral(again, make_citizen(world, 4))
    assert entries(again) == [(K.WELCOME_BONUS, 20, S.CANCELLED, "welcome_limit")]


# --- What stays in the wallet ----------------------------------------------------------------------------------

def test_confirmed_points_stay_and_waiting_referrals_end_on_both_sides(world, citizen):
    """The referrer deletes: the confirmed referral stays; the waiting one is not confirmed ("deleted"),
    which takes back the friend's welcome bonus on hold too (FR-24)."""
    done_friend, waiting_friend = make_citizen(world, 2), make_citizen(world, 3)
    done = wallet.start_referral(done_friend, citizen)
    wallet.decide(done, confirm=True)
    waiting = wallet.start_referral(waiting_friend, citizen)
    assert cache(citizen) == (50, 50)

    accounts.delete_account(citizen, via="app")

    done.refresh_from_db()
    waiting.refresh_from_db()
    assert done.status == Referral.Status.CONFIRMED
    assert (waiting.status, waiting.reason_key) == (Referral.Status.REJECTED, "deleted")
    assert sorted(entries(citizen)) == sorted([
        (K.REFERRAL_REWARD, 50, S.CONFIRMED, ""),
        (K.REFERRAL_REWARD, 50, S.CANCELLED, "deleted"),
    ])
    assert cache(citizen) == (0, 50)
    assert entries(waiting_friend) == [(K.WELCOME_BONUS, 20, S.CANCELLED, "deleted")]
    assert cache(waiting_friend) == (0, 0) and cache(done_friend) == (0, 20)
    assert wallet.check_wallets() == 0


def test_the_new_citizen_deleting_ends_the_referrers_hold(world, citizen):
    referrer = make_citizen(world, 2)
    referral = wallet.start_referral(citizen, referrer)
    accounts.delete_account(citizen, via="app")
    referral.refresh_from_db()
    assert (referral.status, referral.reason_key) == (Referral.Status.REJECTED, "deleted")
    assert entries(referrer) == [(K.REFERRAL_REWARD, 50, S.CANCELLED, "deleted")]
    assert cache(referrer) == (0, 0) and cache(citizen) == (0, 0)
    assert wallet.check_wallets() == 0


def test_a_hold_without_a_referral_is_cancelled_too(citizen):
    wallet._add_entry(citizen.wallet, points=30, kind=K.WELCOME_BONUS, status=S.HELD, key="manual:1")
    accounts.delete_account(citizen, via="app")
    assert entries(citizen) == [(K.WELCOME_BONUS, 30, S.CANCELLED, "deleted")]
    assert cache(citizen) == (0, 0)


def test_the_hourly_job_says_deleted_not_blocked(world, citizen):
    """A deleted account is inactive too; its reason is still "deleted"."""
    referrer = make_citizen(world, 2)
    referral = wallet.start_referral(citizen, referrer)
    now = timezone.now()
    Referral.objects.filter(pk=referral.pk).update(confirm_after=now - timedelta(hours=1))
    CitizenAccount.objects.filter(pk=citizen.pk).update(deleted_at=now)
    User.objects.filter(pk=citizen.user_id).update(is_active=False)
    assert wallet.decide_due_referrals() == {"confirmed": 0, "rejected": 1}
    referral.refresh_from_db()
    assert referral.reason_key == "deleted"


def test_deleting_twice_does_nothing_more(citizen, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        accounts.delete_account(citizen, via="app")
    stale = CitizenAccount.objects.get(pk=citizen.pk)
    with django_capture_on_commit_callbacks(execute=True):
        accounts.delete_account(stale, via="website")
    assert AuditLog.objects.filter(action="citizen.account_deleted").count() == 1
    assert len(mailoutbox) == 1


def test_the_super_admin_deletion_is_audited_with_the_reason(world, citizen):
    admin = world.users["super"]
    accounts.delete_account(citizen, via="admin", by=admin, reason="fraud")
    entry = AuditLog.objects.get(action="citizen.account_deleted")
    assert (entry.via, entry.actor_id, entry.data) == ("admin", admin.pk, {"via": "admin", "reason": "fraud"})
    assert_deleted(citizen)


# --- On the website, with an emailed code ----------------------------------------------------------------------

def ask_code(api, login="9876543210"):
    return api().post(DELETE_CODE, {"login": login}, format="json")


def test_the_website_deletes_with_the_emailed_code(api, citizen, mailoutbox, django_capture_on_commit_callbacks, english):
    sent = ask_code(api)
    assert sent.status_code == 200 and set(sent.json()) == {"expires_in", "resend_after"}
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]
    assert VerificationCode.objects.get(account=citizen).purpose == VerificationCode.Purpose.DELETE
    code = code_in(mailoutbox[0])

    with django_capture_on_commit_callbacks(execute=True):
        done = api().post(DELETE_WITH_CODE, {"login": "lakshmi.devi@gmail.com", "code": code}, format="json")
    assert done.status_code == 200 and done.json() == {"deleted": True}
    assert_deleted(citizen)
    assert AuditLog.objects.get(action="citizen.account_deleted").via == "website"
    assert len(mailoutbox) == 2 and mailoutbox[1].to == ["lakshmi.devi@gmail.com"]

    again = api().post(DELETE_WITH_CODE, {"login": "9876543210", "code": code}, format="json")
    assert (again.status_code, err(again)) == (400, "code_expired")


def test_the_same_answer_for_every_login(api, world, citizen, mailoutbox):
    blocked = make_citizen(world, 2, email="blocked@gmail.com", mobile="9876500002")
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    gone = make_citizen(world, 3, email="gone@gmail.com", mobile="9876500003")
    accounts.delete_account(gone, via="app")
    mailoutbox.clear()

    answers = {
        login: ask_code(api, login).json()
        for login in ("9876543210", "nobody@gmail.com", "9000000000", "blocked@gmail.com", "gone@gmail.com")
    }
    assert len({tuple(sorted(a.items())) for a in answers.values()}) == 1
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]  # only the open account


def test_a_number_with_a_leading_0_gets_the_code(api, citizen, mailoutbox):
    """BVTGC-153: the website accepts "0" + 10 digits, so the server must find the account by it."""
    assert ask_code(api, "098765 43210").status_code == 200
    assert [m.to for m in mailoutbox] == [["lakshmi.devi@gmail.com"]]


def test_a_password_reset_code_does_not_delete(api, citizen, mailoutbox):
    api().post(FORGOT, {"login": "9876543210"}, format="json")
    reset_code = code_in(mailoutbox[-1])
    response = api().post(DELETE_WITH_CODE, {"login": "9876543210", "code": reset_code}, format="json")
    assert (response.status_code, err(response)) == (400, "code_expired")
    citizen.refresh_from_db()
    assert citizen.deleted_at is None


def test_a_wrong_code_counts_its_tries(api, citizen, mailoutbox):
    ask_code(api)
    right = code_in(mailoutbox[-1])
    wrong = "000000" if right != "000000" else "111111"
    response = api().post(DELETE_WITH_CODE, {"login": "9876543210", "code": wrong}, format="json")
    assert (response.status_code, err(response)) == (400, "code_wrong")
    assert response.json()["error"]["attempts_left"] == 4
    citizen.refresh_from_db()
    assert citizen.deleted_at is None


def test_every_step_answers_the_same_for_every_login(api, world, citizen, mailoutbox, code_journey, english):
    """BVTGC-152: the website's deletion page no longer tells a registered login apart by the wait for a
    new code, the day's limit or a wrong code (TC-142-07); only the registered one gets emails."""
    blocked = make_citizen(world, 2, email="blocked@gmail.com", mobile="9876500002")
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    gone = make_citizen(world, 3, email="gone@gmail.com", mobile="9876500003")
    accounts.delete_account(gone, via="app")
    mailoutbox.clear()
    runs = {
        login: code_journey(login, DELETE_CODE, DELETE_WITH_CODE)
        for login in ["9876543210", "nobody@gmail.com", "blocked@gmail.com", "gone@gmail.com", "9876500003"]
    }
    assert len({tuple(run) for run in runs.values()}) == 1, runs
    steps = runs["9876543210"]
    assert [step[0] for step in steps] == [200, 429] + [400] * 6 + [200] * 4 + [429]
    assert [step[3] for step in steps[2:7]] == [4, 3, 2, 1, 0] and steps[7][1] == "code_expired"
    assert {tuple(m.to) for m in mailoutbox} == {("lakshmi.devi@gmail.com",)}
    citizen.refresh_from_db()
    assert citizen.deleted_at is None


def test_the_forgotten_password_and_the_deletion_share_the_wait(api, wallet_on, english):
    """As a registered account's codes do, an unknown login's codes share one 60-second wait (BVTGC-152)."""
    assert ask_code(api, "nobody@gmail.com").status_code == 200
    again = api().post(FORGOT, {"login": "nobody@gmail.com"}, format="json")
    assert (again.status_code, err(again)) == (429, "code_cooldown")


def test_an_unknown_login_reads_as_an_expired_code(api, citizen):
    response = api().post(DELETE_WITH_CODE, {"login": "nobody@gmail.com", "code": "123456"}, format="json")
    assert (response.status_code, err(response)) == (400, "code_expired")


# --- Housekeeping ---------------------------------------------------------------------------------------------

def pending(*, expires_in: timedelta) -> PendingSignup:
    now = timezone.now()
    return PendingSignup.objects.create(
        name="రవి", village_id=1, mobile_hash=hash_mobile("9876500099"), mobile_last4="0099",
        email="ravi@gmail.com", email_key="ravi@gmail.com", password="x", age_confirmed_at=now,
        consent_version="v1", expires_at=now + expires_in,
    )


def code_row(account, *, days_ago: float) -> VerificationCode:
    created = timezone.now() - timedelta(days=days_ago)
    return VerificationCode.objects.create(
        purpose=VerificationCode.Purpose.RESET_PASSWORD, account=account, email_key=account.email_key,
        code_hash="x", expires_at=created + timedelta(minutes=10), created_at=created,
    )


def test_housekeeping_forgets_what_the_accounts_no_longer_need(world, citizen):
    old_signup, new_signup = pending(expires_in=timedelta(minutes=-1)), pending(expires_in=timedelta(hours=1))
    old_code, new_code = code_row(citizen, days_ago=8), code_row(citizen, days_ago=6)
    today = timezone.localdate()
    CitizenActiveDay.objects.create(account=citizen, day=today - timedelta(days=61))
    CitizenActiveDay.objects.create(account=citizen, day=today - timedelta(days=59))
    long_ago, recent = make_citizen(world, 2), make_citizen(world, 3)
    for account in (long_ago, recent):
        accounts.delete_account(account, via="app")
    CitizenAccount.objects.filter(pk=long_ago.pk).update(deleted_at=timezone.now() - timedelta(days=366))

    counts = run_housekeeping()
    assert {k: v for k, v in counts.items() if k.startswith("citizen_")} == {
        "citizen_pending_signups": 1, "citizen_codes": 1, "citizen_active_days": 1, "citizen_deleted_fingerprints": 1,
    }
    assert list(PendingSignup.objects.values_list("pk", flat=True)) == [new_signup.pk]
    assert not PendingSignup.objects.filter(pk=old_signup.pk).exists()
    assert list(VerificationCode.objects.values_list("pk", flat=True)) == [new_code.pk]
    assert not VerificationCode.objects.filter(pk=old_code.pk).exists()
    assert CitizenActiveDay.objects.get().day == today - timedelta(days=59)
    long_ago.refresh_from_db()
    recent.refresh_from_db()
    assert (long_ago.deleted_mobile_hash, long_ago.deleted_email_key) == ("", "")
    assert recent.deleted_mobile_hash and recent.deleted_email_key

    again = run_housekeeping()
    assert all(again[k] == 0 for k in counts if k.startswith("citizen_"))


def test_zero_keep_days_keep_the_fingerprints(world, citizen, settings):
    settings.CITIZEN_DELETED_KEEP_DAYS = 0
    accounts.delete_account(citizen, via="app")
    CitizenAccount.objects.filter(pk=citizen.pk).update(deleted_at=timezone.now() - timedelta(days=4000))
    assert run_housekeeping()["citizen_deleted_fingerprints"] == 0
    citizen.refresh_from_db()
    assert citizen.deleted_mobile_hash and citizen.deleted_email_key


def test_housekeeping_keeps_the_wallet_and_open_accounts(world, citizen):
    wallet.start_referral(make_citizen(world, 2), citizen)
    run_housekeeping()
    citizen.refresh_from_db()
    assert citizen.email == "lakshmi.devi@gmail.com" and citizen.deleted_at is None
    assert Referral.objects.count() == 1 and WalletEntry.objects.count() == 2


def test_the_citizen_group_stays_for_the_others(world, citizen):
    other = make_citizen(world, 2)
    accounts.delete_account(citizen, via="app")
    group = Group.objects.get(pk=RoleAssignment.objects.get(user=other.user, is_active=True).group_id)
    assert other.user.groups.filter(pk=group.pk).exists()
