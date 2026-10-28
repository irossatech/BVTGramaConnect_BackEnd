"""Reads for services and service requests. docs/api-contract.md, section 16."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.accounts import authz
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel

from .models import Service, ServiceRequest

VIEW = "service_requests.view_service_requests"
MANAGE = "service_requests.manage_service_requests"
AREA = {"level_field": "area_level", "id_field": "village_id"}


def active_services() -> QuerySet:
    """What the citizen's Home shows, in order."""
    return Service.objects.filter(is_active=True).order_by("order", "id")


def all_services() -> QuerySet:
    """The Super Admin's list: hidden ones too."""
    return Service.objects.order_by("order", "id")


def visible_requests(user) -> QuerySet:
    """Requests inside the areas where the user manages them, plus those assigned to them inside their view area."""
    if authz.areas_for(user, MANAGE) is authz.ALL_AREAS:
        # area_q gives Q() for "everywhere", and Q() | X would collapse to X: return everything explicitly.
        return ServiceRequest.objects.all()
    scope = authz.area_q(user, MANAGE, **AREA) | (authz.area_q(user, VIEW, **AREA) & Q(assigned_to=user))
    return ServiceRequest.objects.filter(scope)


def village_admin_of(village_id: int):
    """The official who gets a new request: an active user managing requests with an assignment on exactly
    this village (the Village Admin). The earliest such assignment wins; None when there is none.

    Asks for the permission, not a group name (docs/authorization.md).
    """
    village = AreaRef(AreaLevel.VILLAGE, village_id)
    return (
        authz.holders(MANAGE, [village], platform_wide=False)
        .filter(
            role_assignments__is_active=True,
            role_assignments__area_level=AreaLevel.VILLAGE,
            role_assignments__area_id=village_id,
        )
        .order_by("role_assignments__assigned_at", "pk")
        .first()
    )
