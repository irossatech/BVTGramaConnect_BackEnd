"""The Super Admin's work on citizen wallet accounts (BVTGC-128; requirements D11, D16, FR-6, FR-9, FR-16,
FR-33, FR-35; contract section 21, its Super Admin table).

Find an account by name, village, the last 4 digits or a referral code, or by the full number or email a
caller gives (a POST, audited, so personal data never goes in a URL). After calling the caller back
(D16): set a new email, which the citizen confirms through Forgot password, or remove a number another
account registered. Block, unblock and delete, each with a reason. The Super Admin never sees or sets a
password. Every action is audited with the email masked and the last 4 digits of a number at most.

With apps.citizens.services_account, the only writers of CitizenAccount.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts.authz import forget
from apps.accounts.tokens import block_user, unblock_user
from apps.audit.masking import mask_email
from apps.audit.services import record
from grama.exceptions import ApiError
from grama.params import uuid_or_none

from . import emails
from .models import CitizenAccount, VerificationCode
from .services_account import canonical_email, clean_mobile, delete_account, hash_mobile

ACTIVE, BLOCKED, DELETED = "active", "blocked", "deleted"
STATUSES = (ACTIVE, BLOCKED, DELETED)


def status_of(account: CitizenAccount) -> str:
    if account.deleted_at is not None:
        return DELETED
    return ACTIVE if account.user.is_active else BLOCKED


def status_q(value: str) -> Q:
    return {
        ACTIVE: Q(deleted_at__isnull=True, user__is_active=True),
        BLOCKED: Q(deleted_at__isnull=True, user__is_active=False),
        DELETED: Q(deleted_at__isnull=False),
    }[value]


def accounts() -> QuerySet:
    return CitizenAccount.objects.select_related("user", "wallet")


def account_or_404(pk) -> CitizenAccount:
    pk = uuid_or_none(pk)
    account = accounts().filter(pk=pk).first() if pk else None
    if account is None:
        raise ApiError("not_found", _("No such citizen account."), status=status.HTTP_404_NOT_FOUND)
    return account


def search(queryset: QuerySet, q: str) -> QuerySet:
    """A name (English or Telugu), the last 4 digits of the number, or a referral code."""
    q = " ".join((q or "").split())
    if not q:
        return queryset
    found = Q(user__name__icontains=q) | Q(user__name_te__icontains=q) | Q(referral_code=q.upper())
    if len(q) == 4 and q.isdecimal():
        found |= Q(mobile_last4=q)
    return queryset.filter(found)


def allowed_actions(account: CitizenAccount, *, can_adjust: bool) -> list[str]:
    """What the console may offer on this row; the server checks the same rules again."""
    state = status_of(account)
    if state == DELETED:
        return []
    actions = ["set_email"]
    if account.mobile_hash:
        actions.append("release_mobile")
    actions.append("block" if state == ACTIVE else "unblock")
    actions.append("delete")
    if can_adjust:
        actions.append("adjust")
    return actions


def _open(account: CitizenAccount) -> None:
    if account.deleted_at is not None:
        raise ApiError(
            "invalid_transition", _("This account was deleted; it cannot be changed."), status=status.HTTP_409_CONFLICT
        )


def _fresh(account: CitizenAccount) -> CitizenAccount:
    return accounts().get(pk=account.pk)


# --- Finding an account a caller names --------------------------------------------------------------------

def find(*, mobile: str = "", email: str = "", by, via: str) -> list[CitizenAccount]:
    """The account with this full number or email (0 or 1 rows). Audited without the full value."""
    if mobile:
        mobile = clean_mobile(mobile)
        found = list(accounts().filter(mobile_hash=hash_mobile(mobile)))
        looked_for = {"by": "mobile", "mobile": mobile[-4:]}
    else:
        found = list(accounts().filter(email_key=canonical_email(email)))
        looked_for = {"by": "email", "email": mask_email(email)}
    record(
        "citizen.account_found", actor=by, target=found[0].user if found else None,
        target_label=found[0].user.name if found else "", via=via, data={**looked_for, "found": len(found)},
    )
    return found


# --- Help after a call-back (D16, FR-16) --------------------------------------------------------------------

def set_email(account: CitizenAccount, email: str, *, reason: str, by, via: str) -> CitizenAccount:
    """A new email, not yet confirmed: the citizen confirms it by Forgot password, whose code goes there.
    Codes already sent stop working; the old address gets the "email changed" notice."""
    _open(account)
    email = (email or "").strip().lower()
    email_key = canonical_email(email)
    if email_key == account.email_key:
        message = _("This is already the account's email.")
        raise ApiError("validation_error", message, fields={"email": [message]})
    taken = ApiError(
        "email_in_use", _("This email belongs to another citizen account."), status=status.HTTP_409_CONFLICT
    )
    if CitizenAccount.objects.filter(email_key=email_key).exclude(pk=account.pk).exists():
        raise taken
    user, old_email, now = account.user, account.email, timezone.now()
    try:
        with transaction.atomic():
            CitizenAccount.objects.filter(pk=account.pk).update(email=email, email_key=email_key, email_verified_at=None)
            VerificationCode.objects.filter(account=account, consumed_at__isnull=True, expires_at__gt=now).update(
                expires_at=now
            )
            record(
                "citizen.email_set_by_admin", actor=by, target=user, target_label=user.name, via=via,
                data={"from": mask_email(old_email), "to": mask_email(email), "reason": reason},
            )
            if old_email:
                language, name = user.preferred_language, user.name
                transaction.on_commit(
                    lambda: emails.notify("email_changed", old_email, language=language, name=name, new_email=email)
                )
    except IntegrityError:
        raise taken from None  # another account took it meanwhile
    return _fresh(account)


def release_mobile(account: CitizenAccount, *, reason: str, by, via: str) -> CitizenAccount:
    """Remove the number from this account, so the person who owns it now can sign up or add it. The
    account asks for a new number at its next visit (mobile_missing)."""
    _open(account)
    if not account.mobile_hash:
        raise ApiError(
            "invalid_transition", _("This account has no mobile number."), status=status.HTTP_409_CONFLICT
        )
    user, last4 = account.user, account.mobile_last4
    with transaction.atomic():
        CitizenAccount.objects.filter(pk=account.pk).update(mobile_hash=None, mobile_last4="", mobile_verified_at=None)
        record(
            "citizen.mobile_released", actor=by, target=user, target_label=user.name, via=via,
            data={"mobile": last4, "reason": reason},
        )
    return _fresh(account)


# --- Block, unblock, delete ---------------------------------------------------------------------------------

def block(account: CitizenAccount, *, reason: str, by, via: str) -> CitizenAccount:
    """Every session ends at once and the phones are unlinked (FR-6). Already blocked: nothing changes."""
    _open(account)
    user = account.user
    if user.is_active:
        with transaction.atomic():
            block_user(user)
            record("citizen.account_blocked", actor=by, target=user, target_label=user.name, via=via, data={"reason": reason})
        forget(user)
    return _fresh(account)


def unblock(account: CitizenAccount, *, reason: str, by, via: str) -> CitizenAccount:
    _open(account)
    user = account.user
    if not user.is_active:
        with transaction.atomic():
            unblock_user(user)
            record("citizen.account_unblocked", actor=by, target=user, target_label=user.name, via=via, data={"reason": reason})
        forget(user)
    return _fresh(account)


def delete(account: CitizenAccount, *, reason: str, by) -> CitizenAccount:
    """FR-9: for example fraud or a legal request. The same deletion as the citizen's own (BVTGC-127)."""
    _open(account)
    delete_account(account, via="admin", by=by, reason=reason)
    return _fresh(account)
