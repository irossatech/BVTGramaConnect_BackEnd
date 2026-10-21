"""Who may do what, and where. Every access decision in the API comes down to this module.

An official holds role assignments: one Django group in one area. A group's
permissions (role_matrix.ROLE_GROUPS) count only inside that assignment's area
and the areas below it: State > District > Mandal > Village. A platform-wide
group (Super Admin) covers every area.

    can(user, "posts.publish_post", post.target)   # one decision
    areas_for(user, "posts.review_post")           # where, for filtering lists
    area_q(user, "posts.review_post", "target_level", "target_area_id")  # the same as a Q

can() is True only when the user is logged in and active, has no forced
password change pending, and at least one ACTIVE assignment grants the
permission over the target. Targets are an AreaRef, PLATFORM (only
platform-wide assignments cover it) or ANYWHERE (the permission held in any
area at all, for "may this user use this screen").

User management adds ranks (0 is the highest): an admin may give, move or
remove a role, and block, reset or unlock someone, only for groups ranked
strictly below the assignment that grants the permission, and only inside its
area. Giving a role also needs that group's manage permission
(accounts.manage_mandal_admin and so on).

Lookups are cached on the user object, which the API loads fresh for every
request: two queries load a user's assignments and permissions, and area
ancestry is loaded in batches (prime()), so lists make no query per row.
Nothing here tests a group name or key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.conf import settings
from django.db.models import Q, QuerySet

from apps.geography.areas import DEPTH, AreaRef, ancestries, within_q


class _Scope:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


#: Target: the whole platform. Only platform-wide assignments cover it.
PLATFORM = _Scope("PLATFORM")
#: Target: anywhere. True when the permission is held in any area.
ANYWHERE = _Scope("ANYWHERE")
#: areas_for() result: every area (a platform-wide assignment holds the permission).
ALL_AREAS = _Scope("ALL_AREAS")


@dataclass(frozen=True)
class Grant:
    """One active assignment and what its group allows."""

    assignment_id: object
    group_id: int
    key: str
    name: str
    level: str
    rank: int
    platform_wide: bool
    area: AreaRef
    permissions: frozenset[str]


@dataclass(frozen=True)
class RoleInfo:
    """A group that is a role: RoleGroup plus its group's name."""

    group_id: int
    key: str
    name: str
    name_te: str
    level: str
    rank: int
    platform_wide: bool
    manage_permission: str

    def label(self, language: str = "te") -> str:
        return self.name_te if (language or "te").startswith("te") and self.name_te else self.name


@dataclass
class _Cache:
    grants: list[Grant]
    ancestry: dict[AreaRef, dict[str, int]] = field(default_factory=dict)
    roles: dict | None = None


_CACHE_ATTR = "_authz_cache"


# --- Loading ----------------------------------------------------------------------------


def _setting_allows(requires: dict, perm: str) -> bool:
    setting = (requires or {}).get(perm)
    return setting is None or bool(getattr(settings, setting, False))


def _load(user) -> _Cache:
    from django.contrib.auth.models import Group

    from .models import RoleAssignment

    rows = list(
        RoleAssignment.objects.filter(user_id=user.pk, is_active=True, group__role_group__isnull=False).values(
            "id",
            "group_id",
            "group__name",
            "area_level",
            "area_id",
            "group__role_group__key",
            "group__role_group__level",
            "group__role_group__rank",
            "group__role_group__platform_wide",
            "group__role_group__requires_setting",
        )
    )
    perms: dict[int, set[str]] = {}
    if rows:
        through = Group.permissions.through
        for group_id, app_label, codename in through.objects.filter(
            group_id__in={row["group_id"] for row in rows}
        ).values_list("group_id", "permission__content_type__app_label", "permission__codename"):
            perms.setdefault(group_id, set()).add(f"{app_label}.{codename}")
    grants = []
    for row in rows:
        requires = row["group__role_group__requires_setting"] or {}
        allowed = frozenset(p for p in perms.get(row["group_id"], ()) if _setting_allows(requires, p))
        grants.append(
            Grant(
                assignment_id=row["id"],
                group_id=row["group_id"],
                key=row["group__role_group__key"],
                name=row["group__name"],
                level=row["group__role_group__level"],
                rank=row["group__role_group__rank"],
                platform_wide=row["group__role_group__platform_wide"],
                area=AreaRef(row["area_level"], row["area_id"]),
                permissions=allowed,
            )
        )
    grants.sort(key=lambda grant: (grant.rank, grant.area.level, grant.area.id))
    return _Cache(grants=grants)


def _cache(user) -> _Cache:
    cache = getattr(user, _CACHE_ATTR, None)
    if cache is None:
        cache = _load(user)
        setattr(user, _CACHE_ATTR, cache)
    return cache


def forget(user) -> None:
    """Drop the cached grants, after changing this user object's assignments in the same request."""
    if user is not None and hasattr(user, _CACHE_ATTR):
        delattr(user, _CACHE_ATTR)


