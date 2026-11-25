"""Citizens' wallet accounts: the only code that writes them (BVTGC-123 on; contract section 21).

Sign-up (BVTGC-123): the form is checked, a PendingSignup keeps it with the password as a hash, and a
code goes to the email. The account exists only once the code is typed in: then, in one transaction,
the Django user (a generated username nobody sees), the Citizen role for the chosen village, the
CitizenAccount and its Wallet are created. The unique email_key and mobile_hash are the real guard when
two sign-ups race for one email or number.

The full mobile number is never stored: login and uniqueness use its keyed hash (PHONE_HASH_KEY).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts.models import RoleAssignment, User, mobile_validator
from apps.audit.services import record
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from apps.posts.models import CITIZEN_ROLE
from apps.posts.services import runtime
from grama.exceptions import ApiError

from . import codes
from .models import CitizenAccount, PendingSignup, VerificationCode
from .passwords import check_citizen_password
from .services import _active_village, _keyed_hash, _validation, hash_android_id

SIGNUP_TTL = timedelta(hours=24)
SIGNUP_SALT = "citizens.signup"
#: Referral codes: capital letters and digits without look-alikes (no 0/O, 1/I/L), 8 long (FR-17).
REFERRAL_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERRAL_LENGTH = 8
GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


# --- The switch and the pilot (runtime settings; the console edits them from BVTGC-129) ------------

def wallet_enabled() -> bool:
    return bool(runtime("WALLET_ENABLED"))


def require_wallet() -> None:
    """Every wallet endpoint answers 404 while the wallet is off (contract section 21)."""
    if not wallet_enabled():
        raise Http404


def pilot_mandals() -> list[int]:
    value = runtime("WALLET_PILOT_MANDALS") or []
    if isinstance(value, str):
        value = [part for part in value.replace(" ", "").split(",") if part]
    return [int(item) for item in value]


# --- Emails, numbers and codes ----------------------------------------------------------------------

def canonical_email(email: str) -> str:
    """The login form of an email: lower case; for Gmail, without dots or a "+" part before the @, so one
    person's Gmail variants count as one email (FR-11)."""
    local, _at, domain = (email or "").strip().lower().rpartition("@")
    if domain in GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def clean_mobile(mobile: str) -> str:
    """The 10 digits, with spaces, dashes and a leading +91 dropped; validation_error otherwise."""
    value = "".join(ch for ch in (mobile or "") if ch not in " -")
    if value.startswith("+91"):
        value = value[3:]
    try:
        mobile_validator(value)
    except ValidationError as exc:
        raise _validation("mobile", exc.messages[0]) from None
    return value


def hash_mobile(mobile: str) -> str:
    return _keyed_hash(b"citizen-mobile", mobile)


def new_referral_code() -> str:
    while True:
        code = "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(REFERRAL_LENGTH))
        if not CitizenAccount.objects.filter(referral_code=code).exists():
            return code


def _in_use(*, mobile_hash: str, email_key: str) -> None:
    if CitizenAccount.objects.filter(mobile_hash=mobile_hash).exists():
        raise ApiError(
            "mobile_in_use",
            _("This mobile number already has an account. Log in or reset your password."),
            status=status.HTTP_409_CONFLICT,
        )
    if CitizenAccount.objects.filter(email_key=email_key).exists():
        raise ApiError(
            "email_in_use",
            _("This email already has an account. Log in or reset your password."),
            status=status.HTTP_409_CONFLICT,
        )


def check_referral(code: str, *, mobile_hash: str, email_key: str) -> CitizenAccount | None:
    """The referrer for a code typed at sign-up (FR-18, FR-19), or None when no code was typed.
    BVTGC-126 creates the referral itself when the account is made."""
    code = (code or "").strip().upper()
    if not code:
        return None
    referrer = CitizenAccount.objects.select_related("user").filter(referral_code=code).first()
    if referrer is None:
        raise ApiError("referral_code_invalid", _("No such referral code."))
    if referrer.mobile_hash == mobile_hash or referrer.email_key == email_key:
        raise ApiError("referral_code_own", _("You cannot use your own referral code."))
    if referrer.deleted_at is not None or not referrer.user.is_active:
        raise ApiError("referral_not_open", _("This referral code can no longer be used."))
    return referrer


# --- Sign-up tokens -----------------------------------------------------------------------------------

def _token_invalid() -> ApiError:
    return ApiError(
        "signup_token_invalid",
        _("This sign-up has expired or was already finished. Start again."),
        status=status.HTTP_401_UNAUTHORIZED,
    )


def signup_token(pending: PendingSignup) -> str:
    """Signed, carrying only the PendingSignup id; it lives as long as the row (24 hours)."""
    return signing.dumps({"p": str(pending.id)}, salt=SIGNUP_SALT)


