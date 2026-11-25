"""The only writer of referrals and wallet entries (BVTGC-126; requirements D3, D10, FR-17 to FR-29).

A referral starts at sign-up (apps.citizens.services_account.finish_signup), inside the same transaction
that makes the account: PENDING, with up to two HELD entries, the referrer's reward and the new citizen's
welcome bonus. Points that the family rules (D10) do not pay are kept as CANCELLED entries with a reason,
so each wallet's history says why. The hourly job confirms or rejects every PENDING referral past its hold;
a Super Admin may decide one earlier (BVTGC-128). Each decision is one conditional UPDATE on the
referral's status, so the first decision wins on any database; the entries and the wallet's cache move in
the same transaction. A daily check compares each cache with the sum of its entries: the entries are the truth.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Min, Q, Sum
from django.utils import timezone

from apps.audit.services import record
from apps.posts.services import runtime

from .models import Referral, Wallet, WalletEntry

#: WalletEntry reasons that mean "never paid": they do not count as a welcome bonus received.
NOT_GRANTED = ("welcome_limit", "no_device", "same_phone")


def setting(name: str) -> int:
    return int(runtime(name))


# --- Entries and the cache -------------------------------------------------------------------------------

def _add_entry(wallet: Wallet, *, points: int, kind: str, status: str, key: str, referral=None, reason_key: str = "",
               reason: str = "", created_by=None) -> WalletEntry | None:
    """One entry; HELD or CONFIRMED points also move the cache. The idempotency key makes a repeat a no-op."""
    try:
        with transaction.atomic():
            entry = WalletEntry.objects.create(
                wallet=wallet, points=points, kind=kind, status=status, referral=referral, reason_key=reason_key,
                reason=reason, created_by=created_by, idempotency_key=key,
                decided_at=timezone.now() if status != WalletEntry.Status.HELD else None,
            )
    except IntegrityError:
        return None  # already written by an earlier try
    if status == WalletEntry.Status.HELD:
        Wallet.objects.filter(pk=wallet.pk).update(held_points=F("held_points") + points)
    elif status == WalletEntry.Status.CONFIRMED:
        Wallet.objects.filter(pk=wallet.pk).update(confirmed_points=F("confirmed_points") + points)
    return entry


def summary(wallet: Wallet) -> dict:
    """The balance part of GET /wallet/ and of the Account: the cache, and the date the first held
    points are decided."""
    wallet.refresh_from_db()
    next_release = (
        WalletEntry.objects.filter(wallet=wallet, status=WalletEntry.Status.HELD, referral__isnull=False)
        .aggregate(first=Min("referral__confirm_after"))["first"]
    )
    return {"confirmed_points": wallet.confirmed_points, "held_points": wallet.held_points, "next_release_at": next_release}


# --- Starting a referral at sign-up ---------------------------------------------------------------------

def _welcome_bonuses_given(*, android_id_hash: str, mobile_hash: str, email_key: str) -> tuple[int, bool]:
    """(welcome bonuses already given on this phone, whether a deleted account with this number or email got one).
    A bonus counts once granted, even if it was later cancelled; deleted accounts included (FR-21)."""
    granted = WalletEntry.objects.filter(kind=WalletEntry.Kind.WELCOME_BONUS).exclude(reason_key__in=NOT_GRANTED)
    on_phone = granted.filter(wallet__account__signup_android_id_hash=android_id_hash).count() if android_id_hash else 0
    before = Q()
    if mobile_hash:
        before |= Q(wallet__account__deleted_mobile_hash=mobile_hash)
    if email_key:
        before |= Q(wallet__account__deleted_email_key=email_key)
    deleted_had_one = bool(before) and granted.filter(before).exists()
    return on_phone, deleted_had_one


def start_referral(referee, referrer, *, now=None) -> Referral:
    """A friend signed up with `referrer`'s code. Call inside the sign-up transaction, after the wallet exists."""
    now = now or timezone.now()
    referral = Referral.objects.create(
        referrer=referrer, referee=referee, pending_at=now, confirm_after=now + timedelta(days=setting("REFERRAL_HOLD_DAYS")),
        created_at=now,
    )
    reward, welcome = setting("REFERRAL_REWARD_POINTS"), setting("WELCOME_BONUS_POINTS")
    same_phone = bool(referee.signup_android_id_hash) and referee.signup_android_id_hash == referrer.signup_android_id_hash

    if reward > 0:
        # D10, FR-20: no referrer reward between accounts that signed up on one phone; kept as a note.
        _add_entry(
            referrer.wallet, points=reward, kind=WalletEntry.Kind.REFERRAL_REWARD,
            status=WalletEntry.Status.CANCELLED if same_phone else WalletEntry.Status.HELD,
            reason_key="same_phone" if same_phone else "", referral=referral, key=f"ref:{referral.id}:referrer",
        )
    if welcome > 0:
        reason_key = ""
        if not referee.signup_android_id_hash:
            reason_key = "no_device"
        else:
            on_phone, deleted_had_one = _welcome_bonuses_given(
                android_id_hash=referee.signup_android_id_hash, mobile_hash=referee.mobile_hash or "",
                email_key=referee.email_key or "",
            )
            if deleted_had_one or on_phone >= setting("WELCOME_BONUSES_PER_PHONE"):
                reason_key = "welcome_limit"
        _add_entry(
            referee.wallet, points=welcome, kind=WalletEntry.Kind.WELCOME_BONUS,
            status=WalletEntry.Status.CANCELLED if reason_key else WalletEntry.Status.HELD,
            reason_key=reason_key, referral=referral, key=f"ref:{referral.id}:welcome",
        )
    record(
        "referral.created", target_label=referral.referrer.referral_code, via="citizen",
        data={"referral": str(referral.id), "same_phone": same_phone},
    )
    return referral


