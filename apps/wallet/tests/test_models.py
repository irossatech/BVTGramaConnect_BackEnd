"""The database guards of wallets, entries and referrals (BVTGC-120).

The services (BVTGC-126) rely on these: no entry of 0 points, only adjustments negative, one entry per
idempotency key and wallet, no self-referral, one referral per new citizen, and a wallet cache that
cannot go below zero.
"""

from datetime import timedelta

import pytest
from django.db import DataError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.citizens.models import CitizenAccount
from apps.wallet.models import Referral, Wallet, WalletEntry

pytestmark = pytest.mark.django_db


def make_wallet(n: int) -> Wallet:
    user = User.objects.create_user(username=f"citizen{n}", name=f"Citizen {n}", password="x")
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=1, age_confirmed_at=now, consent_version="v1", consent_at=now, referral_code=f"CODE{n:04d}"
    )
    return Wallet.objects.create(account=account)


def entry(wallet: Wallet, points: int, kind=WalletEntry.Kind.WELCOME_BONUS, key="k1") -> WalletEntry:
    return WalletEntry.objects.create(
        wallet=wallet, points=points, kind=kind, status=WalletEntry.Status.HELD, idempotency_key=key
    )


def test_an_entry_is_never_zero_points():
    wallet = make_wallet(1)
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(wallet, 0, kind=WalletEntry.Kind.ADJUSTMENT)


@pytest.mark.parametrize("kind", [WalletEntry.Kind.REFERRAL_REWARD, WalletEntry.Kind.WELCOME_BONUS])
def test_only_an_adjustment_takes_points_away(kind):
    wallet = make_wallet(1)
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(wallet, -5, kind=kind)
    assert entry(wallet, -5, kind=WalletEntry.Kind.ADJUSTMENT, key="adj").points == -5


def test_an_idempotency_key_pays_once_per_wallet():
    first, second = make_wallet(1), make_wallet(2)
    entry(first, 20, key="ref:1:welcome")
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(first, 20, key="ref:1:welcome")
    entry(second, 20, key="ref:1:welcome")  # another wallet may use the same key
    assert WalletEntry.objects.count() == 2


def test_no_self_referral_and_one_referral_per_new_citizen():
    a, b, c = make_wallet(1).account, make_wallet(2).account, make_wallet(3).account
    later = timezone.now() + timedelta(days=7)
    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(referrer=a, referee=a, confirm_after=later)
    Referral.objects.create(referrer=a, referee=b, confirm_after=later)
    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(referrer=c, referee=b, confirm_after=later)


def test_the_wallet_cache_cannot_go_below_zero():
    wallet = make_wallet(1)
    Wallet.objects.filter(pk=wallet.pk).update(confirmed_points=F("confirmed_points") + 5)
    # SQLite and PostgreSQL refuse with a CHECK constraint, MySQL with an UNSIGNED column.
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        Wallet.objects.filter(pk=wallet.pk).update(confirmed_points=F("confirmed_points") - 6)
    wallet.refresh_from_db()
    assert wallet.confirmed_points == 5
