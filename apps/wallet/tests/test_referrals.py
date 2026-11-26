"""Referrals, wallet entries, the hourly decisions and the daily check (BVTGC-126; requirements D3, D10,
FR-17 to FR-29; contract section 21).
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_q.models import Schedule

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenAccount, CitizenActiveDay
from apps.citizens.services import hash_android_id
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel
from apps.posts.models import CITIZEN_ROLE
from apps.wallet import services
from apps.wallet.models import Referral, Wallet, WalletEntry
from apps.wallet.tasks import CHECK_WALLETS_SCHEDULE, DECIDE_REFERRALS_SCHEDULE, ensure_wallet_schedules

pytestmark = pytest.mark.django_db

K, S = WalletEntry.Kind, WalletEntry.Status


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    return settings


def make_citizen(world, n: int, *, phone: str = "", email: str | None = None, mobile: str | None = None) -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.r{n}", name=f"Citizen {n}", password="48213957")
    ref = world.ref("kolakaluru")
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    email = email or f"citizen{n}@gmail.com"
    mobile = mobile or f"9{n:09d}"
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now, mobile_hash=hash_mobile(mobile),
        mobile_last4=mobile[-4:], referral_code=f"REF{n:05d}", signup_android_id_hash=hash_android_id(phone),
    )
    Wallet.objects.create(account=account)
    return account


def entries(account) -> list[tuple]:
    return list(WalletEntry.objects.filter(wallet=account.wallet).values_list("kind", "points", "status", "reason_key"))


def cache(account) -> tuple[int, int]:
    wallet = Wallet.objects.get(pk=account.pk)
    return wallet.held_points, wallet.confirmed_points


def make_due(referral, *, active_days: int = 2) -> None:
    """Move a referral's hold into the past, with `active_days` app-use days of the new citizen inside it."""
    now = timezone.now()
    Referral.objects.filter(pk=referral.pk).update(pending_at=now - timedelta(days=8), confirm_after=now - timedelta(hours=1))
    for back in range(7, 7 - active_days, -1):
        CitizenActiveDay.objects.get_or_create(account=referral.referee, day=timezone.localdate(now - timedelta(days=back)))


# --- Starting a referral ---------------------------------------------------------------------------------

def test_a_friend_signing_up_with_a_code_starts_a_held_referral(api, world, wallet_on, mailoutbox):
    referrer = make_citizen(world, 1, phone="phone-a")
    body = {
        "name": "లక్ష్మి", "mobile": "9876543210", "email": "lakshmi@gmail.com", "password": "48213957",
        "village_id": world.ref("kolakaluru").id, "age_confirmed": True, "consent_version": "2026-11-25",
        "referral_code": referrer.referral_code, "android_id": "phone-b",
    }
    token = api().post("/api/v1/citizen/auth/signup/", body, format="json").json()["signup_token"]
    code = re.search(r"\b(\d{6})\b", mailoutbox[-1].subject).group(1)
    done = api().post("/api/v1/citizen/auth/signup/verify/", {"signup_token": token, "code": code}, format="json")
    assert done.status_code == 201
    assert done.json()["account"]["referral"]["status"] == "PENDING"
    assert done.json()["account"]["wallet"]["held_points"] == 20

    referral = Referral.objects.get()
    assert referral.referrer == referrer and referral.status == Referral.Status.PENDING
    assert timedelta(days=6, hours=23) < referral.confirm_after - referral.pending_at <= timedelta(days=7)
    assert entries(referrer) == [(K.REFERRAL_REWARD, 50, S.HELD, "")]
    assert entries(referral.referee) == [(K.WELCOME_BONUS, 20, S.HELD, "")]
    assert cache(referrer) == (50, 0) and cache(referral.referee) == (20, 0)
    assert AuditLog.objects.filter(action="referral.created").exists()


