from django.utils.translation import gettext as _
from rest_framework.permissions import BasePermission

from apps.accounts.scope import is_super_admin
from grama.exceptions import permission_denied


class IsSuperAdmin(BasePermission):
    """Console-only endpoints: master data writes, dashboard, reports."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not is_super_admin(request.user):
            raise permission_denied(_("Only Super Admins can do this."))
        return True


def client_via(request) -> str:
    """'console' when the Super Admin console sent the request, else 'app'."""
    return "console" if request.headers.get("X-GC-Client") == "console" else "app"
