"""Migration audit.0003 masks personal data in entries written before the fix (BE-SEC-001, BE-SEC-007)."""

import importlib

from django.apps import apps as django_apps

from apps.audit.models import AuditLog

migration = importlib.import_module("apps.audit.migrations.0003_mask_personal_data")


def test_old_entries_are_masked_and_new_ones_left_alone(db):
    old_edit = AuditLog.objects.create(
        action="user.updated", type="users", target_label="Ravi",
        data={"email": "ravi.kumar@example.in", "mobile": "9876543210", "name_te": "రవి"},
    )
    new_edit = AuditLog.objects.create(
        action="user.updated", type="users", target_label="Ravi",
        data={"changed": {"mobile": {"from": "******0001", "to": "******3210"}}},
    )
    super_admin_command = AuditLog.objects.create(
        action="user.updated", type="users", data={"role": "SUPER_ADMIN", "by": "create_super_admin"},
    )
    typed_password = AuditLog.objects.create(
        action="auth.login_failed", type="logins", target_label="grama-secret-1",
        data={"username": "grama-secret-1", "locked": False},
    )
    real_account = AuditLog.objects.create(
        action="auth.login_failed", type="logins", target_type="accounts.user", target_id="abc",
        target_label="va_kaza", data={"username": "va_kaza", "locked": False},
    )

    migration.forwards(django_apps, None)
    migration.forwards(django_apps, None)  # running it twice changes nothing more

    def data(row):
        return AuditLog.objects.get(pk=row.pk)

    assert data(old_edit).data == {"email": "r***@example.in", "mobile": "******3210", "name_te": "రవి"}
    assert data(new_edit).data == new_edit.data
    assert data(super_admin_command).data == super_admin_command.data
    assert data(typed_password).data == {"username": "g***", "locked": False}
    assert data(typed_password).target_label == "g***"
    assert data(real_account).data["username"] == "va_kaza"
