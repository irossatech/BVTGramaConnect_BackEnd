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
    """Refuses a password that contains the citizen's 10-digit mobile number."""

    def validate(self, password, user=None):
        mobile = getattr(user, "citizen_mobile", "") or ""
        if mobile and mobile in (password or ""):
            raise ValidationError(_("The password cannot contain your mobile number."), code="password_is_mobile")

    def get_help_text(self):
        return _("The password cannot contain your mobile number.")


def check_citizen_password(password: str, *, name: str = "", email: str = "", mobile: str = "", field: str = "password") -> None:
    """Raise a validation_error ApiError on `field` unless the citizen rule accepts the password."""
    probe = User(name=name or "", email=email or None)
    probe.citizen_mobile = mobile
    try:
        validate_password(password, probe, password_validators=get_password_validators(settings.CITIZEN_PASSWORD_VALIDATORS))
    except ValidationError as exc:
        raise ApiError("validation_error", exc.messages[0], fields={field: list(exc.messages)}) from None
