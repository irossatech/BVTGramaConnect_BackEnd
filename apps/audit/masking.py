"""Masks for personal data that must never reach the audit log in full (CLAUDE.md, "Always").

    mask_phone("9000010001")         -> "******0001"
    mask_email("ravi.k@example.in")  -> "r***@example.in"
    mask_text("whatever was typed")  -> "w***"

Use them for the `data` and `target_label` of audit entries. The audit log is never cleaned and
refuses changes, so whatever goes in stays there.
"""

from __future__ import annotations


def mask_phone(value: str | None) -> str:
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
