"""Keep Django group membership in step with role assignments, and audit group permission edits.

- A user belongs to a group exactly while they hold at least one active
  assignment of it. Adding an assignment adds them; removing or deactivating
  the last one removes them.
- Any change to a group's permissions made outside sync_role_permissions
  (Django admin, a shell) is written to the audit log. The sync command
  writes one entry of its own instead.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from django.contrib.auth.models import Group
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import RoleAssignment

#: (user, via) of whoever is editing groups right now; set by Django admin (accounts/admin.py).
group_editor: ContextVar[tuple | None] = ContextVar("group_editor", default=None)
_quiet: ContextVar[bool] = ContextVar("group_audit_quiet", default=False)


@contextmanager
def quiet_group_audit():
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


@contextmanager
def editing_groups(user, via: str = "admin"):
    token = group_editor.set((user, via))
    try:
        yield
    finally:
        group_editor.reset(token)


def sync_user_groups(user) -> None:
    """Set the user's groups to the groups of their active assignments."""
    wanted = set(
        RoleAssignment.objects.filter(user_id=user.pk, is_active=True).values_list("group_id", flat=True)
    )
    current = set(user.groups.values_list("pk", flat=True))
    if wanted - current:
        user.groups.add(*(wanted - current))
    if current - wanted:
        user.groups.remove(*(current - wanted))


@receiver(post_save, sender=RoleAssignment, dispatch_uid="role_assignment_saved")
def assignment_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        sync_user_groups(instance.user)


@receiver(post_delete, sender=RoleAssignment, dispatch_uid="role_assignment_deleted")
def assignment_deleted(sender, instance, **kwargs):
    from .models import User

    user = User.objects.filter(pk=instance.user_id).first()
    if user is not None:
        sync_user_groups(user)


@receiver(m2m_changed, sender=Group.permissions.through, dispatch_uid="group_permissions_changed")
def group_permissions_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if _quiet.get() or reverse or action not in ("post_add", "post_remove", "pre_clear"):
        return
    from django.contrib.auth.models import Permission

    from apps.audit.services import record

    if action == "pre_clear":
        pk_set = set(instance.permissions.values_list("pk", flat=True))
    names = sorted(
        f"{app}.{codename}"
        for app, codename in Permission.objects.filter(pk__in=pk_set or []).values_list(
            "content_type__app_label", "codename"
        )
    )
    if not names:
        return
    editor, via = group_editor.get() or (None, "system")
    record(
        "setting.group_permissions_changed",
        actor=editor,
        target=instance,
        target_label=instance.name,
        via=via,
        data={"change": "removed" if action != "post_add" else "added", "permissions": names},
    )