def test_the_sign_up_day_counts_for_the_active_rule(api, world, wallet_on, mailoutbox):
    """BVTGC-154: a friend who signs up and opens the app once more on another day has the 2 active days.
    Before, only opening the app (GET citizen/account/) counted, so the sign-up day was often lost."""
    referrer = make_citizen(world, 1, phone="phone-a")
    body = {
        "name": "లక్ష్మి", "mobile": "9876543210", "email": "lakshmi@gmail.com", "password": "48213957",
        "village_id": world.ref("kolakaluru").id, "age_confirmed": True, "consent_version": "2026-11-25",
        "referral_code": referrer.referral_code, "android_id": "phone-b",
    }
    token = api().post("/api/v1/citizen/auth/signup/", body, format="json").json()["signup_token"]
    code = re.search(r"\b(\d{6})\b", mailoutbox[-1].subject).group(1)
    assert api().post("/api/v1/citizen/auth/signup/verify/", {"signup_token": token, "code": code}, format="json").status_code == 201
    referral = Referral.objects.get()
    friend = referral.referee
    assert list(friend.active_days.values_list("day", flat=True)) == [timezone.localdate()]

    # The same, two days back: the sign-up day, then one open of the app the next day; the hold is over.
    now = timezone.now()
    CitizenActiveDay.objects.filter(account=friend).update(day=timezone.localdate(now - timedelta(days=2)))
    CitizenActiveDay.objects.create(account=friend, day=timezone.localdate(now - timedelta(days=1)))
    Referral.objects.filter(pk=referral.pk).update(pending_at=now - timedelta(days=2), confirm_after=now - timedelta(hours=1))
    assert services.decide_due_referrals() == {"confirmed": 1, "rejected": 0}


def test_two_accounts_of_one_phone_pay_only_the_welcome_bonus(world, wallet_on):
    referrer, friend = make_citizen(world, 1, phone="family"), make_citizen(world, 2, phone="family")
    services.start_referral(friend, referrer)
    assert entries(referrer) == [(K.REFERRAL_REWARD, 50, S.CANCELLED, "same_phone")]
    assert cache(referrer) == (0, 0)
    assert entries(friend) == [(K.WELCOME_BONUS, 20, S.HELD, "")]


def test_no_welcome_bonus_without_a_phone_id(world, wallet_on):
    referrer, friend = make_citizen(world, 1, phone="a"), make_citizen(world, 2, phone="")
    services.start_referral(friend, referrer)
    assert entries(friend) == [(K.WELCOME_BONUS, 20, S.CANCELLED, "no_device")] and cache(friend) == (0, 0)
    assert entries(referrer) == [(K.REFERRAL_REWARD, 50, S.HELD, "")]


def test_at_most_three_welcome_bonuses_per_phone_deleted_accounts_included(world, wallet_on):
    referrer = make_citizen(world, 1, phone="referrer-phone")
    earlier = [make_citizen(world, n, phone="shared") for n in (2, 3, 4)]
    for friend in earlier:
        services.start_referral(friend, referrer)
    CitizenAccount.objects.filter(pk=earlier[0].pk).update(deleted_at=timezone.now(), email_key=None, mobile_hash=None)
    fourth = make_citizen(world, 5, phone="shared")
    services.start_referral(fourth, make_citizen(world, 6, phone="other"))
    assert entries(fourth) == [(K.WELCOME_BONUS, 20, S.CANCELLED, "welcome_limit")]


def test_no_second_welcome_bonus_after_deleting_the_account(world, wallet_on):
    referrer = make_citizen(world, 1, phone="a")
    first = make_citizen(world, 2, phone="b", mobile="9876543210", email="lakshmi@gmail.com")
    services.start_referral(first, referrer)
    CitizenAccount.objects.filter(pk=first.pk).update(
        deleted_at=timezone.now(), deleted_mobile_hash=first.mobile_hash, mobile_hash=None,
        deleted_email_key=first.email_key, email_key=None,
    )
    again = make_citizen(world, 3, phone="c", mobile="9876543210", email="other@gmail.com")
    services.start_referral(again, make_citizen(world, 4, phone="d"))
    assert entries(again) == [(K.WELCOME_BONUS, 20, S.CANCELLED, "welcome_limit")]


def test_zero_points_settings_write_no_entry(world, wallet_on):
    wallet_on.REFERRAL_REWARD_POINTS = 0
    wallet_on.WELCOME_BONUS_POINTS = 0
    referrer, friend = make_citizen(world, 1, phone="a"), make_citizen(world, 2, phone="b")
    services.start_referral(friend, referrer)
    assert entries(referrer) == [] and entries(friend) == []


def test_an_idempotency_key_pays_once(world, wallet_on):
    account = make_citizen(world, 1)
    for _ in range(2):
        services._add_entry(account.wallet, points=20, kind=K.WELCOME_BONUS, status=S.HELD, key="ref:x:welcome")
    assert len(entries(account)) == 1 and cache(account) == (20, 0)


# --- The hourly decisions ----------------------------------------------------------------------------------

