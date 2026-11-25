"""The database guards of the citizen wallet accounts (BVTGC-120).

They hold on SQLite, MySQL and PostgreSQL alike: unique logins while set, freed by deletion, one owner
per code, one active-day row per day, and no user deleted from under an account.
"""

from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.citizens.models import CitizenAccount, CitizenActiveDay, PendingSignup, VerificationCode

pytestmark = pytest.mark.django_db


def make_account(n: int, **extra) -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen{n}", name=f"Citizen {n}", password="x")
    now = timezone.now()
    fields = {
        "user": user,
        "village_id": 1,
        "age_confirmed_at": now,
        "consent_version": "v1",
        "consent_at": now,
        "referral_code": f"CODE{n:04d}",
    }
    fields.update(extra)
    return CitizenAccount.objects.create(**fields)


def make_pending() -> PendingSignup:
    return PendingSignup.objects.create(
        name="రవి",
        village_id=1,
        mobile_hash="m" * 64,
        mobile_last4="3210",
        email="ravi@example.com",
        email_key="ravi@example.com",
        password="hash",
        age_confirmed_at=timezone.now(),
        consent_version="v1",
        expires_at=timezone.now() + timedelta(hours=24),
    )


def code(**owner) -> VerificationCode:
    return VerificationCode.objects.create(
        purpose=VerificationCode.Purpose.SIGNUP,
        email_key="ravi@example.com",
        code_hash="c" * 64,
        expires_at=timezone.now() + timedelta(minutes=10),
        **owner,
    )


@pytest.mark.parametrize("field", ["email_key", "mobile_hash", "referral_code"])
def test_logins_and_referral_codes_are_unique(field):
    make_account(1, email_key="a@gmail.com", mobile_hash="h" * 64, referral_code="ABCDEFGH")
    taken = {"email_key": "a@gmail.com", "mobile_hash": "h" * 64, "referral_code": "ABCDEFGH"}[field]
    with pytest.raises(IntegrityError), transaction.atomic():
        make_account(2, **{field: taken})


def test_deleted_accounts_free_their_email_and_number():
    # Null after deletion (or after the Super Admin removed the number), and many nulls are allowed.
    make_account(1, email_key=None, mobile_hash=None, deleted_email_key="a@gmail.com")
    make_account(2, email_key=None, mobile_hash=None, deleted_email_key="a@gmail.com")
    make_account(3, email_key="a@gmail.com", mobile_hash="h" * 64)
    assert CitizenAccount.objects.count() == 3


def test_a_code_belongs_to_exactly_one_owner():
    account = make_account(1)
    pending = make_pending()
    code(account=account)
    code(pending_signup=pending)
    with pytest.raises(IntegrityError), transaction.atomic():
        code()
    with pytest.raises(IntegrityError), transaction.atomic():
        code(account=account, pending_signup=pending)


def test_one_active_day_row_per_account_and_day():
    account = make_account(1)
    CitizenActiveDay.objects.create(account=account, day=date(2026, 11, 25))
    CitizenActiveDay.objects.create(account=account, day=date(2026, 11, 26))
    with pytest.raises(IntegrityError), transaction.atomic():
        CitizenActiveDay.objects.create(account=account, day=date(2026, 11, 25))


def test_finishing_or_expiring_a_sign_up_deletes_its_codes():
    pending = make_pending()
    code(pending_signup=pending)
    pending.delete()
    assert not VerificationCode.objects.exists()


def test_the_user_behind_an_account_cannot_be_deleted():
    # Deleting an account scrambles the user and keeps both rows (Q8); nothing deletes the user.
    account = make_account(1)
    with pytest.raises(ProtectedError):
        account.user.delete()
