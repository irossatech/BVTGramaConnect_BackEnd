"""Who may act where.

Every permission check in the API comes down to two questions (CLAUDE.md):
1. Does one of the actor's role assignments allow this action?
2. Does the target sit inside that assignment's area?

user_areas() answers the first half, is_within() the second. A Super Admin's
assignment counts as the whole platform, whatever area it names.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q, QuerySet

from apps.geography.areas import AreaRef, area_is_within, within_q

from .roles import MANAGEABLE_ROLES, POSTING_ROLES, REVIEW_ROLES, ROLE_LEVEL, Role, sort_roles

_CACHE_ATTR = "_role_areas_cache"


@dataclass(frozen=True)
class RoleArea:
    role: str
    area: AreaRef

    @property
    def is_global(self) -> bool:
        return self.role == Role.SUPER_ADMIN


def user_areas(user, roles=None) -> list[RoleArea]:
    """The user's role assignments as (role, area) pairs, optionally only some roles.

    Read from the database once per user object. API requests load the user
    fresh, so a removed assignment stops counting on the next request.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    cached = getattr(user, _CACHE_ATTR, None)
    if cached is None:
        cached = [
            RoleArea(role, AreaRef(level, area_id))
            for role, level, area_id in user.role_assignments.values_list("role", "area_level", "area_id")
        ]
        setattr(user, _CACHE_ATTR, cached)
    if roles is None:
        return list(cached)
    roles = set(roles)
    return [item for item in cached if item.role in roles]


def forget_areas(user) -> None:
    """Drop the cached assignments, after changing them on this user object."""
    if hasattr(user, _CACHE_ATTR):
        delattr(user, _CACHE_ATTR)


def is_within(area: AreaRef, container: RoleArea | AreaRef) -> bool:
    """True when `area` is the container's area or lies below it.

    Pass a RoleArea to honour the Super Admin's platform-wide reach.
    """
    if isinstance(container, RoleArea):
        if container.is_global:
            return True
        container = container.area
    return area_is_within(area, container)


def has_role(user, *roles) -> bool:
    return bool(user_areas(user, roles))


def is_super_admin(user) -> bool:
    return has_role(user, Role.SUPER_ADMIN)


def manageable_roles(user) -> list[str]:
    roles: set[str] = set()
    for item in user_areas(user):
        roles |= MANAGEABLE_ROLES[item.role]
    return sort_roles(roles)


def can_manage(user, role: str, area: AreaRef) -> bool:
    """May `user` create, block or reassign someone holding `role` in `area`?"""
    return any(
        role in MANAGEABLE_ROLES[item.role] and is_within(area, item)
        for item in user_areas(user)
    )


def manageable_assignment_q(user) -> Q:
    """ORM filter for RoleAssignment rows this user may manage."""
    q = Q(pk__in=[])
    for item in user_areas(user):
        roles = MANAGEABLE_ROLES[item.role]
        if not roles:
            continue
        clause = Q(role__in=roles)
        if not item.is_global:
            clause &= within_q(item.area)
        q |= clause
    return q


def manageable_users(user) -> QuerySet:
    """Users this user may manage: every one of their assignments is manageable.

    A user with an assignment the actor may not manage (a peer role, a higher
    role, or another area) is left out entirely. Users with no assignment at
    all are visible to Super Admins only.
    """
    from .models import RoleAssignment, User

    q = manageable_assignment_q(user)
    manageable = RoleAssignment.objects.filter(q).values("user_id")
    other = RoleAssignment.objects.exclude(q).values("user_id")
    visible = Q(id__in=manageable)
    if is_super_admin(user):
        visible |= ~Q(id__in=RoleAssignment.objects.values("user_id"))
    return User.objects.filter(visible).exclude(id__in=other).exclude(id=user.id)


def role_matches_level(role: str, area: AreaRef) -> bool:
    return ROLE_LEVEL.get(role) == area.level


def capabilities(user) -> dict:
    roles = {item.role for item in user_areas(user)}
    posting = set(POSTING_ROLES)
    if settings.SUPER_ADMIN_CAN_POST:
        posting.add(Role.SUPER_ADMIN)
    manage = manageable_roles(user)
    return {
        "can_post": bool(roles & posting),
        "can_review": bool(roles & REVIEW_ROLES),
        "can_manage_users": bool(manage),
        "manageable_roles": manage,
        "is_super_admin": Role.SUPER_ADMIN in roles,
    }