def is_usable(user, *, pending_change_ok: bool = False) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and (pending_change_ok or not getattr(user, "must_change_password", False))
    )


def grants(user, *, pending_change_ok: bool = False) -> list[Grant]:
    """The user's active assignments, highest rank first. Empty when the user may not act.

    pending_change_ok=True answers as if the forced password change were done:
    for the console login gate and for /me, which only shows or hides UI.
    """
    if not is_usable(user, pending_change_ok=pending_change_ok):
        return []
    return list(_cache(user).grants)


def prime(user, areas: Iterable[AreaRef]) -> None:
    """Load the ancestry of many target areas at once, before checking them one by one."""
    if not is_usable(user, pending_change_ok=True):
        return
    cache = _cache(user)
    missing = {area for area in areas if isinstance(area, AreaRef) and area not in cache.ancestry}
    if missing:
        cache.ancestry.update(ancestries(missing))


def _ancestry(user, area: AreaRef) -> dict[str, int]:
    cache = _cache(user)
    if area not in cache.ancestry:
        cache.ancestry.update(ancestries([area]))
    return cache.ancestry[area]


def _covers(user, grant: Grant, target) -> bool:
    if target is ANYWHERE:
        return True
    if target is PLATFORM:
        return grant.platform_wide
    if grant.platform_wide:
        return True
    if DEPTH[grant.area.level] > DEPTH[target.level]:
        return False
    return _ancestry(user, target).get(grant.area.level) == grant.area.id


# --- Questions ---------------------------------------------------------------------------


def grants_for(user, perm: str, target=ANYWHERE, *, pending_change_ok: bool = False) -> list[Grant]:
    """Active assignments that grant `perm` over `target`, highest rank first."""
    return [
        g
        for g in grants(user, pending_change_ok=pending_change_ok)
        if perm in g.permissions and _covers(user, g, target)
    ]


def best_grant(user, perm: str, target=ANYWHERE) -> Grant | None:
    """The highest-ranked assignment that grants `perm` over `target`."""
    found = grants_for(user, perm, target)
    return found[0] if found else None


def can(user, perm: str, target=ANYWHERE, *, pending_change_ok: bool = False) -> bool:
    return bool(grants_for(user, perm, target, pending_change_ok=pending_change_ok))


def areas_for(user, perm: str):
    """Where the user holds `perm`: ALL_AREAS, or a list of AreaRef (empty: nowhere)."""
    found = grants_for(user, perm)
    if any(g.platform_wide for g in found):
        return ALL_AREAS
    return sorted({g.area for g in found})


def area_q(user, perm: str, level_field: str = "area_level", id_field: str = "area_id") -> Q:
    """ORM filter: rows whose (level, id) pair lies inside an area where the user holds `perm`."""
    areas = areas_for(user, perm)
    if areas is ALL_AREAS:
        return Q()
    query = Q(pk__in=[])
    for area in areas:
        query |= within_q(area, level_field=level_field, id_field=id_field)
    return query


def permissions(user, *, pending_change_ok: bool = False) -> list[str]:
    """Every permission the user holds somewhere, as app_label.codename."""
    return sorted({perm for grant in grants(user, pending_change_ok=pending_change_ok) for perm in grant.permissions})


def permissions_by_area(user, *, pending_change_ok: bool = False) -> list[tuple[AreaRef, list[str]]]:
    merged: dict[AreaRef, set[str]] = {}
    for grant in grants(user, pending_change_ok=pending_change_ok):
        merged.setdefault(grant.area, set()).update(grant.permissions)
    return [(area, sorted(perms)) for area, perms in merged.items()]


def is_platform_wide(user, perm: str) -> bool:
    return can(user, perm, PLATFORM)


# --- Roles and ranks -----------------------------------------------------------------------


def roles() -> dict[str, RoleInfo]:
    """Every group that is a role, by key, highest rank first."""
    from .models import RoleGroup

    return {
        row.key: RoleInfo(
            group_id=row.group_id,
            key=row.key,
            name=row.group.name,
            name_te=row.name_te,
            level=row.level,
            rank=row.rank,
            platform_wide=row.platform_wide,
            manage_permission=row.manage_permission,
        )
        for row in RoleGroup.objects.select_related("group").order_by("rank")
    }


def known_roles(user) -> dict[str, RoleInfo]:
    """roles(), loaded once per request on the user object."""
    if user is None or not getattr(user, "is_authenticated", False):
        return roles()
    cache = _cache(user)
    if cache.roles is None:
        cache.roles = roles()
    return cache.roles


def role_info(role_group) -> RoleInfo:
    return RoleInfo(
        group_id=role_group.group_id,
        key=role_group.key,
        name=role_group.group.name,
        name_te=role_group.name_te,
        level=role_group.level,
        rank=role_group.rank,
        platform_wide=role_group.platform_wide,
        manage_permission=role_group.manage_permission,
    )


