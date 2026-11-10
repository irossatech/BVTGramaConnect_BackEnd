"""Masks for personal data that must never reach the audit log in full (CLAUDE.md, "Always").

    mask_phone("9000010001")         -> "******0001"
    mask_email("ravi.k@example.in")  -> "r***@example.in"
    mask_text("whatever was typed")  -> "w***"

Use them for the `data` and `target_label` of audit entries. The audit log is never cleaned and
refuses changes, so whatever goes in stays there.
"""

from __future__ import annotations

import re

#: A phone number that is already masked ("******0001"): masking it again must not lose the stars.
_MASKED_PHONE = re.compile(r"^\*+\d{0,4}$")
#: Text that is only a phone number: digits with spaces, dashes, brackets or a leading "+".
_PHONE_ONLY = re.compile(r"^\+?[\d\s()\-]{10,20}$")
_EMAIL_ONLY = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def mask_phone(value: str | None) -> str:
    if _MASKED_PHONE.match(str(value or "")):
        return str(value)
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return ""
    return "*" * max(len(digits) - 4, 0) + digits[-4:]


def mask_email(value: str | None) -> str:
    value = str(value or "").strip()
    local, at, domain = value.partition("@")
    if not at:
        return mask_text(value)
    return f"{local[:1]}***@{domain}"


def mask_text(value: str | None) -> str:
    """Free text that may hold anything (a password typed into the username box): one character."""
    value = str(value or "").strip()
    return f"{value[:1]}***" if value else ""


#: Model fields that hold contact details, and how each is masked in an audit entry.
CONTACT_FIELD_MASKS = {
    "email": mask_email,
    "mobile": mask_phone,
    "phone": mask_phone,
}


#: Words that mark a key as a secret (password, new_password, push_token, client_secret, otp ...).
#: Its value never reaches the audit log ...
SECRET_WORDS = {"password", "passwd", "token", "secret", "otp", "apikey"}
#: ... except these notes, which say how a password was set and are not the password.
PASSWORD_NOTES = {"generated", "set by admin", "none"}
REMOVED = "[removed]"


def _words(key: str) -> list[str]:
    return [word for word in re.split(r"[_\-\s]+", key.lower()) if word]


def _contact_mask(key: str):
    words = _words(key)
    if not words:
        return None
    last = words[-1].rstrip("s")  # "phones" too
    return CONTACT_FIELD_MASKS.get(last)


def _is_secret(key: str) -> bool:
    words = _words(key)
    return any(word in SECRET_WORDS for word in words) or "api_key" in key.lower()


def _mask_leaves(value, mask):
    """Every text or number inside `value`, masked; dicts and lists keep their shape."""
    if isinstance(value, dict):
        return {key: _mask_leaves(item, mask) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_leaves(item, mask) for item in value]
    if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
        return mask(str(value))
    return value


def _drop_secrets(value):
    if isinstance(value, dict):
        return {key: _drop_secrets(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_drop_secrets(item) for item in value]
    if value in (None, "") or isinstance(value, bool) or value in PASSWORD_NOTES:
        return value
    return REMOVED


def scrub(data):
    """The safety net inside audit.services.record(): `data` with personal data masked and secrets removed.

    Works at any depth, so {"changed": {"email": {"from": ..., "to": ...}}} is covered too.
    Contact keys (email, mobile, phone, and names ending in _email, _mobile, _phone or a plural)
    are masked; keys naming a secret (password, token, secret, otp, api key) keep only
    PASSWORD_NOTES. The masks are idempotent, so a caller that already masked loses nothing.
    """
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            name = str(key)
            mask = _contact_mask(name)
            if mask is not None:
                out[key] = _mask_leaves(value, mask)
            elif _is_secret(name):
                out[key] = _drop_secrets(value)
            else:
                out[key] = scrub(value)
        return out
    if isinstance(data, (list, tuple)):
        return [scrub(item) for item in data]
    return data


def scrub_label(label: str | None) -> str:
    """A target label that is only an email address or a phone number, masked; anything else as is."""
    text = str(label or "").strip()
    if _EMAIL_ONLY.match(text):
        return mask_email(text)
    if _PHONE_ONLY.match(text) and sum(ch.isdigit() for ch in text) >= 10:
        return mask_phone(text)
    return label or ""


def changes(before: dict, obj) -> dict:
    """{field: {"from": old, "to": new}} for the fields of `before` that changed on `obj`.

    Contact details are masked; everything else is kept as is.
    """
    out = {}
    for name, old in before.items():
        new = getattr(obj, name)
        if old == new:
            continue
        mask = CONTACT_FIELD_MASKS.get(name)
        out[name] = {
            "from": mask(old) if mask else old,
            "to": mask(new) if mask else new,
        }
    return out
