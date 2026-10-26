"""Phone numbers for important numbers: clean them up and tell which kind they are.

    normalise_phone("+91 90000 10001")              -> ("9000010001", "MOBILE")
    normalise_phone("0863-2234567")                  -> ("08632234567", "LANDLINE")
    normalise_phone("108", allow_short=True)         -> ("108", "SHORT_CODE")

The strict grammar is also what keeps USSD codes such as *#06# out of the
app's dialler (tel: links).
"""

from __future__ import annotations

import re

from .models import PhoneKind

SEPARATORS = re.compile(r"[\s\-()]")
PATTERNS = [
    (PhoneKind.MOBILE, re.compile(r"[6-9]\d{9}")),
    (PhoneKind.LANDLINE, re.compile(r"0\d{9,10}")),
    (PhoneKind.TOLL_FREE, re.compile(r"1800\d{6,7}")),
]
SHORT_CODE = re.compile(r"\d{3,6}")


class InvalidPhone(ValueError):
    """The text is not a phone number the platform accepts. str() is the reason, for the form."""


def normalise_phone(raw: str, *, allow_short: bool = False) -> tuple[str, str]:
    """(digits, kind) for a phone number typed by an admin, or raise InvalidPhone."""
    value = SEPARATORS.sub("", raw or "")
    if not value:
        raise InvalidPhone("Enter a phone number.")
    if value.startswith("+"):
        if not (value.startswith("+91") and len(value) == 13):
            raise InvalidPhone("Only Indian numbers: +91 and 10 digits.")
        value = value[3:]
    elif len(value) == 12 and value.startswith("91"):
        value = value[2:]
    if not value.isdigit():
        raise InvalidPhone("Use digits only.")
    kind = phone_kind(value, allow_short=allow_short)
    if kind is None:
        if allow_short is False and SHORT_CODE.fullmatch(value):
            raise InvalidPhone("Short codes such as 108 are for government helplines only.")
        raise InvalidPhone("Enter a 10-digit mobile, a landline with its STD code, a 1800 number or a short code.")
    return value, kind


def phone_kind(digits: str, *, allow_short: bool = False) -> str | None:
    """The kind of an already-normalised number, or None when it is not one."""
    for kind, pattern in PATTERNS:
        if pattern.fullmatch(digits):
            return kind
    if allow_short and SHORT_CODE.fullmatch(digits):
        return PhoneKind.SHORT_CODE
    return None


def mask(digits: str) -> str:
    """For audit entries and logs: never a full number. "9000010001" -> "******0001"."""
    return "*" * max(len(digits) - 4, 0) + digits[-4:]
