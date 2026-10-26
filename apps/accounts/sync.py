"""Make the groups, their RoleGroup rows and their permissions match role_matrix.ROLE_GROUPS.

Used by:
- the data migration 0005 (with the migration's historical models),
- `python manage.py sync_role_permissions`,
- a post_migrate handler, so every `migrate` (and so every deploy) applies it.

Safe to run any number of times: it changes only what differs and returns
one line per change.
"""

from __future__ import annotations

from django.apps import apps as global_apps
from django.conf import settings
from django.contrib.auth.management import create_permissions
from django.db import DEFAULT_DB_ALIAS, transaction

from .role_matrix import ROLE_GROUPS


class UnknownPermission(Exception):
    """The dict names a permission that no model declares."""


def ensure_permissions(apps=global_apps, using: str = DEFAULT_DB_ALIAS) -> None:
    """Create the Permission rows (and content types) of every model, as migrate does at the end."""
    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, verbosity=0, apps=apps, using=using)


def _app_not_migrated_yet(apps, perm: str) -> bool:
    """True inside a migration whose historical state has no models of the permission's app yet.

    Data migration 0005 runs this sync with the dict as it is today, so an app
    added later (contacts) is not migrated at that point. Its permissions are
    applied by the post_migrate sync at the end of the same `migrate`.
    """
    if apps is global_apps:
        return False
    try:
        apps.get_app_config(perm.split(".", 1)[0])
    except LookupError:
        return True
    return False


def wanted_permissions(spec: dict) -> set[str]:
    """The group's permissions, without those whose setting is off."""
    conditional = spec.get("requires_setting", {})
    return {
        perm
        for perm in spec["permissions"]
        if perm not in conditional or getattr(settings, conditional[perm], False)
    }


def _row_values(spec: dict) -> dict:
    return {
        "name_te": spec["name_te"],
        "level": spec["level"],
        "rank": spec["rank"],
        "platform_wide": spec["platform_wide"],
        "manage_permission": spec["manage_permission"] or "",
        "requires_setting": spec.get("requires_setting", {}),
    }


def sync_role_permissions(apps=global_apps, using: str = DEFAULT_DB_ALIAS) -> list[str]:
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    RoleGroup = apps.get_model("accounts", "RoleGroup")

    ensure_permissions(apps, using)
    from .signals import quiet_group_audit

    changes: list[str] = []
    with quiet_group_audit(), transaction.atomic(using=using):
        by_name = {
            f"{app_label}.{codename}": pk
            for pk, app_label, codename in Permission.objects.using(using).values_list(
                "pk", "content_type__app_label", "codename"
            )
        }
        names = {pk: name for name, pk in by_name.items()}
        named = {perm for spec in ROLE_GROUPS.values() for perm in spec["permissions"]}
        named |= {spec["manage_permission"] for spec in ROLE_GROUPS.values() if spec["manage_permission"]}
        missing = {perm for perm in named if perm not in by_name}
        later = {perm for perm in missing if _app_not_migrated_yet(apps, perm)}
        unknown = sorted(missing - later)
        if unknown:
            raise UnknownPermission("No model declares: " + ", ".join(unknown))

        existing = {row.key: row for row in RoleGroup.objects.using(using).select_related("group")}
        original_rank = {key: row.rank for key, row in existing.items()}
        # Ranks are unique: when any rank moves, park every row first so the
        # new order never collides halfway through.
        if any(row.rank != ROLE_GROUPS[key]["rank"] for key, row in existing.items() if key in ROLE_GROUPS):
            for offset, row in enumerate(existing.values()):
                row.rank = 1000 + offset
                RoleGroup.objects.using(using).filter(pk=row.pk).update(rank=row.rank)

        for key, spec in ROLE_GROUPS.items():
            row = existing.get(key)
            is_new = row is None
            if is_new:
                group, created = Group.objects.using(using).get_or_create(name=spec["name"])
                if created:
                    changes.append(f"{key}: created group {spec['name']!r}")
                row = RoleGroup(group=group, key=key)
                changes.append(f"{key}: level {spec['level']}, rank {spec['rank']}")
            group = row.group
            if group.name != spec["name"]:
                changes.append(f"{key}: renamed group {group.name!r} to {spec['name']!r}")
                group.name = spec["name"]
                group.save(using=using)
            dirty = is_new
            for field, value in _row_values(spec).items():
                if getattr(row, field, None) != value:
                    before = original_rank.get(key) if field == "rank" else getattr(row, field, None)
                    if not is_new and before != value:
                        changes.append(f"{key}: {field} {before!r} -> {value!r}")
                    setattr(row, field, value)
                    dirty = True
            if dirty:
                row.save(using=using, force_insert=is_new)

            wanted = {by_name[perm] for perm in wanted_permissions(spec) - later}
            current = set(group.permissions.values_list("pk", flat=True))
            for pk in sorted(wanted - current, key=names.get):
                changes.append(f"{key}: + {names[pk]}")
            for pk in sorted(current - wanted, key=lambda value: names.get(value, str(value))):
                changes.append(f"{key}: - {names.get(pk, pk)}")
            if wanted != current:
                group.permissions.set(wanted)
    return changes


def sync_after_migrate(sender, apps=global_apps, using=DEFAULT_DB_ALIAS, **kwargs) -> None:
    """post_migrate handler: re-apply the dict whenever the schema is up to date."""
    try:
        role_assignment = apps.get_model("accounts", "RoleAssignment")
        apps.get_model("accounts", "RoleGroup")
    except LookupError:
        return  # migrated back to a state from before groups existed
    if "group" not in {field.name for field in role_assignment._meta.get_fields()}:
        return
    sync_role_permissions(apps, using=using)
