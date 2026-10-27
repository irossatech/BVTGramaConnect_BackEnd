"""Service requests: who sees them, creating one, and every change. docs/api-contract.md, section 16.

- A citizen's phone (citizens.CitizenDevice, X-Citizen-Token) creates a request
  for its village; a blocked phone cannot, and SERVICE_REQUESTS_PER_DAY limits
  one phone (all its installs) per 24 hours.
- Officials holding service_requests.manage_service_requests over the village
  (Village, Mandal and District Admins, the Super Admin) see it, change its
  status, assign it to an official of the area and write a note.
- An official with only service_requests.view_service_requests (a Panchayat
  member) sees the requests assigned to them inside their area, and may move
  them to IN_PROGRESS or COMPLETED.

Every change writes one ServiceRequestEvent and the audit entries in the same
transaction; the push to the citizen goes out after commit.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts import authz
from apps.audit.services import record
from apps.citizens import services as citizens
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from grama.exceptions import ApiError, permission_denied

from .models import RequestStatus, ServiceRequest, ServiceRequestEvent

logger = logging.getLogger("grama.service_requests")

VIEW = "service_requests.view_service_requests"
MANAGE = "service_requests.manage_service_requests"
AREA = {"level_field": "area_level", "id_field": "village_id"}
#: What an assignee who cannot manage requests may move them to.
ASSIGNEE_STATUSES = (RequestStatus.IN_PROGRESS, RequestStatus.COMPLETED)
DAY = timedelta(hours=24)


def area_of(request: ServiceRequest) -> AreaRef:
    return AreaRef(AreaLevel.VILLAGE, request.village_id)


def visible(user) -> QuerySet:
    """Requests inside the areas where the user manages them, plus those assigned to them inside their view area."""
    if authz.areas_for(user, MANAGE) is authz.ALL_AREAS:
        # area_q gives Q() for "everywhere", and Q() | X would collapse to X: return everything explicitly.
        return ServiceRequest.objects.all()
    scope = authz.area_q(user, MANAGE, **AREA) | (authz.area_q(user, VIEW, **AREA) & Q(assigned_to=user))
    return ServiceRequest.objects.filter(scope)


def allowed_actions(user, request: ServiceRequest) -> list[str]:
    if authz.can(user, MANAGE, area_of(request)):
        return ["set_status", "assign"]
    if request.assigned_to_id == user.id and authz.can(user, VIEW, area_of(request)):
        return ["set_status"]
    return []


def _validation(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


# --- Citizens ------------------------------------------------------------------------------------


def create(device, *, service: str, name: str, phone: str, email: str = "", details: str = "") -> ServiceRequest:
    """A citizen's request for the phone's village. Officials are told after commit."""
    now = timezone.now()
    block = citizens.active_block(device, now)
    if block is not None:
        raise citizens.blocked_error(block)
    limit = settings.SERVICE_REQUESTS_PER_DAY
    if limit:
        recent = ServiceRequest.objects.filter(device__in=citizens.same_phone(device), created_at__gt=now - DAY)
        if recent.count() >= limit:
            oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
            raise citizens.throttled(
                _("You can send at most %(n)d service requests a day.") % {"n": limit},
                max(1, int((oldest + DAY - now).total_seconds()) + 1),
            )
    if not Village.objects.active().filter(id=device.village_id).exists():
        raise _validation("village_id", _("Your village is not available any more. Choose your village again."))

    with transaction.atomic():
        request = ServiceRequest.objects.create(
            service=service, name=name, phone=phone, email=email, details=details,
            village_id=device.village_id, device=device,
        )
        # No name, phone or email in the audit log: only the service and the phone's device id.
        record(
            "service_request.created", target=request, target_label=request.get_service_display(),
            area=area_of(request), via="citizen", data={"service": service, "citizen": str(device.pk)},
        )
        transaction.on_commit(lambda: _notify_officials(request))
    return request


def _notify_officials(request: ServiceRequest) -> None:
    from apps.notifications import services as notifications

    try:
        notifications.notify_service_request(request)
    except Exception:  # never fail the citizen's request over a notification
        logger.exception("Service request notification failed")


# --- Officials ------------------------------------------------------------------------------------


def _assignee_or_raise(user_id, request: ServiceRequest):
    """An active official whose area contains the request's village."""
    from apps.accounts.models import User

    assignee = User.objects.filter(pk=user_id, is_active=True).first()
    if assignee is None or not authz.can(assignee, VIEW, area_of(request)):
        raise _validation("assigned_to", _("Assign the request to an active official of this area."))
    return assignee


UNSET = object()


def update(user, request: ServiceRequest, *, status=None, assigned_to=UNSET, note=None) -> ServiceRequest:
    """PATCH by an official: any of a new status, an assignee (a user id or None) and a note."""
    area = area_of(request)
    grant = authz.best_grant(user, MANAGE, area)
    if grant is None:
        grant = authz.best_grant(user, VIEW, area)
        if grant is None or request.assigned_to_id != user.id:
            raise permission_denied(_("You cannot change this request."))
        if assigned_to is not UNSET:
            raise permission_denied(_("Only admins can assign service requests."))
        if status is not None and status not in ASSIGNEE_STATUSES:
            raise permission_denied(_("You can only mark this request as in progress or completed."))

    note = (note or "").strip() if note is not None else None
    if status == RequestStatus.REJECTED and not note:
        raise _validation("note", _("Write a note to the citizen to reject the request."))

    new_status = status if status is not None and status != request.status else None
    assignee = None
    assign_changed = False
    if assigned_to is not UNSET:
        assignee = _assignee_or_raise(assigned_to, request) if assigned_to is not None else None
        assign_changed = (assignee.pk if assignee else None) != request.assigned_to_id
    if new_status is None and not assign_changed and not note:
        return request

    with transaction.atomic():
        before = request.status
        fields = ["updated_at"]
        if new_status is not None:
            request.status = new_status
            fields.append("status")
        if assign_changed:
            request.assigned_to = assignee
            fields.append("assigned_to")
        if note:
            request.note = note
            fields.append("note")
        request.save(update_fields=fields)
        ServiceRequestEvent.objects.create(
            request=request, by=user, by_role=grant.key, status=new_status or "",
            assigned_changed=assign_changed, assigned_to=assignee if assign_changed else None, note=note or "",
        )
        common = {"actor": user, "actor_role": grant.key, "target": request,
                  "target_label": request.get_service_display(), "area": area}
        if new_status is not None:
            record("service_request.status_changed", **common,
                   data={"from": before, "to": new_status, **({"note": note} if note else {})})
        if assign_changed:
            record("service_request.assigned", **common,
                   data={"to": str(assignee.pk) if assignee else None, "to_name": assignee.name if assignee else ""})
        if new_status is not None:
            transaction.on_commit(lambda: _tell_citizen(request))
    return request



def _tell_citizen(request: ServiceRequest) -> None:
    from apps.notifications import services as notifications

    try:
        notifications.tell_citizen_service_request(request)
    except Exception:
        logger.exception("Service request push failed")
