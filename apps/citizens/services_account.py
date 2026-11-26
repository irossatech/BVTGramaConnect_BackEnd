"""Citizens' wallet accounts: the only code that writes them (BVTGC-123 on; contract section 21).

Sign-up (BVTGC-123): the form is checked, a PendingSignup keeps it with the password as a hash, and a
code goes to the email. The account exists only once the code is typed in: then, in one transaction,
the Django user (a generated username nobody sees), the Citizen role for the chosen village, the
CitizenAccount and its Wallet are created. The unique email_key and mobile_hash are the real guard when
two sign-ups race for one email or number.

Login and the forgotten password (BVTGC-124): the email or the mobile number finds the account, and
the officials' login limits apply as they are (accounts.credentials.authenticate_with_limits), keyed on
the account's username, so the email and the number count as one account.

The citizen's own account (BVTGC-125): reading it records the day for the referral active rule; the
village, the language, the password, the email (with a code to the new address) and the mobile number
change here. A wrong password on these counts with the officials' wrong tries on "Change password", so
guessing ends every session of the account.

Deletion (BVTGC-127): in the app with the password, on the website with an emailed code, or by the
Super Admin with a reason (BVTGC-128), all through delete_account(). The account closes at once and its
personal details go; confirmed points and referrals stay without them (owner's decision of 2026-11-25,
Q8, the one exception to "data is kept permanently").

The full mobile number is never stored: login and uniqueness use its keyed hash (PHONE_HASH_KEY).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts import credentials, lockout
from apps.accounts.models import RoleAssignment, RoleGroup, User, mobile_validator
from apps.accounts.tokens import issue_tokens, revoke_all_sessions
from apps.audit.masking import mask_email
from apps.audit.services import record
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from apps.posts.models import CITIZEN_ROLE
from apps.posts.services import runtime
from grama.exceptions import ApiError

from . import codes, emails
from .models import CitizenAccount, CitizenActiveDay, PendingSignup, VerificationCode
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
    """The 10 digits, with spaces, dashes and a leading +91 dropped, or a leading 0 when 11 digits are left
    (BVTGC-153: "09876543210" is the number the app's and the website's checks accept); validation_error
    otherwise. Used by sign-up, login, the forgotten password, the website deletion and the Super Admin's Find."""
    value = "".join(ch for ch in (mobile or "") if ch not in " -")
    if value.startswith("+91"):
        value = value[3:]
    elif len(value) == 11 and value.startswith("0"):
        value = value[1:]
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


