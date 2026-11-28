"""Peer roles: a platform-wide role (Super Admin) whose holders manage each other.

Plan of 2026-11-06 (docs/authorization.md, section 4, "More Super Admins"): the first Super Admin
is made on the server with create_super_admin; after that any Super Admin may make another one,
and block, unblock, unlock or reset one, but never act on themselves (authz.can_act_on). Because a
Super Admin can do everything, giving the role has extra rules, all here:

- S1 the actor confirms with their own password (credentials.confirm_own_password);
- S3 never zero: no change may leave the platform without an active holder (409 last_super_admin);
- S4 at most MAX_SUPER_ADMINS active holders, 0 for no limit (409 super_admin_limit).

Which role is a peer role comes from the role matrix (platform_wide), never from a group name.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import QuerySet
from django.utils.translation import gettext as _
from rest_framework import status

from grama.exceptions import ApiError

from .authz import RoleInfo
from .models import RoleAssignment, User


def is_peer_role(role: RoleInfo) -> bool:
    return role.platform_wide


def active_holders(role: RoleInfo) -> QuerySet:
    """Active users with an active assignment of `role`."""
    holding = RoleAssignment.objects.filter(group_id=role.group_id, is_active=True).values("user_id")
    return User.objects.filter(id__in=holding, is_active=True)


def peer_roles_of(user: User, *, active_only: bool = True) -> list[RoleInfo]:
    """The peer roles `user` holds (through any assignment, or only active ones)."""
    from . import authz

    rows = user.role_assignments.all() if not active_only else user.role_assignments.filter(is_active=True)
    found = {}
    for assignment in rows.select_related("group__role_group"):
        role_group = getattr(assignment.group, "role_group", None)
        if role_group is not None and role_group.platform_wide:
            role = authz.role_info(role_group)
            found[role.key] = role
    return list(found.values())


def _limit_reached(limit: int) -> ApiError:
    return ApiError(
        "super_admin_limit",
        _("The limit of Super Admins is reached."),
        status=status.HTTP_409_CONFLICT,
        limit=limit,
    )


def _last_one() -> ApiError:
    return ApiError(
        "last_super_admin",
        _("There must always be at least one active Super Admin."),
        status=status.HTTP_409_CONFLICT,
    )


def check_room_for_one_more(role: RoleInfo, user: User | None = None) -> None:
    """S4: refuse when `role` already has MAX_SUPER_ADMINS active holders (`user` not counted twice)."""
    limit = settings.MAX_SUPER_ADMINS
    if not limit or not is_peer_role(role):
        return
    holders = active_holders(role)
    if user is not None and user.pk:
        holders = holders.exclude(pk=user.pk)
    if holders.count() >= limit:
        raise _limit_reached(limit)


def check_not_last(role: RoleInfo, user: User) -> None:
    """S3: refuse when taking `role` away from `user` (or blocking them) leaves nobody holding it."""
    if not is_peer_role(role):
        return
    holders = active_holders(role)
    if not holders.filter(pk=user.pk).exists():
        return  # they do not count now, so nothing changes
    if not holders.exclude(pk=user.pk).exists():
        raise _last_one()


# --- The same rules inside the change's transaction (audit finding BE-DB-004) ----------------------
#
# The checks above run before the write, so two changes at the same moment could both pass them:
# two Super Admins blocking each other would leave none. Inside the transaction, lock() makes changes
# to a peer role wait for each other, and confirm() checks the rule again after the write; when it no
# longer holds, it raises and the whole change is rolled back. confirm() is the real guard on databases
# without row locks (SQLite, which also runs one write at a time).


def lock(role: RoleInfo) -> None:
    """Inside a transaction: wait for any other change to this peer role to finish (a row lock)."""
    if is_peer_role(role):
        from .models import RoleGroup

        list(RoleGroup.objects.select_for_update().filter(group_id=role.group_id).values_list("pk", flat=True))


def confirm(role: RoleInfo, *, adding: bool) -> None:
    """After the write, in the same transaction: S4 when holders were added, S3 when some were taken away."""
    if not is_peer_role(role):
        return
    holders = active_holders(role).count()
    limit = settings.MAX_SUPER_ADMINS
    if adding and limit and holders > limit:
        raise _limit_reached(limit)
    if not adding and holders == 0:
        raise _last_one()
