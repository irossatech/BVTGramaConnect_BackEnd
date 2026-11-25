"""The Super Admin's referrals, wallets and the CSV export (BVTGC-128; requirements J8, FR-29, FR-33, FR-35;
contract section 21, "Super Admin").

Only the Super Admin. A decision is one conditional UPDATE, so a second one gets 409 already_decided;
taking points away never takes the balance below zero; the export holds no full email or number.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens import services_account as accounts
from apps.citizens.models import CitizenAccount
from apps.citizens.services import hash_android_id
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel
from apps.posts.models import CITIZEN_ROLE
from apps.wallet import services, views_admin
from apps.wallet.models import Referral, Wallet, WalletEntry

pytestmark = pytest.mark.django_db

K, S = WalletEntry.Kind, WalletEntry.Status
REFERRALS = "/api/v1/admin/referrals/"
EXPORT = "/api/v1/admin/wallet/export/"
OFFICIALS = [
    "da_guntur", "da_krishna", "ma_tenali", "ma_mangalagiri", "ma_gannavaram", "va_kolakaluru", "va_nandivelugu",
    "va_kaza", "va_nidamanuru", "pm_kolakaluru", "pm_nandivelugu", "pm_kaza", "pm_nidamanuru",
]


@pytest.fixture
def wallet_on(settings):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    return settings


def make_citizen(world, n: int, *, name: str | None = None) -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen.w{n}", name=name or f"Citizen {n}", password="48213957")
    ref = world.ref("kolakaluru")
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    email, mobile = f"person{n}@gmail.com", f"9{n:09d}"
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now, mobile_hash=hash_mobile(mobile),
        mobile_last4=mobile[-4:], referral_code=f"WAL{n:05d}", signup_android_id_hash=hash_android_id(f"phone-{n}"),
    )
    Wallet.objects.create(account=account)
    return account


@pytest.fixture
def pair(world, wallet_on):
    """Lakshmi invited Ravi: a PENDING referral, 50 and 20 points on hold."""
    referrer, friend = make_citizen(world, 1, name="లక్ష్మి"), make_citizen(world, 2, name="Ravi")
    return services.start_referral(friend, referrer)


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def err(response) -> str:
    return response.json()["error"]["code"]


def cache(account) -> tuple[int, int]:
    wallet = Wallet.objects.get(account=account)
    return wallet.held_points, wallet.confirmed_points


def routes(pair) -> list[tuple[str, str, dict | None]]:
    wallet = f"/api/v1/admin/wallets/{pair.referrer_id}/"
    return [
        ("get", REFERRALS, None),
        ("post", f"{REFERRALS}{pair.id}/confirm/", {"comment": "Checked"}),
        ("post", f"{REFERRALS}{pair.id}/reject/", {"comment": "Fake"}),
        ("get", wallet, None),
        ("post", wallet + "adjust/", {"points": 10, "reason": "Help-line fix"}),
        ("get", EXPORT + "?type=referrals&format=csv", None),
    ]


def call(client, method, url, body):
    return client.get(url) if method == "get" else client.post(url, body, format="json")


def read_csv(response) -> list[list[str]]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


# --- Who may call ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("name", OFFICIALS)
def test_no_other_official_may_call(api, world, pair, name, index):
    method, url, body = routes(pair)[index]
    response = call(api(world.users[name]), method, url, body)
    assert (response.status_code, err(response)) == (403, "permission_denied")
    pair.refresh_from_db()
    assert pair.status == Referral.Status.PENDING and cache(pair.referrer) == (50, 0)


@pytest.mark.parametrize("index", range(6))
def test_a_citizen_and_an_anonymous_caller_are_refused(api, pair, index):
    method, url, body = routes(pair)[index]
    assert call(api(pair.referrer.user), method, url, body).status_code == 403
    assert call(api(), method, url, body).status_code == 401
    assert cache(pair.referrer) == (50, 0)


@pytest.mark.parametrize("index", range(6))
def test_the_super_admin_may_call_each_one(admin, pair, index):
    method, url, body = routes(pair)[index]
    assert call(admin, method, url, body).status_code == 200


# --- Referrals -------------------------------------------------------------------------------------------------------

def test_the_list_counts_each_status_and_hides_full_details(admin, world, pair):
    done = services.start_referral(make_citizen(world, 3), make_citizen(world, 4))
    services.decide(done, confirm=False, reason_key="not_active")
    body = admin.get(REFERRALS).json()
    assert body["status_counts"] == {"PENDING": 1, "CONFIRMED": 0, "REJECTED": 1} and body["count"] == 2
    pending = admin.get(REFERRALS + "?status=PENDING").json()["results"]
    assert len(pending) == 1
    row = pending[0]
    assert row["id"] == str(pair.id) and row["allowed_actions"] == ["confirm", "reject"]
    assert row["referrer"] == {"id": str(pair.referrer_id), "name": "లక్ష్మి", "mobile_last4": "0001", "referral_code": "WAL00001"}
    assert row["referee"]["name"] == "Ravi" and row["decided_by"] is None and row["reason_key"] is None
    rejected = admin.get(REFERRALS + "?status=REJECTED").json()["results"][0]
    assert (rejected["reason_key"], rejected["allowed_actions"]) == ("not_active", [])
    assert "person1@gmail.com" not in admin.get(REFERRALS).content.decode()
    assert err(admin.get(REFERRALS + "?status=done")) == "validation_error"


@pytest.mark.parametrize("q", ["ravi", "లక్ష్మి", "0002", "wal00001"])
def test_search_either_side(admin, world, pair, q):
    services.start_referral(make_citizen(world, 3), make_citizen(world, 4))
    body = admin.get(REFERRALS + f"?q={q}").json()
    assert [row["id"] for row in body["results"]] == [str(pair.id)]
    assert body["status_counts"]["PENDING"] == 1


def test_confirming_early_pays_both_at_once(admin, world, pair):
    response = admin.post(f"{REFERRALS}{pair.id}/confirm/", {"comment": "Known family, checked by phone"}, format="json")
    row = response.json()
    assert row["status"] == "CONFIRMED" and row["allowed_actions"] == []
    assert row["decided_by"] == {"id": str(world.users["super"].pk), "name": world.users["super"].name}
    assert row["comment"] == "Known family, checked by phone"
    assert cache(pair.referrer) == (0, 50) and cache(pair.referee) == (0, 20)
    entry = AuditLog.objects.get(action="referral.confirmed")
    assert (entry.via, entry.actor_id) == ("admin", world.users["super"].pk)


def test_rejecting_shows_the_comment_in_both_histories(admin, api, pair, english):
    admin.post(f"{REFERRALS}{pair.id}/reject/", {"comment": "Same family, two phones"}, format="json")
    pair.refresh_from_db()
    assert (pair.status, pair.reason_key) == (Referral.Status.REJECTED, "rejected")
    assert cache(pair.referrer) == (0, 0) and cache(pair.referee) == (0, 0)
    for account in (pair.referrer, pair.referee):
        entry = api(account.user).get("/api/v1/wallet/").json()["results"][0]
        assert (entry["status"], entry["reason_key"], entry["reason"]) == ("CANCELLED", "rejected", "Same family, two phones")
    assert AuditLog.objects.get(action="referral.rejected").data["reason"] == "rejected"


def test_a_second_decision_is_409_with_the_row(admin, pair):
    services.decide_due_referrals(now=pair.confirm_after + timedelta(minutes=1))  # the hourly job first: not_active
    response = admin.post(f"{REFERRALS}{pair.id}/confirm/", {"comment": "late"}, format="json")
    assert (response.status_code, err(response)) == (409, "already_decided")
    assert response.json()["error"]["referral"]["status"] == "REJECTED"
    assert cache(pair.referrer) == (0, 0)


def test_a_decision_needs_a_comment_and_a_known_referral(admin, pair):
    assert err(admin.post(f"{REFERRALS}{pair.id}/reject/", {}, format="json")) == "validation_error"
    unknown = admin.post(f"{REFERRALS}{uuid.uuid4()}/reject/", {"comment": "x"}, format="json")
    assert (unknown.status_code, err(unknown)) == (404, "not_found")
    pair.refresh_from_db()
    assert pair.status == Referral.Status.PENDING


# --- One wallet and adjustments ----------------------------------------------------------------------------------------

def adjust(admin, account, points, reason="Help-line fix"):
    return admin.post(f"/api/v1/admin/wallets/{account.pk}/adjust/", {"points": points, "reason": reason}, format="json")


def test_adding_points_is_confirmed_at_once_with_the_reason(admin, api, world, pair, english):
    response = adjust(admin, pair.referrer, 30, "Missed reward, ticket 12")
    assert response.status_code == 200
    assert response.json() == {"confirmed_points": 30, "held_points": 50, "next_release_at": response.json()["next_release_at"]}
    entry = api(pair.referrer.user).get("/api/v1/wallet/").json()["results"][0]
    assert (entry["kind"], entry["points"], entry["status"], entry["reason"]) == ("ADJUSTMENT", 30, "CONFIRMED", "Missed reward, ticket 12")
    audit = AuditLog.objects.get(action="wallet.adjusted")
    assert (audit.actor_id, audit.data) == (world.users["super"].pk, {"points": 30, "reason": "Missed reward, ticket 12"})

    history = admin.get(f"/api/v1/admin/wallets/{pair.referrer_id}/").json()
    assert (history["confirmed_points"], history["held_points"], history["count"]) == (30, 50, 2)
    newest = history["results"][0]
    assert newest["created_by"] == {"id": str(world.users["super"].pk), "name": world.users["super"].name}
    assert history["results"][1]["referral_id"] == str(pair.id)


def test_taking_points_away_never_goes_below_zero(admin, pair):
    adjust(admin, pair.referrer, 40)
    assert adjust(admin, pair.referrer, -15).json()["confirmed_points"] == 25
    response = adjust(admin, pair.referrer, -26)
    assert (response.status_code, err(response)) == (409, "not_enough_points")
    assert response.json()["error"]["confirmed_points"] == 25
    assert cache(pair.referrer) == (50, 25)
    assert WalletEntry.objects.filter(kind=K.ADJUSTMENT).count() == 2
    assert AuditLog.objects.filter(action="wallet.adjusted").count() == 2
    assert services.check_wallets() == 0


@pytest.mark.parametrize("body", [
    {"points": 0, "reason": "x"},
    {"points": 100_001, "reason": "x"},
    {"points": -100_001, "reason": "x"},
    {"points": 5},
    {"points": 2.5, "reason": "x"},
])
def test_an_adjustment_needs_whole_points_and_a_reason(admin, pair, body):
    response = admin.post(f"/api/v1/admin/wallets/{pair.referrer_id}/adjust/", body, format="json")
    assert err(response) == "validation_error"
    assert not WalletEntry.objects.filter(kind=K.ADJUSTMENT).exists()


def test_no_adjustment_for_a_deleted_or_unknown_account(admin, pair):
    accounts.delete_account(pair.referrer, via="app")
    response = adjust(admin, pair.referrer, 10)
    assert (response.status_code, err(response)) == (409, "invalid_transition")
    unknown = admin.post(f"/api/v1/admin/wallets/{uuid.uuid4()}/adjust/", {"points": 1, "reason": "x"}, format="json")
    assert unknown.status_code == 404
    assert admin.get(f"/api/v1/admin/wallets/{uuid.uuid4()}/").status_code == 404


# --- The CSV export -----------------------------------------------------------------------------------------------------

def test_the_referrals_export_holds_no_full_email_or_number(admin, world, pair):
    rows = read_csv(admin.get(EXPORT + "?type=referrals&format=csv&q=ravi"))
    header, line = rows[0], rows[1]
    assert len(rows) == 2 and header[:4] == ["id", "started", "confirm_after", "status"]
    data = dict(zip(header, line))
    assert (data["referrer_name"], data["referrer_mobile_last4"], data["referrer_email"]) == ("లక్ష్మి", "0001", "p***@gmail.com")
    assert data["status"] == "PENDING" and "+05:30" not in data["started"]
    content = admin.get(EXPORT + "?type=referrals").content.decode()
    assert "person1@gmail.com" not in content and "9000000001" not in content
    exported = [entry.data for entry in AuditLog.objects.filter(action="report.exported")]
    assert {"report": "wallet_referrals", "filters": {"type": "referrals"}, "format": "csv"} in exported
    assert {"report": "wallet_referrals", "filters": {"type": "referrals", "q": "(given)"}, "format": "csv"} in exported


def test_the_entries_export_filters(admin, pair, english):
    adjust(admin, pair.referrer, 5, "Fix")
    one = read_csv(admin.get(EXPORT + f"?type=entries&account_id={pair.referrer_id}&kind=ADJUSTMENT"))
    assert len(one) == 2
    data = dict(zip(one[0], one[1]))
    assert (data["kind"], data["points"], data["reason"], data["status"]) == ("ADJUSTMENT", "5", "Fix", "CONFIRMED")
    assert data["email"] == "p***@gmail.com" and data["created_by"]
    every = read_csv(admin.get(EXPORT + "?type=entries"))
    assert len(every) == 4  # header, the reward, the welcome bonus, the adjustment
    today = timezone.localdate()
    assert len(read_csv(admin.get(EXPORT + f"?type=entries&from={today}&to={today}"))) == 4
    assert len(read_csv(admin.get(EXPORT + f"?type=entries&to={today - timedelta(days=1)}"))) == 1


@pytest.mark.parametrize("query", [
    "", "?type=points", "?type=entries&format=json", "?type=entries&from=25-11-2026", "?type=entries&kind=GIFT",
    "?type=entries&account_id=12",
])
def test_bad_export_requests_answer_in_json(admin, pair, query):
    response = admin.get(EXPORT + query)
    assert response.status_code == 400 and response["Content-Type"].startswith("application/json")
    assert err(response) == "validation_error"
    assert not AuditLog.objects.filter(action="report.exported").exists()


def test_too_many_rows_is_refused_not_cut(admin, pair, monkeypatch):
    monkeypatch.setattr(views_admin, "EXPORT_MAX_ROWS", 1)
    response = admin.get(EXPORT + "?type=entries&format=csv")
    assert (response.status_code, err(response)) == (400, "validation_error")
    assert not AuditLog.objects.filter(action="report.exported").exists()