def _taken_elsewhere(*, mobile_hash: str = "", email_key: str = "") -> None:
    """For a logged-in citizen's change: the same 409 codes as at sign-up, with words that point to the help
    line (D16), since logging in or resetting does not help here."""
    if mobile_hash and CitizenAccount.objects.filter(mobile_hash=mobile_hash).exists():
        raise ApiError(
            "mobile_in_use",
            _("This mobile number belongs to another account. If it is yours, call the help line."),
            status=status.HTTP_409_CONFLICT,
        )
    if email_key and CitizenAccount.objects.filter(email_key=email_key).exists():
        raise ApiError(
            "email_in_use",
            _("This email belongs to another account. If it is yours, call the help line."),
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
            # The sign-up day is a day of using the app for the referral's active rule (BVTGC-154).
            CitizenActiveDay.objects.create(account=account, day=timezone.localdate(now))
            from apps.wallet.models import Wallet
            from apps.wallet.services import start_referral

            Wallet.objects.create(account=account)
            if referrer is not None:
                # The referral starts now, PENDING, with its held points (BVTGC-126).
                start_referral(account, referrer, now=now)
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


# --- Login (BVTGC-124) ------------------------------------------------------------------------------------

#: The login key of an email or number with no account: "~" is not allowed in usernames, so it never
#: matches a real user, and its failures still count per login and address.
UNKNOWN_LOGIN_PREFIX = "~citizen:"


def citizen_invalid_credentials() -> ApiError:
    return ApiError("invalid_credentials", _("Wrong email, mobile number or password."))


def account_for_login(login: str) -> CitizenAccount | None:
    """The account an email (contains @) or a 10-digit mobile number names, or None."""
    value = (login or "").strip()
    accounts = CitizenAccount.objects.select_related("user")
    if "@" in value:
        return accounts.filter(email_key=canonical_email(value)).first()
    try:
        mobile = clean_mobile(value)
    except ApiError:
        return None
    return accounts.filter(mobile_hash=hash_mobile(mobile)).first()


def _login_key(login: str, account: CitizenAccount | None) -> str:
    if account is not None:
        return account.user.username
    return UNKNOWN_LOGIN_PREFIX + hashlib.sha256((login or "").strip().lower().encode()).hexdigest()[:24]


def log_in(request, login: str, password: str, *, via: str = "citizen") -> CitizenAccount:
    """The account for this email or number and password (contract section 21). `request` is Django's
    HttpRequest. A wrong password, an unknown login and a deleted account get the same answer; a blocked
    account 403 account_blocked. Lockouts as for officials: 429 locked_out with retry_after_s."""
    require_wallet()
    value = (login or "").strip()
    account = account_for_login(value) if value else None
    try:
        user = credentials.authenticate_with_limits(request, _login_key(value, account), password or "", via=via)
    except ApiError as exc:
        if exc.error_code == "invalid_credentials":
            raise citizen_invalid_credentials() from None
        raise
    if account is None or user.pk != account.user_id or account.deleted_at is not None:
        raise citizen_invalid_credentials()
    if not user.is_active:
        raise credentials.refuse(user, "account_blocked", _("This account is blocked."), via)
    record_active_day(account)  # a login is a day of using the app (BVTGC-154)
    return account


# --- Forgotten password (BVTGC-124) --------------------------------------------------------------------

def _open_account(login: str) -> CitizenAccount | None:
    account = account_for_login(login)
    if account is None or account.deleted_at is not None or not account.user.is_active or not account.email:
        return None
    return account


def forgot_password(login: str, *, via: str = "citizen") -> dict:
    """Email a RESET_PASSWORD code to the account's address. The same answer for every login (FR-14):
    an unknown, deleted or blocked account gets no code."""
    require_wallet()
    account = _open_account(login)
    if account is None:
        return {"expires_in": int(codes.CODE_TTL.total_seconds()), "resend_after": int(codes.RESEND_AFTER.total_seconds())}
    sent = codes.send_code(
        purpose=VerificationCode.Purpose.RESET_PASSWORD,
        to_email=account.email,
        email_key=account.email_key or canonical_email(account.email),
        language=account.user.preferred_language,
        name=account.user.name,
        account=account,
        via=via,
    )
    return sent.as_dict()


def reset_password(login: str, code: str, new_password: str, *, via: str = "citizen") -> CitizenAccount:
    """The right code sets the new password, confirms the email if needed, ends every session and every
    lock of the account, and emails a notice. An unknown login answers like an expired code."""
    require_wallet()
    account = _open_account(login)
    if account is None:
        raise codes.expired_error()
    row = codes.check_code(purpose=VerificationCode.Purpose.RESET_PASSWORD, code=code, account=account)
    user = account.user
    check_citizen_password(
        new_password, name=user.name, email=account.email, mobile_hash=account.mobile_hash or "", field="new_password"
    )
    now = timezone.now()
    with transaction.atomic():
        if not codes.consume(row):
            raise codes.expired_error()
        user.set_password(new_password)
        user.password_changed_at = now
        user.save(update_fields=["password", "password_changed_at"])
        CitizenAccount.objects.filter(pk=account.pk, email_verified_at__isnull=True).update(email_verified_at=now)
        record("citizen.password_reset", actor=user, target=user, target_label=user.name, via=via)
    revoke_all_sessions(user)
    lockout.unlock(user)
    emails.notify("password_changed", account.email, language=user.preferred_language, name=user.name)
    record_active_day(account)  # the reset signs the app in: a day of using it (BVTGC-154)
    account.refresh_from_db()
    account.user.refresh_from_db()
    return account


# --- The citizen's own account (BVTGC-125) --------------------------------------------------------------

def record_active_day(account: CitizenAccount) -> None:
    """One row per India date the app is used with the account (the referral active rule): opening the app
    (GET citizen/account/), and since BVTGC-154 a login and a password reset; finish_signup writes the
    sign-up day itself. The first of a day also sets last_active_at. Cheap: get_or_create on the unique pair."""
    _row, created = CitizenActiveDay.objects.get_or_create(account=account, day=timezone.localdate())
    if created:
        CitizenAccount.objects.filter(pk=account.pk).update(last_active_at=timezone.now())


def _check_own_password(account: CitizenAccount, password: str) -> None:
    """Raise unless `password` is the account's. A wrong one is audited and counted with the wrong tries on
    "Change password"; too many end every session (401 session_revoked)."""
    user = account.user
    if user.check_password(password or ""):
        return
    error = credentials.wrong_current_password(user, via="citizen")
    if error.error_code == "invalid_credentials":
        raise ApiError("invalid_credentials", _("Wrong password."))
    raise error


def update_account(account: CitizenAccount, *, village_id=None, language=None, via: str = "citizen") -> CitizenAccount:
    """PATCH /citizen/account/: a new village moves the Citizen role with it (no pilot check: an existing
    account keeps working wherever its village moves); a language for emails and pushes."""
    user = account.user
    with transaction.atomic():
        if village_id is not None and int(village_id) != account.village_id:
            new_village = _active_village(village_id)
            old_village = account.village_id
            CitizenAccount.objects.filter(pk=account.pk).update(village_id=new_village)
            citizen_group = RoleGroup.objects.get(key=CITIZEN_ROLE).group_id
            RoleAssignment.objects.filter(user=user, group_id=citizen_group, is_active=True).update(
                area_level=AreaLevel.VILLAGE, area_id=new_village
            )
            record(
                "citizen.village_changed", actor=user, target=user, target_label=user.name, via=via,
                data={"from": old_village, "to": new_village},
            )
        if language in ("te", "en") and language != user.preferred_language:
            User.objects.filter(pk=user.pk).update(preferred_language=language)
    account.refresh_from_db()
    account.user.refresh_from_db()
    return account


def change_password(account: CitizenAccount, current: str, new: str, *, via: str = "citizen") -> dict:
    """The citizen's own password change. Every other session ends; this phone gets new tokens."""
    _check_own_password(account, current)
    user = account.user
    check_citizen_password(new, name=user.name, email=account.email, mobile_hash=account.mobile_hash or "", field="new_password")
    if new == current:
        message = _("Choose a password different from the current one.")
        raise ApiError("validation_error", message, fields={"new_password": [message]})
    with transaction.atomic():
        user.set_password(new)
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "password_changed_at"])
        revoke_all_sessions(user)  # also unlinks the phones; the app links this one again at once
        record("citizen.password_changed", actor=user, target=user, target_label=user.name, via=via)
    emails.notify("password_changed", account.email, language=user.preferred_language, name=user.name)
    user.refresh_from_db()
    return issue_tokens(user)