def _manages(user, role: RoleInfo, area, perm: str | None = None) -> bool:
    """Some assignment covers `area`, ranks above `role`, may give `role`, and holds `perm` too."""
    if not role.manage_permission:
        return False
    for grant in grants_for(user, role.manage_permission, area):
        if grant.rank < role.rank and (perm is None or perm in grant.permissions):
            return True
    return False


def can_assign(user, role: RoleInfo, area: AreaRef) -> bool:
    """May `user` give `role` in `area` to someone?"""
    return _manages(user, role, area)


def assignable_roles(user) -> list[RoleInfo]:
    """Roles the user may give somewhere, highest first."""
    if not is_usable(user):
        return []
    return [role for role in known_roles(user).values() if _manages(user, role, ANYWHERE)]


def _assignment_roles(target_user) -> list[tuple[RoleInfo, AreaRef]]:
    """Every assignment of the target, active or not, with its role. Uses prefetched rows when present."""
    out = []
    for assignment in target_user.role_assignments.all():
        role_group = getattr(assignment.group, "role_group", None)
        if role_group is None:
            continue
        out.append((role_info(role_group), assignment.area))
    return out


def can_act_on(user, perm: str | None, target_user) -> bool:
    """May `user` use `perm` (block_user, reset_password, ...) on `target_user`?

    Every assignment of the target, active or not, must lie inside an area
    where the user holds both `perm` and the manage permission of that
    assignment's group, through an assignment ranked strictly higher. Nobody
    acts on themselves. Users without any assignment are left to platform-wide
    admins. perm=None asks only whether the user manages the target at all.
    """
    if not is_usable(user) or target_user is None or target_user.pk == user.pk:
        return False
    held = _assignment_roles(target_user)
    if not held:
        manage_perms = {role.manage_permission for role in known_roles(user).values() if role.manage_permission}
        return any(
            g.platform_wide and (perm is None or perm in g.permissions) and g.permissions & manage_perms
            for g in grants(user)
        )
    prime(user, [area for _role, area in held])
    return all(_manages(user, role, area, perm) for role, area in held)


def manageable_users(user) -> QuerySet:
    """Users every one of whose assignments the user manages (can_act_on(user, None, them)) as a queryset."""
    from .models import RoleAssignment, User

    if not is_usable(user):
        return User.objects.none()
    all_roles = known_roles(user)
    query = Q(pk__in=[])
    any_clause = False
    platform_manager = False
    for grant in grants(user):
        for role in all_roles.values():
            if role.manage_permission in grant.permissions and role.rank > grant.rank:
                clause = Q(group_id=role.group_id)
                if not grant.platform_wide:
                    clause &= within_q(grant.area)
                else:
                    platform_manager = True
                query |= clause
                any_clause = True
    if not any_clause:
        return User.objects.none()
    manageable = RoleAssignment.objects.filter(query).values("user_id")
    other = RoleAssignment.objects.exclude(query).values("user_id")
    visible = Q(id__in=manageable)
    if platform_manager:
        visible |= ~Q(id__in=RoleAssignment.objects.values("user_id"))
    return User.objects.filter(visible).exclude(id__in=other).exclude(id=user.pk)


# --- Who holds a permission (for notifications and reports) ----------------------------------


def holders(perm: str, areas: Iterable[AreaRef] | None = None, *, platform_wide: bool | None = None) -> QuerySet:
    """Active users with an active assignment granting `perm`.

    areas: only assignments at exactly these areas (None: any area).
    platform_wide: True only platform-wide groups, False none of them, None either.
    """
    from .models import RoleAssignment, RoleGroup, User

    app_label, codename = perm.split(".", 1)
    query = Q(
        is_active=True,
        group__permissions__content_type__app_label=app_label,
        group__permissions__codename=codename,
        group__role_group__isnull=False,
    )
    if platform_wide is not None:
        query &= Q(group__role_group__platform_wide=platform_wide)
    if areas is not None:
        area_query = Q(pk__in=[])
        for area in areas:
            area_query |= Q(area_level=area.level, area_id=area.id)
        query &= area_query
    switched_off = [
        row.group_id
        for row in RoleGroup.objects.exclude(requires_setting={})
        if not _setting_allows(row.requires_setting, perm)
    ]
    assignments = RoleAssignment.objects.filter(query).exclude(group_id__in=switched_off)
    return User.objects.filter(id__in=assignments.values("user_id"), is_active=True)


def role_keys_holding(perm: str, *, platform_wide: bool | None = None) -> list[str]:
    """Keys of the groups that grant `perm` (for reports that group people by role)."""
    from .models import RoleGroup

    app_label, codename = perm.split(".", 1)
    rows = RoleGroup.objects.filter(
        group__permissions__content_type__app_label=app_label, group__permissions__codename=codename
    )
    if platform_wide is not None:
        rows = rows.filter(platform_wide=platform_wide)
    return [row.key for row in rows.order_by("rank") if _setting_allows(row.requires_setting, perm)]
