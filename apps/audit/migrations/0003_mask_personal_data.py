"""Mask personal data in audit entries written before the masking fix (audit BE_01, BE-SEC-001 and
BE-SEC-007; owner's decision of 2026-11-09).

- "user.updated" from users/{id}/ stored the edited fields as sent: a full email and mobile.
- "auth.login_failed" and "auth.locked_out" for a name that is not an account stored it as typed:
  possibly a password typed into the username box, or an email address.

The audit log otherwise never changes. This runs once, on the rows of those three actions only, and
uses the historical model (queryset update), so AuditLog.save()'s refusal does not apply. The masks
are copied here, not imported, so that later changes to apps/audit/masking.py cannot change what
this migration did. Reversing it does nothing: the full values are gone on purpose.
"""

from django.db import migrations


def mask_phone(value) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return "*" * max(len(digits) - 4, 0) + digits[-4:] if digits else ""


def mask_text(value) -> str:
    value = str(value or "").strip()
    return f"{value[:1]}***" if value else ""


def mask_email(value) -> str:
    value = str(value or "").strip()
    local, at, domain = value.partition("@")
    return f"{local[:1]}***@{domain}" if at else mask_text(value)


MASKS = {"email": mask_email, "mobile": mask_phone, "phone": mask_phone}


def masked_contact_fields(data: dict) -> dict:
    """The old user.updated shape {"email": ..., "mobile": ..., "name": ...}, contact details masked."""
    out = dict(data)
    for name, mask in MASKS.items():
        value = out.get(name)
        if value and "*" not in str(value):
            out[name] = mask(value)
    return out


def forwards(apps, schema_editor):
    AuditLog = apps.get_model("audit", "AuditLog")

    for row in AuditLog.objects.filter(action="user.updated").only("id", "data").iterator():
        data = row.data if isinstance(row.data, dict) else {}
        if "changed" in data:
            continue  # written after the fix: already masked
        masked = masked_contact_fields(data)
        if masked != data:
            AuditLog.objects.filter(pk=row.pk).update(data=masked)

    failures = AuditLog.objects.filter(action__in=["auth.login_failed", "auth.locked_out"], target_id="")
    for row in failures.only("id", "data", "target_label").iterator():
        data = dict(row.data) if isinstance(row.data, dict) else {}
        typed = data.get("username") or row.target_label
        if not typed or str(typed).endswith("***"):
            continue
        data["username"] = mask_text(typed)
        AuditLog.objects.filter(pk=row.pk).update(data=data, target_label=mask_text(row.target_label or typed))


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_permissions"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