# --- Deciding a referral ----------------------------------------------------------------------------------

def decide(referral: Referral, *, confirm: bool, reason_key: str = "", by=None, comment: str = "", now=None) -> bool:
    """Confirm or reject a PENDING referral. False when it was already decided (the first decision wins)."""
    now = now or timezone.now()
    status = Referral.Status.CONFIRMED if confirm else Referral.Status.REJECTED
    with transaction.atomic():
        moved = Referral.objects.filter(pk=referral.pk, status=Referral.Status.PENDING).update(
            status=status, reason_key="" if confirm else reason_key, comment=comment, decided_at=now, decided_by=by
        )
        if not moved:
            return False
        for entry in WalletEntry.objects.filter(referral=referral, status=WalletEntry.Status.HELD):
            if confirm:
                WalletEntry.objects.filter(pk=entry.pk).update(status=WalletEntry.Status.CONFIRMED, decided_at=now)
                Wallet.objects.filter(pk=entry.wallet_id).update(
                    held_points=F("held_points") - entry.points, confirmed_points=F("confirmed_points") + entry.points
                )
            else:
                WalletEntry.objects.filter(pk=entry.pk).update(
                    status=WalletEntry.Status.CANCELLED, decided_at=now, reason_key=reason_key
                )
                Wallet.objects.filter(pk=entry.wallet_id).update(held_points=F("held_points") - entry.points)
        record(
            "referral.confirmed" if confirm else "referral.rejected", actor=by, target_label=str(referral.id)[:8],
            via="admin" if by is not None else "system", data={"referral": str(referral.id), "reason": reason_key or None},
        )
    return True


def _india_month_start(now):
    local = timezone.localtime(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def reject_reason(referral: Referral, now=None) -> str:
    """Why a referral past its hold is not confirmed, or "" (FR-24, FR-25)."""
    now = now or timezone.now()
    referrer, referee = referral.referrer, referral.referee
    if referrer.deleted_at is not None or referee.deleted_at is not None:
        return "deleted"  # before "blocked": a deleted account is inactive too
    if not referrer.user.is_active or not referee.user.is_active:
        return "blocked"
    days = referee.active_days.filter(
        day__gte=timezone.localtime(referral.pending_at).date(), day__lte=timezone.localtime(referral.confirm_after).date()
    ).count()
    if days < setting("REFERRAL_ACTIVE_DAYS"):
        return "not_active"
    limit = setting("REFERRALS_PER_MONTH")
    if limit:
        confirmed = Referral.objects.filter(
            referrer=referrer, status=Referral.Status.CONFIRMED, decided_at__gte=_india_month_start(now)
        ).count()
        if confirmed >= limit:
            return "monthly_limit"
    return ""


def decide_due_referrals(now=None) -> dict:
    """The hourly job: every PENDING referral past its hold, confirmed or rejected with a reason."""
    now = now or timezone.now()
    counts = {"confirmed": 0, "rejected": 0}
    due = Referral.objects.filter(status=Referral.Status.PENDING, confirm_after__lte=now).select_related(
        "referrer__user", "referee__user"
    ).order_by("confirm_after")
    for referral in due.iterator():
        reason = reject_reason(referral, now)
        if decide(referral, confirm=not reason, reason_key=reason, now=now):
            counts["rejected" if reason else "confirmed"] += 1
    return counts


# --- The daily check ------------------------------------------------------------------------------------------

def check_wallets() -> int:
    """Compare every wallet's cache with the sum of its entries; audit each mismatch (wallet.mismatch)."""
    mismatches = 0
    sums = {
        (row["wallet"], row["status"]): row["total"]
        for row in WalletEntry.objects.filter(status__in=[WalletEntry.Status.HELD, WalletEntry.Status.CONFIRMED])
        .values("wallet", "status").annotate(total=Sum("points"))
    }
    for wallet in Wallet.objects.all().iterator():
        held = sums.get((wallet.pk, WalletEntry.Status.HELD), 0)
        confirmed = sums.get((wallet.pk, WalletEntry.Status.CONFIRMED), 0)
        if (held, confirmed) != (wallet.held_points, wallet.confirmed_points):
            mismatches += 1
            record(
                "wallet.mismatch", target_label=str(wallet.pk)[:8], via="system",
                data={"cache": [wallet.held_points, wallet.confirmed_points], "entries": [held, confirmed]},
            )
    return mismatches
