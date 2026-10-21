"""DRF permission classes on top of accounts.authz.

    permission_classes = [HasPerm("audit.view_audit_log", PLATFORM)]
    permission_classes = [HasPerm("posts.view_review_queue")]          # held in any area

has_permission() asks authz.can(user, perm, target). has_object_permission()
asks it again for the object's own area (obj.target for posts, obj.area for
anything else with an area), so DRF's get_object() refuses objects outside
the user's areas with 403 out_of_area.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

from grama.exceptions import out_of_area, permission_denied

from . import authz
from .authz import ANYWHERE, PLATFORM  # noqa: F401  (re-exported for views)


def object_area(obj):
    for name in ("target", "area"):
        value = getattr(obj, name, None)
        if isinstance(value, authz.AreaRef):
            return value
    return None


class HasPerm(BasePermission):
    """Allow the request when the user holds `perm` over `target` (ANYWHERE by default)."""

    def __init__(self, perm: str, target=ANYWHERE, message=None):
        self.perm = perm
        self.target = target
        self.message = message or _("Your role does not allow this.")

    def __call__(self):
        # DRF instantiates each entry of permission_classes; an instance stands for itself.
        return self

    def __repr__(self) -> str:
        return f"HasPerm({self.perm!r}, {self.target!r})"

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False  # 401 not_authenticated
        if not authz.can(user, self.perm, self.target):
            raise permission_denied(self.message)
        return True

    def has_object_permission(self, request, view, obj):
        area = object_area(obj)
        if area is not None and not authz.can(request.user, self.perm, area):
            raise out_of_area()
        return True


class ManagesUsers(BasePermission):
    """The user may give at least one role somewhere (any manage_* permission over a lower group)."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if not authz.assignable_roles(user):
            raise permission_denied(_("Your role cannot manage users."))
        return True