@pytest.fixture
def pair(world, wallet_on):
    referrer, friend = make_citizen(world, 1, phone="a"), make_citizen(world, 2, phone="b")
    return services.start_referral(friend, referrer)


def test_an_active_friend_confirms_both_points(pair):
    make_due(pair, active_days=2)
    assert services.decide_due_referrals() == {"confirmed": 1, "rejected": 0}
    pair.refresh_from_db()
    assert pair.status == Referral.Status.CONFIRMED and pair.decided_at is not None
    assert cache(pair.referrer) == (0, 50) and cache(pair.referee) == (0, 20)
    assert all(status == S.CONFIRMED for _k, _p, status, _r in entries(pair.referrer) + entries(pair.referee))
    entry = AuditLog.objects.get(action="referral.confirmed")
    assert entry.via == "system"


@pytest.mark.parametrize("change, reason", [
    (lambda pair: None, "not_active"),  # no app-use days
    (lambda pair: User.objects.filter(pk=pair.referrer.user_id).update(is_active=False), "blocked"),
    (lambda pair: CitizenAccount.objects.filter(pk=pair.referee_id).update(deleted_at=timezone.now()), "deleted"),
])
def test_a_referral_is_not_confirmed_with_its_reason(pair, change, reason):
    make_due(pair, active_days=0 if reason == "not_active" else 2)
    change(pair)
    assert services.decide_due_referrals() == {"confirmed": 0, "rejected": 1}
    pair.refresh_from_db()
    assert (pair.status, pair.reason_key) == (Referral.Status.REJECTED, reason)
    assert cache(pair.referrer) == (0, 0) and cache(pair.referee) == (0, 0)
    assert entries(pair.referee) == [(K.WELCOME_BONUS, 20, S.CANCELLED, reason)]


def test_the_monthly_limit_per_referrer(world, wallet_on):
    wallet_on.REFERRALS_PER_MONTH = 1
    referrer = make_citizen(world, 1, phone="a")
    first = services.start_referral(make_citizen(world, 2, phone="b"), referrer)
    second = services.start_referral(make_citizen(world, 3, phone="c"), referrer)
    make_due(first)
    make_due(second)
    Referral.objects.filter(pk=second.pk).update(confirm_after=timezone.now() - timedelta(minutes=30))  # decided after the first
    assert services.decide_due_referrals() == {"confirmed": 1, "rejected": 1}
    second.refresh_from_db()
    assert second.reason_key == "monthly_limit"


def test_no_monthly_limit_when_zero(world, wallet_on):
    wallet_on.REFERRALS_PER_MONTH = 0
    referrer = make_citizen(world, 1, phone="a")
    for n in (2, 3):
        make_due(services.start_referral(make_citizen(world, n, phone=f"p{n}"), referrer))
    assert services.decide_due_referrals() == {"confirmed": 2, "rejected": 0}


def test_a_referral_still_on_hold_is_left_alone(pair):
    assert services.decide_due_referrals() == {"confirmed": 0, "rejected": 0}
    pair.refresh_from_db()
    assert pair.status == Referral.Status.PENDING


def test_the_first_decision_wins_and_the_job_runs_twice_safely(pair):
    make_due(pair)
    assert services.decide(pair, confirm=False, reason_key="rejected") is True
    assert services.decide(pair, confirm=True) is False
    assert services.decide_due_referrals() == {"confirmed": 0, "rejected": 0}
    assert cache(pair.referrer) == (0, 0)


# --- The daily check ----------------------------------------------------------------------------------------

def test_the_daily_check_finds_a_cache_that_differs(pair):
    assert services.check_wallets() == 0
    Wallet.objects.filter(pk=pair.referrer_id).update(held_points=999)
    assert services.check_wallets() == 1
    entry = AuditLog.objects.get(action="wallet.mismatch")
    assert entry.data == {"cache": [999, 0], "entries": [50, 0]}


def test_the_schedules_are_registered():
    ensure_wallet_schedules()
    ensure_wallet_schedules()  # safe to run on every deploy
    names = dict(Schedule.objects.values_list("name", "schedule_type"))
    assert names[DECIDE_REFERRALS_SCHEDULE] == Schedule.HOURLY and names[CHECK_WALLETS_SCHEDULE] == Schedule.DAILY
    assert Schedule.objects.filter(name=DECIDE_REFERRALS_SCHEDULE).count() == 1


# --- GET /wallet/ ---------------------------------------------------------------------------------------------

WALLET = "/api/v1/wallet/"


