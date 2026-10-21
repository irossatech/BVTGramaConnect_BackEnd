"""Roles become Django groups (step 2 of 3: data).

1. Create the permissions, the five groups, their RoleGroup rows and their
   permissions from apps/accounts/role_matrix.py (the same code as
   `manage.py sync_role_permissions`; safe to run again).
2. Point every RoleAssignment at the group of its old role string.
3. Copy User.status into is_active (BLOCKED -> False).
4. Put each user in the groups of their assignments.

Prints the assignment counts per role before and after; they must match, or
the migration stops and changes nothing.
"""

import sys
from collections import Counter

from django.db import migrations

OLD_TO_KEY = {
    # The old role strings are the new group keys.
    "SUPER_ADMIN": "SUPER_ADMIN",
    "DISTRICT_ADMIN": "DISTRICT_ADMIN",
    "MANDAL_ADMIN": "MANDAL_ADMIN",
    "VILLAGE_ADMIN": "VILLAGE_ADMIN",
    "PANCHAYAT_MEMBER": "PANCHAYAT_MEMBER",
}


def _report(title, counts):
    total = sum(counts.values())
    parts = ", ".join(f"{key} {counts[key]}" for key in sorted(counts)) or "none"
    sys.stdout.write(f"\n  {title}: {total} ({parts})")


def forwards(apps, schema_editor):
    from apps.accounts.sync import sync_role_permissions

    db = schema_editor.connection.alias
    RoleAssignment = apps.get_model("accounts", "RoleAssignment")
    RoleGroup = apps.get_model("accounts", "RoleGroup")
    User = apps.get_model("accounts", "User")

    before = Counter(RoleAssignment.objects.using(db).values_list("role", flat=True))
    blocked = User.objects.using(db).filter(status="BLOCKED").count()

    sync_role_permissions(apps, using=db)

    group_by_key = dict(RoleGroup.objects.using(db).values_list("key", "group_id"))
    unknown = sorted(set(before) - set(OLD_TO_KEY))
    if unknown:
        raise RuntimeError(f"Role assignments with unknown roles: {unknown}. Nothing was changed.")
    for old, key in OLD_TO_KEY.items():
        RoleAssignment.objects.using(db).filter(role=old).update(group_id=group_by_key[key])

    User.objects.using(db).filter(status="BLOCKED").update(is_active=False)
    User.objects.using(db).exclude(status="BLOCKED").update(is_active=True)

    membership = User._meta.get_field("groups").remote_field.through
    wanted = set(RoleAssignment.objects.using(db).values_list("user_id", "group_id"))
    present = set(membership.objects.using(db).values_list("user_id", "group_id"))
    membership.objects.using(db).bulk_create(
        [membership(user_id=user_id, group_id=group_id) for user_id, group_id in sorted(wanted - present, key=str)]
    )

    key_by_group = {group_id: key for key, group_id in group_by_key.items()}
    after = Counter(key_by_group[gid] for gid in RoleAssignment.objects.using(db).values_list("group_id", flat=True))
    if Counter({OLD_TO_KEY[role]: n for role, n in before.items()}) != after:
        raise RuntimeError(f"Counts differ: before {dict(before)}, after {dict(after)}. Nothing was changed.")
    if before or blocked:
        _report("Role assignments before (role strings)", before)
        _report("Role assignments after (groups)", after)
        sys.stdout.write(f"\n  Blocked users: {blocked} before, {User.objects.using(db).filter(is_active=False).count()} after\n")


def backwards(apps, schema_editor):
    db = schema_editor.connection.alias
    RoleAssignment = apps.get_model("accounts", "RoleAssignment")
    RoleGroup = apps.get_model("accounts", "RoleGroup")
    User = apps.get_model("accounts", "User")
    key_to_old = {key: old for old, key in OLD_TO_KEY.items()}
    for key, group_id in RoleGroup.objects.using(db).values_list("key", "group_id"):
        if key in key_to_old:
            RoleAssignment.objects.using(db).filter(group_id=group_id).update(role=key_to_old[key])
    User.objects.using(db).filter(is_active=False).update(status="BLOCKED")
    User.objects.using(db).filter(is_active=True).update(status="ACTIVE")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_roles_as_groups"),
        ("contenttypes", "0002_remove_content_type_name"),
        # The migrations that declare the permissions the role matrix hands out.
        ("audit", "0002_permissions"),
        ("geography", "0002_permissions"),
        ("notifications", "0005_permissions"),
        ("posts", "0004_permissions"),
        ("web_admin", "0001_console_permissions"),
        ("web_public", "0003_permissions"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
