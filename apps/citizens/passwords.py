"""The password rule of citizens' wallet accounts (requirements D15, FR-13; BVTGC-123).

At least 8 characters of any kind (numbers only are fine), not a common password, not too like the
name or the email, and not containing the mobile number. settings.CITIZEN_PASSWORD_VALIDATORS holds
the list. It is checked only here, never through accounts.credentials.check_new_password, which applies
the officials' rule; the global AUTH_PASSWORD_VALIDATORS stay the officials' alone.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.password_validation import get_password_validators, validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.accounts.models import User
from grama.exceptions import ApiError


class NotTheMobileNumberValidator:
    """Refuses a password that contains the citizen's 10-digit mobile number: the typed number at sign-up,
    or, later, any 10 digits in a row whose keyed hash is the account's (the number itself is not stored)."""

    def validate(self, password, user=None):
        password = password or ""
        mobile = getattr(user, "citizen_mobile", "") or ""
        mobile_hash = getattr(user, "citizen_mobile_hash", "") or ""
        found = bool(mobile) and mobile in password
        if not found and mobile_hash:
            from .services_account import hash_mobile

            found = any(hash_mobile(run) == mobile_hash for run in _ten_digit_runs(password))
        if found:
            raise ValidationError(_("The password cannot contain your mobile number."), code="password_is_mobile")

    def get_help_text(self):
        return _("The password cannot contain your mobile number.")


def _ten_digit_runs(password: str):
    for start in range(len(password) - 9):
        run = password[start:start + 10]
        if run.isascii() and run.isdigit():
            yield run


def check_citizen_password(
    password: str, *, name: str = "", email: str = "", mobile: str = "", mobile_hash: str = "", field: str = "password"
) -> None:
    """Raise a validation_error ApiError on `field` unless the citizen rule accepts the password.
    Give `mobile` at sign-up (typed) and `mobile_hash` afterwards (the account's)."""
    probe = User(name=name or "", email=email or None)
    probe.citizen_mobile = mobile
    probe.citizen_mobile_hash = mobile_hash
    try:
        validate_password(password, probe, password_validators=get_password_validators(settings.CITIZEN_PASSWORD_VALIDATORS))
    except ValidationError as exc:
        raise ApiError("validation_error", exc.messages[0], fields={field: list(exc.messages)}) from None