def start_email_change(account: CitizenAccount, email: str, password: str, *, via: str = "citizen") -> codes.CodeSent:
    """A CHANGE_EMAIL code to the new address, after the password. The email changes only with that code."""
    _check_own_password(account, password)
    email = (email or "").strip().lower()
    email_key = canonical_email(email)
    if email_key == account.email_key:
        message = _("This is already your email.")
        raise ApiError("validation_error", message, fields={"email": [message]})
    _taken_elsewhere(email_key=email_key)
    user = account.user
    return codes.send_code(
        purpose=VerificationCode.Purpose.CHANGE_EMAIL,
        to_email=email,
        email_key=email_key,
        language=user.preferred_language,
        name=user.name,
        account=account,
        via=via,
    )


def finish_email_change(account: CitizenAccount, code: str, *, via: str = "citizen") -> CitizenAccount:
    """The right code moves the account to the new address; the old address gets a notice."""
    row = codes.check_code(purpose=VerificationCode.Purpose.CHANGE_EMAIL, code=code, account=account)
    old_email, new_email, new_key = account.email, row.new_email, row.new_email_key
    user = account.user
    try:
        with transaction.atomic():
            if not codes.consume(row):
                raise codes.expired_error()
            CitizenAccount.objects.filter(pk=account.pk).update(
                email=new_email, email_key=new_key, email_verified_at=timezone.now()
            )
            VerificationCode.objects.filter(pk=row.pk).update(new_email="", new_email_key="")
            record(
                "citizen.email_changed", actor=user, target=user, target_label=user.name, via=via,
                data={"from": mask_email(old_email), "to": mask_email(new_email)},
            )
    except IntegrityError:
        _taken_elsewhere(email_key=new_key)  # another account took it meanwhile: 409
        raise
    if old_email:
        emails.notify("email_changed", old_email, language=user.preferred_language, name=user.name, new_email=new_email)
    account.refresh_from_db()
    return account