def test_the_wallet_is_hidden_while_off_and_needs_a_citizen(api, world, settings):
    assert api().get(WALLET).status_code == 404
    settings.WALLET_ENABLED = True
    assert api().get(WALLET).status_code == 401
    assert api(world.users["va_kolakaluru"]).get(WALLET).status_code == 403


def test_the_wallet_shows_the_balance_and_the_history_with_reasons(api, pair, english):
    friend = pair.referee
    make_due(pair, active_days=0)
    services.decide_due_referrals()
    body = api(friend.user).get(WALLET).json()
    assert (body["confirmed_points"], body["held_points"], body["next_release_at"]) == (0, 0, None)
    assert [(row["kind"], row["points"], row["status"], row["reason_key"]) for row in body["results"]] == [
        ("WELCOME_BONUS", 20, "CANCELLED", "not_active")
    ]
    assert body["results"][0]["reason"] == "Not confirmed: the new citizen did not use the app on enough days."


def test_each_held_row_has_its_own_release_date(api, world, wallet_on):
    """BVTGC-157: two friends who joined on different days. Each held row says when it is decided (its referral's
    end of hold); the summary's next_release_at is the earliest; a decided row has none."""
    referrer = make_citizen(world, 1, phone="a")
    first = services.start_referral(make_citizen(world, 2, phone="b"), referrer)
    second = services.start_referral(make_citizen(world, 3, phone="c"), referrer)
    now = timezone.now()
    Referral.objects.filter(pk=first.pk).update(confirm_after=now + timedelta(days=2))
    Referral.objects.filter(pk=second.pk).update(confirm_after=now + timedelta(days=5))
    first.refresh_from_db()
    second.refresh_from_db()

    def release_dates() -> tuple[dict, str | None]:
        body = api(referrer.user).get(WALLET).json()
        return {row["id"]: row["release_at"] for row in body["results"]}, body["next_release_at"]

    rows, next_release = release_dates()
    first_row = str(WalletEntry.objects.get(wallet_id=referrer.pk, referral=first).pk)
    second_row = str(WalletEntry.objects.get(wallet_id=referrer.pk, referral=second).pk)
    assert parse_datetime(rows[first_row]) == first.confirm_after
    assert parse_datetime(rows[second_row]) == second.confirm_after
    assert rows[first_row].endswith("+05:30")  # India time, as every time the API sends
    assert parse_datetime(next_release) == first.confirm_after  # the earliest

    assert services.decide(first, confirm=True) is True
    rows, next_release = release_dates()
    assert rows[first_row] is None  # decided: nothing left to wait for
    assert parse_datetime(next_release) == second.confirm_after


def test_the_history_takes_the_same_queries_however_many_rows(api, world, wallet_on):
    """release_at reads each entry's referral: loaded with the entries, never one query per row (BVTGC-157)."""
    referrer = make_citizen(world, 1, phone="a")
    services.start_referral(make_citizen(world, 2, phone="b"), referrer)
    client = api(referrer.user)
    assert client.get(WALLET).status_code == 200  # warms the runtime settings' cache, as on a server
    with CaptureQueriesContext(connection) as few:
        assert client.get(WALLET).status_code == 200
    for n in range(3, 7):
        services.start_referral(make_citizen(world, n, phone=f"p{n}"), referrer)
    with CaptureQueriesContext(connection) as many:
        assert len(client.get(WALLET).json()["results"]) == 5
    assert len(many.captured_queries) == len(few.captured_queries)


def test_the_reasons_are_telugu_by_default(api, world, wallet_on):
    referrer, friend = make_citizen(world, 1, phone="family"), make_citizen(world, 2, phone="family")
    services.start_referral(friend, referrer)
    row = api(referrer.user).get(WALLET).json()["results"][0]
    assert row["reason"] == "బహుమతి లేదు: రెండు ఖాతాలు ఒకే ఫోన్‌లో నమోదయ్యాయి."


def test_the_history_pages_newest_first_with_the_balance_on_every_page(api, world, wallet_on):
    account = make_citizen(world, 1)
    for n in range(25):
        services._add_entry(account.wallet, points=1, kind=K.ADJUSTMENT, status=S.CONFIRMED, key=f"adj:{n}", reason=f"#{n}")
    first = api(account.user).get(WALLET).json()
    assert len(first["results"]) == 20 and first["results"][0]["reason"] == "#24" and first["confirmed_points"] == 25
    second = api(account.user).get(first["next"]).json()
    assert len(second["results"]) == 5 and second["confirmed_points"] == 25 and second["next"] is None
