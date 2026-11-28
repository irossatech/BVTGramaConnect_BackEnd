"""Password composition rule, run with Django's other validators (settings.AUTH_PASSWORD_VALIDATORS).

Every password an official chooses, and every temporary password an admin
types or the server generates, needs at least one capital letter, one small
letter, one number and one special character (anything that is not a letter,
a number or a space, such as @ # $ % & * ! ?). Existing passwords keep
working; the rule applies the next time a password is set.
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

SPECIAL_EXAMPLES = "@ # $ % & * ! ?"


def _is_special(ch: str) -> bool:
    return not ch.isalnum() and not ch.isspace()


class PasswordCompositionValidator:
    """One capital letter, one small letter, one number, one special character (each switchable)."""

    def __init__(self, upper=True, lower=True, digit=True, special=True):
        self.upper, self.lower, self.digit, self.special = upper, lower, digit, special

    def _missing(self, password: str) -> list[tuple[str, str]]:
        missing = []
        if self.upper and not any(ch.isupper() for ch in password):
            missing.append(("password_no_upper", _("a capital letter (A-Z)")))
        if self.lower and not any(ch.islower() for ch in password):
            missing.append(("password_no_lower", _("a small letter (a-z)")))
        if self.digit and not any(ch.isdigit() for ch in password):
            missing.append(("password_no_digit", _("a number (0-9)")))
        if self.special and not any(_is_special(ch) for ch in password):
            missing.append(("password_no_special", _("a special character such as %(examples)s") % {"examples": SPECIAL_EXAMPLES}))
        return missing

    def validate(self, password, user=None):
        missing = self._missing(password or "")
        if missing:
            # One message naming everything missing, so the person fixes it in one try.
            raise ValidationError(
                _("The password needs at least %(parts)s.") % {"parts": ", ".join(text for _code, text in missing)},
                code=missing[0][0],
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least one capital letter, one small letter, one number "
            "and one special character such as %(examples)s."
        ) % {"examples": SPECIAL_EXAMPLES}