def change_mobile(account: CitizenAccount, mobile: str, password: str, *, via: str = "citizen") -> CitizenAccount:
    """A new mobile number, after the password; not confirmed by a code until the later stage (D17), so it
    clears mobile_verified_at. Also how an account whose number the Super Admin removed adds a new one."""
    _check_own_password(account, password)
    mobile = clean_mobile(mobile)
    mobile_hash = hash_mobile(mobile)
    if mobile_hash == account.mobile_hash:
        message = _("This is already your mobile number.")
        raise ApiError("validation_error", message, fields={"mobile": [message]})
    _taken_elsewhere(mobile_hash=mobile_hash)
    user = account.user
    old_last4 = account.mobile_last4
    try:
        with transaction.atomic():
            CitizenAccount.objects.filter(pk=account.pk).update(
                mobile_hash=mobile_hash, mobile_last4=mobile[-4:], mobile_verified_at=None
            )
            record(
                "citizen.mobile_changed", actor=user, target=user, target_label=user.name, via=via,
                data={"from": old_last4, "to": mobile[-4:]},
            )
    except IntegrityError:
        _taken_elsewhere(mobile_hash=mobile_hash)
        raise
    account.refresh_from_db()
    return account


# --- Deletion (BVTGC-127) ---------------------------------------------------------------------------------

DELETED_NAME = "Deleted citizen"


def delete_account(account: CitizenAccount, *, via: str, by=None, reason: str = "") -> None:
    """Close the account at once and remove its personal details (requirements section 9, FR-9).

    One transaction: every session ends and the phones are unlinked; the name, username, password, email
    and number go; the email key and number hash move to deleted_* for a time (requirements Q9), so a new
    sign-up gets no second welcome bonus; its email codes go; the Citizen role ends. Its referrals still
    waiting are not confirmed (reason "deleted", FR-24: the outcome is already certain), which cancels the
    points on hold on both sides, and anything else on hold in its wallet is cancelled too. Confirmed
    entries and decided referrals stay, for the audit trail. The "account deleted" notice goes to the
    address read before it is cleared, once the deletion has committed. `via` is app, website or admin.
    A second call does nothing.
    """
    from apps.accounts.authz import forget
    from apps.accounts.signals import sync_user_groups
    from apps.wallet import services as wallet
    from apps.wallet.models import Referral, Wallet, WalletEntry

    user = account.user
    address, name, language = account.email, user.name, user.preferred_language
    now = timezone.now()
    with transaction.atomic():
        closed = CitizenAccount.objects.filter(pk=account.pk, deleted_at__isnull=True).update(
            deleted_at=now,
            deleted_mobile_hash=account.mobile_hash or "",
            deleted_email_key=account.email_key or "",
            mobile_hash=None,
            mobile_last4="",
            email="",
            email_key=None,
        )
        if not closed:
            return  # already deleted, perhaps by a second request at the same moment
        User.objects.filter(pk=user.pk).update(
            is_active=False,
            name=DELETED_NAME,
            name_te="",
            email=None,
            username=f"deleted.{uuid.uuid4().hex}",
            password=make_password(None),  # unusable
        )
        VerificationCode.objects.filter(account=account).delete()  # they hold the address
        RoleAssignment.objects.filter(user=user, is_active=True).update(is_active=False)
        sync_user_groups(user)  # update() skips the signals that keep the Django groups in step
        waiting = Referral.objects.filter(status=Referral.Status.PENDING).filter(
            Q(referrer=account) | Q(referee=account)
        )
        for referral in waiting:
            wallet.decide(referral, confirm=False, reason_key="deleted", now=now)
        own = Wallet.objects.filter(account=account).first()
        if own is not None:
            held = WalletEntry.objects.filter(wallet=own, status=WalletEntry.Status.HELD)
            points = sum(held.values_list("points", flat=True))
            held.update(status=WalletEntry.Status.CANCELLED, reason_key="deleted", decided_at=now)
            if points:
                Wallet.objects.filter(pk=own.pk).update(held_points=F("held_points") - points)
        record(
            "citizen.account_deleted", actor=by or None, target=user, target_label=DELETED_NAME, via=via,
            data={"via": via, **({"reason": reason} if reason else {})},
        )
        revoke_all_sessions(user)  # also unlinks the phones from push (BVTGC-94)
        if address:
            transaction.on_commit(
                lambda: emails.notify("account_deleted", address, language=language, name=name)
            )
    user.refresh_from_db()
    account.refresh_from_db()
    forget(user)


