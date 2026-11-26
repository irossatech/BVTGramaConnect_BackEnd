"""Email codes for citizens' wallet accounts (BVTGC-122; requirements FR-12; contract section 21, "Rules").

A code is 6 digits, valid 10 minutes, with 5 wrong tries. A new one can be sent after 60 seconds; at most
5 a day per account or email address, and 10 a day per phone (the hashed Android ID). Limits per IP
address come back with BVTGC-105. These are fixed rules, not settings.

The code itself is never stored or logged: its row keeps an HMAC of the code with the row's id and
SECRET_KEY. Only the newest live code of an owner and purpose counts. A wrong try is one conditional
UPDATE (attempts < 5), so parallel tries cannot go past the limit on any database. The limits are
counted before sending, so two requests at the same instant may both pass; the email service's daily
cap (BVTGC-143) is the hard guard against cost.

Used by the sign-up, password reset, email change and deletion services (BVTGC-123 to BVTGC-127).

Decoys (BVTGC-152): a forgotten password and the website's deletion answer the same for every login.
For a login with no open account (unknown, blocked or deleted) decoy_send() and decoy_check() keep a
stand-in "code" in the shared "throttle" cache (a database table on servers, so every worker sees it),
with the same 60-second wait, daily limit and 5 tries, and answer exactly as for a real code that is
typed wrong. Nothing is emailed or written to VerificationCode, and the cache key is a keyed hash of the
login, never the email or the number.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import NoReturn

from django.conf import settings
from django.core.cache import caches
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.audit.masking import mask_email
from apps.audit.services import record
from grama.exceptions import ApiError

from . import emails
from .models import VerificationCode

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
PER_ADDRESS_PER_DAY = 5
PER_PHONE_PER_DAY = 10
_SIX_DIGITS = re.compile(r"^\d{6}$")
DAY = timedelta(days=1)
DECOY_PREFIX = "citizen-code-decoy:"


@dataclass(frozen=True)
class CodeSent:
    """What the API answers after sending a code (contract section 21, CodeSent)."""

    expires_in: int
    resend_after: int
    email_hint: str

    def as_dict(self, *, with_hint: bool = False) -> dict:
        data = {"expires_in": self.expires_in, "resend_after": self.resend_after}
        if with_hint:
            data["email_hint"] = self.email_hint
        return data


def _code_hash(row_id, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{row_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _owner(account=None, pending_signup=None) -> Q:
    if (account is None) == (pending_signup is None):
        raise ValueError("a code belongs to exactly one owner: an account or a pending sign-up")
    return Q(account=account) if account is not None else Q(pending_signup=pending_signup)


def _cooldown_error(wait: int) -> ApiError:
    return ApiError(
        "code_cooldown",
        _("Wait %(s)d seconds before asking for a new code.") % {"s": wait},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after_s=wait,
    )


def _limit_error() -> ApiError:
    return ApiError("code_limit", _("Too many codes today. Try again tomorrow."), status=status.HTTP_429_TOO_MANY_REQUESTS)


def _wrong_error(left: int) -> ApiError:
    left = max(0, left)
    return ApiError("code_wrong", _("Wrong code. Tries left: %(n)d.") % {"n": left}, attempts_left=left)


def _check_limits(*, email_key: str, owner: Q, account, android_id_hash: str, now) -> None:
    recent = (
        VerificationCode.objects.filter(owner | Q(email_key=email_key), created_at__gt=now - RESEND_AFTER)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if recent is not None:
        raise _cooldown_error(max(1, math.ceil((recent + RESEND_AFTER - now).total_seconds())))
    day = now - DAY
    too_many = VerificationCode.objects.filter(email_key=email_key, created_at__gt=day).count() >= PER_ADDRESS_PER_DAY
    if account is not None:
        too_many = too_many or VerificationCode.objects.filter(account=account, created_at__gt=day).count() >= PER_ADDRESS_PER_DAY
    if android_id_hash:
        too_many = too_many or (
            VerificationCode.objects.filter(android_id_hash=android_id_hash, created_at__gt=day).count() >= PER_PHONE_PER_DAY
        )
    if too_many:
        raise _limit_error()


def send_code(
    *,
    purpose: str,
    to_email: str,
    email_key: str,
    language: str,
    name: str = "",
    account=None,
    pending_signup=None,
    android_id_hash: str = "",
    via: str = "app",
) -> CodeSent:
    """Check the limits, make a code and email it to `to_email` (for CHANGE_EMAIL, the new address).

    Raises ApiError code_cooldown or code_limit (429), or email_unavailable (503), in which case no
    code is kept, so the failed try costs the citizen nothing.
    """
    now = timezone.now()
    owner = _owner(account, pending_signup)
    _check_limits(email_key=email_key, owner=owner, account=account, android_id_hash=android_id_hash, now=now)
    code = f"{secrets.randbelow(10**6):06d}"
    row = VerificationCode.objects.create(
        purpose=purpose,
        account=account,
        pending_signup=pending_signup,
        email_key=email_key,
        new_email=to_email if purpose == VerificationCode.Purpose.CHANGE_EMAIL else "",
        new_email_key=email_key if purpose == VerificationCode.Purpose.CHANGE_EMAIL else "",
        android_id_hash=android_id_hash,
        code_hash="",
        expires_at=now + CODE_TTL,
        created_at=now,
    )
    VerificationCode.objects.filter(pk=row.pk).update(code_hash=_code_hash(row.pk, code))
    minutes = int(CODE_TTL.total_seconds() // 60)
    try:
        message_id = emails.send_code(to_email, purpose=purpose, code=code, language=language, name=name, minutes=minutes)
    except emails.EmailUnavailable:
        row.delete()
        raise ApiError(
            "email_unavailable",
            _("We could not send the email just now. Try again in a few minutes."),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None
    VerificationCode.objects.filter(pk=row.pk).update(provider_message_id=message_id[:255])
    hint = mask_email(to_email)
    record(
        "citizen.code_sent",
        target=account.user if account is not None else None,
        target_label=hint,
        via=via,
        data={"purpose": purpose, "channel": VerificationCode.Channel.EMAIL, "email": hint},
    )
    return CodeSent(expires_in=int(CODE_TTL.total_seconds()), resend_after=int(RESEND_AFTER.total_seconds()), email_hint=hint)


def expired_error() -> ApiError:
    """400 code_expired: no live code. Also the answer for an unknown login, so it tells nothing."""
    return ApiError("code_expired", _("This code has expired or was already used. Ask for a new one."))


def check_code(*, purpose: str, code: str, account=None, pending_signup=None) -> VerificationCode:
    """The newest live code of this owner and purpose, if `code` matches it; marks it verified.

    Raises code_wrong (400, with attempts_left) for a wrong code, and code_expired when there is no live
    code: none sent, older than 10 minutes, used, or out of tries. The caller consumes it with consume().
    """
    now = timezone.now()
    row = (
        VerificationCode.objects.filter(
            _owner(account, pending_signup), purpose=purpose, consumed_at__isnull=True, expires_at__gt=now
        )
        .order_by("-created_at")
        .first()
    )
    if row is None or row.attempts >= MAX_ATTEMPTS:
        raise expired_error()
    typed = str(code or "").strip()
    if not (_SIX_DIGITS.match(typed) and hmac.compare_digest(row.code_hash, _code_hash(row.pk, typed))):
        if not VerificationCode.objects.filter(pk=row.pk, attempts__lt=MAX_ATTEMPTS).update(attempts=F("attempts") + 1):
            raise expired_error()
        raise _wrong_error(MAX_ATTEMPTS - VerificationCode.objects.values_list("attempts", flat=True).get(pk=row.pk))
    VerificationCode.objects.filter(pk=row.pk, verified_at__isnull=True).update(verified_at=now)
    row.refresh_from_db()
    return row


def consume(row: VerificationCode) -> bool:
    """Use a checked code once. False when another request used it first (one conditional UPDATE)."""
    return VerificationCode.objects.filter(pk=row.pk, consumed_at__isnull=True).update(consumed_at=timezone.now()) == 1


# --- Decoys for logins with no open account (BVTGC-152) ---------------------------------------------------

def _decoy_load(login_key: str) -> dict:
    return caches["throttle"].get(DECOY_PREFIX + login_key) or {"sent": [], "codes": {}}


def _decoy_save(login_key: str, state: dict) -> None:
    caches["throttle"].set(DECOY_PREFIX + login_key, state, timeout=int(DAY.total_seconds()) + 60)


def decoy_send(*, purpose: str, login_key: str) -> CodeSent:
    """What send_code() answers, for a login with no open account: the same 60-second wait (shared by the
    forgotten password and the deletion, as a real account's codes are), the same daily limit, and a
    stand-in code of `purpose` that no typed code matches. Nothing is emailed. `login_key` is a keyed hash
    of the login (services_account.decoy_key)."""
    now = timezone.now().timestamp()
    state = _decoy_load(login_key)
    sent = [at for at in state["sent"] if at > now - DAY.total_seconds()]
    if sent and now - sent[-1] < RESEND_AFTER.total_seconds():
        raise _cooldown_error(max(1, math.ceil(sent[-1] + RESEND_AFTER.total_seconds() - now)))
    if len(sent) >= PER_ADDRESS_PER_DAY:
        raise _limit_error()
    sent.append(now)
    state["sent"] = sent
    state["codes"][purpose] = {"expires": now + CODE_TTL.total_seconds(), "attempts": 0}
    _decoy_save(login_key, state)
    return CodeSent(expires_in=int(CODE_TTL.total_seconds()), resend_after=int(RESEND_AFTER.total_seconds()), email_hint="")


def decoy_check(*, purpose: str, login_key: str) -> NoReturn:
    """What check_code() answers to a wrong code, for a login with no open account: code_wrong with the tries
    left while the stand-in code is live, code_expired when there is none, it is older than 10 minutes, or
    its 5 tries are used. Always raises: no code is right."""
    now = timezone.now().timestamp()
    state = _decoy_load(login_key)
    row = state["codes"].get(purpose)
    if row is None or row["expires"] <= now or row["attempts"] >= MAX_ATTEMPTS:
        raise expired_error()
    row["attempts"] += 1
    _decoy_save(login_key, state)
    raise _wrong_error(MAX_ATTEMPTS - row["attempts"])