def pending_from_token(token: str) -> PendingSignup:
    try:
        data = signing.loads(token or "", salt=SIGNUP_SALT, max_age=SIGNUP_TTL.total_seconds())
    except signing.BadSignature:  # SignatureExpired is a BadSignature
        raise _token_invalid() from None
    pending = PendingSignup.objects.filter(id=data.get("p"), expires_at__gt=timezone.now()).first()
    if pending is None:
        raise _token_invalid()
    return pending


# --- Sign-up --------------------------------------------------------------------------------------------

def start_signup(
    *,
    name: str,
    mobile: str,
    email: str,
    password: str,
    village_id: int,
    age_confirmed: bool,
    consent_version: str,
    language: str = "te",
    referral_code: str = "",
    android_id: str = "",
    via: str = "app",
) -> tuple[str, codes.CodeSent]:
    """Check the form in the contract's order and email the code. Returns (signup_token, what was sent)."""
    require_wallet()
    village_id = _active_village(village_id)
    mandals = pilot_mandals()
    if mandals and not Village.objects.filter(id=village_id, mandal_id__in=mandals).exists():
        raise ApiError("wallet_not_in_area", _("The wallet is not open in your area yet."), status=status.HTTP_403_FORBIDDEN)
    if not age_confirmed:
        raise ApiError("age_required", _("Confirm that you are 18 or older."))
    if consent_version != settings.CITIZEN_CONSENT_VERSION:
        raise _validation("consent_version", _("Agree to the latest consent notice."))
    name = " ".join((name or "").split())
    mobile = clean_mobile(mobile)
    email = (email or "").strip().lower()
    email_key = canonical_email(email)
    check_citizen_password(password, name=name, email=email, mobile=mobile)
    mobile_hash = hash_mobile(mobile)
    _in_use(mobile_hash=mobile_hash, email_key=email_key)
    referral_code = (referral_code or "").strip().upper()
    check_referral(referral_code, mobile_hash=mobile_hash, email_key=email_key)
    now = timezone.now()
    android_id_hash = hash_android_id(android_id)
    pending = PendingSignup.objects.create(
        name=name,
        village_id=village_id,
        language=language if language in ("te", "en") else "te",
        mobile_hash=mobile_hash,
        mobile_last4=mobile[-4:],
        email=email,
        email_key=email_key,
        password=make_password(password),
        age_confirmed_at=now,
        consent_version=consent_version,
        referral_code=referral_code,
        android_id_hash=android_id_hash,
        created_at=now,
        expires_at=now + SIGNUP_TTL,
    )
    try:
        sent = _send(pending, via=via)
    except ApiError:
        pending.delete()  # nothing to resend without a token: the citizen taps Create account again
        raise
    return signup_token(pending), sent


def _send(pending: PendingSignup, *, via: str) -> codes.CodeSent:
    return codes.send_code(
        purpose=VerificationCode.Purpose.SIGNUP,
        to_email=pending.email,
        email_key=pending.email_key,
        language=pending.language,
        name=pending.name,
        pending_signup=pending,
        android_id_hash=pending.android_id_hash,
        via=via,
    )


def resend_signup(token: str, *, via: str = "app") -> codes.CodeSent:
    require_wallet()
    return _send(pending_from_token(token), via=via)


def finish_signup(token: str, code: str, *, via: str = "app") -> CitizenAccount:
    """The right code makes the account: user, Citizen role, account, wallet. Deletes the PendingSignup."""
    require_wallet()
    pending = pending_from_token(token)
    row = codes.check_code(purpose=VerificationCode.Purpose.SIGNUP, code=code, pending_signup=pending)
    referrer = check_referral(pending.referral_code, mobile_hash=pending.mobile_hash, email_key=pending.email_key)
    now = timezone.now()
    try:
        with transaction.atomic():
            if not codes.consume(row):
                raise _token_invalid()  # another request finished this sign-up first
            _in_use(mobile_hash=pending.mobile_hash, email_key=pending.email_key)
            user = User(
                username=f"citizen.{uuid.uuid4().hex}",
                name=pending.name,
                preferred_language=pending.language,
                password=pending.password,
            )
            user.save()
            RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, pending.village_id))
            account = CitizenAccount.objects.create(
                user=user,
                village_id=pending.village_id,
                age_confirmed_at=pending.age_confirmed_at,
                consent_version=pending.consent_version,
                consent_at=now,
                email=pending.email,
                email_key=pending.email_key,
                email_verified_at=now,
                mobile_hash=pending.mobile_hash,
                mobile_last4=pending.mobile_last4,
                referral_code=new_referral_code(),
                signup_android_id_hash=pending.android_id_hash,
                last_active_at=now,
                created_at=now,
            )
            from apps.wallet.models import Wallet

            Wallet.objects.create(account=account)
            record(
                "citizen.account_created",
                target=user,
                target_label=user.name,
                via=via,
                data={"village_id": pending.village_id, "referral_code_used": referrer is not None},
            )
            pending.delete()
    except IntegrityError:
        # Another sign-up took the email or the number between the check and the insert.
        _in_use(mobile_hash=pending.mobile_hash, email_key=pending.email_key)
        raise
    return account