def delete_own_account(account: CitizenAccount, password: str, *, via: str = "app") -> None:
    """POST /citizen/account/delete/: the citizen, in the app, after the password."""
    _check_own_password(account, password)
    delete_account(account, via=via)


def send_delete_code(login: str, *, via: str = "website") -> dict:
    """POST /citizen/auth/delete/code/: a DELETE code to the account's email; the same answer for every login."""
    require_wallet()
    account = _open_account(login)
    if account is None:
        return {"expires_in": int(codes.CODE_TTL.total_seconds()), "resend_after": int(codes.RESEND_AFTER.total_seconds())}
    return codes.send_code(
        purpose=VerificationCode.Purpose.DELETE,
        to_email=account.email,
        email_key=account.email_key or canonical_email(account.email),
        language=account.user.preferred_language,
        name=account.user.name,
        account=account,
        via=via,
    ).as_dict()


def delete_with_code(login: str, code: str, *, via: str = "website") -> None:
    """POST /citizen/auth/delete/: the website's deletion with a DELETE code (D12). Only someone who can open
    the account's email can delete it; an unknown login reads as an expired code."""
    require_wallet()
    account = _open_account(login)
    if account is None:
        raise codes.expired_error()
    row = codes.check_code(purpose=VerificationCode.Purpose.DELETE, code=code, account=account)
    if not codes.consume(row):
        raise codes.expired_error()
    delete_account(account, via=via)


# --- Housekeeping (BVTGC-127; grama/housekeeping.py runs it daily) ------------------------------------------

#: A used or dead code row is needed only for the daily limits: a week is plenty.
CODE_KEEP_DAYS = 7
#: The active-day rows are a counter for the referral hold, not records.
ACTIVE_DAY_KEEP_DAYS = 60


def housekeeping(now=None) -> dict:
    """Forget what the wallet accounts no longer need: unfinished sign-ups past 24 hours (with their codes),
    codes older than a week, active days older than 60 days, and deleted accounts' fingerprints after
    CITIZEN_DELETED_KEEP_DAYS (requirements Q9). Returns the rows removed or cleared per kind."""
    now = now or timezone.now()
    pending = PendingSignup.objects.filter(expires_at__lt=now)
    pending_count = pending.count()
    pending.delete()
    old_codes = VerificationCode.objects.filter(created_at__lt=now - timedelta(days=CODE_KEEP_DAYS))
    code_count = old_codes.count()
    old_codes.delete()
    old_days = CitizenActiveDay.objects.filter(day__lt=timezone.localdate(now) - timedelta(days=ACTIVE_DAY_KEEP_DAYS))
    day_count = old_days.count()
    old_days.delete()
    keep = settings.CITIZEN_DELETED_KEEP_DAYS
    cleared = 0
    if keep > 0:
        cleared = CitizenAccount.objects.filter(deleted_at__lt=now - timedelta(days=keep)).exclude(
            deleted_mobile_hash="", deleted_email_key=""
        ).update(deleted_mobile_hash="", deleted_email_key="")
    return {
        "citizen_pending_signups": pending_count,
        "citizen_codes": code_count,
        "citizen_active_days": day_count,
        "citizen_deleted_fingerprints": cleared,
    }
