"""Services and service requests: the only writer. docs/api-contract.md, section 16.

- Services: the Super Admin adds, changes, hides and reorders them. The key is
  made from the English name on create and never changes; services are never
  deleted, so old requests stay readable. An optional logo goes through
  grama.logos (JPEG, PNG or WebP re-encoded as WebP); the file is
  services/<key>-<random>-256.webp, new on every replacement.
- A citizen's phone (citizens.CitizenDevice, X-Citizen-Token) creates a request
  for its village; a blocked phone cannot, and SERVICE_REQUESTS_PER_DAY limits
  one phone (all its installs) per 24 hours. In the same transaction it is
  assigned to the village's Village Admin (selectors.village_admin_of).
- Officials holding service_requests.manage_service_requests over the village
  (Village, Mandal and District Admins, the Super Admin) see it. An official
  with only service_requests.view_service_requests (a Panchayat member) sees
  the requests assigned to them inside their area.
- Only the assignee changes a request: any status, a note, and passing it on
  to another official of the area. Everyone else reads it. A request nobody
  holds (no Village Admin, or the assignee was blocked or lost their role
  there) may only be assigned, by an official managing requests over it.
- Rejecting and completing need a note for the citizen.

Every change writes one ServiceRequestEvent and the audit entries in the same
transaction; notifications go out after commit. No name, phone, email or
address ever goes into the audit log.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts import authz
from apps.audit.services import record
from apps.citizens import services as citizens
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from grama import logos
from grama.exceptions import ApiError, permission_denied

from .models import RequestStatus, Service, ServiceRequest, ServiceRequestEvent
from .selectors import MANAGE, VIEW, village_admin_of

logger = logging.getLogger("grama.service_requests")

#: Statuses that close a request and so need a note for the citizen, with the message when it is missing.
NOTE_REQUIRED = {
    RequestStatus.REJECTED: gettext_lazy("Write the reason to reject the request."),
    RequestStatus.COMPLETED: gettext_lazy("Write a note for the citizen."),
}
DAY = timedelta(hours=24)
#: Service fields the Super Admin may change.
SERVICE_FIELDS = ("name_en", "name_te", "hint_en", "hint_te", "icon", "color", "is_active")


def area_of(request: ServiceRequest) -> AreaRef:
    return AreaRef(AreaLevel.VILLAGE, request.village_id)


def _holder_id(request: ServiceRequest):
    """The assignee while they can still act on the request; None when nobody can (never assigned,
    or the assignee was blocked or lost their role over the village), so it is not stuck."""
    assignee = request.assigned_to
    if assignee is None or not assignee.is_active or not authz.can(assignee, VIEW, area_of(request)):
        return None
    return assignee.pk


def allowed_actions(user, request: ServiceRequest) -> list[str]:
    """Only the assignee changes a request; the area's admins may only assign one nobody holds."""
    area = area_of(request)
    if not authz.can(user, VIEW, area):
        return []
    if request.assigned_to_id == user.pk:  # an active user who can view it: they hold it
        return ["set_status", "assign"]
    if authz.can(user, MANAGE, area) and _holder_id(request) is None:
        return ["assign"]
    return []


def _validation(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


def _after_commit(function, *args) -> None:
    """Run a notification after commit; never fail the change over it."""

    def run():
        try:
            function(*args)
        except Exception:
            logger.exception("Service request notification failed")

    transaction.on_commit(run)


# --- Services (Super Admin) ------------------------------------------------------------------------


def _new_key(name_en: str) -> str:
    base = slugify(name_en).replace("-", "_")[:36].strip("_") or "service"
    key, n = base, 2
    while Service.objects.filter(key=key).exists():
        key, n = f"{base}_{n}", n + 1
    return key


def _check_name(name_en: str, service: Service | None = None) -> None:
    taken = Service.objects.filter(name_en__iexact=name_en)
    if service is not None:
        taken = taken.exclude(pk=service.pk)
    if taken.exists():
        raise _validation("name_en", _("A service with this name already exists."))


def _store_logo(service: Service, data: bytes) -> None:
    logos.store_logo(service.logo, service.key, data)


def _delete_logo_after_commit(name: str) -> None:
    logos.delete_after_commit(name, Service._meta.get_field("logo").storage)


def create_service(user, values: dict, logo=None) -> Service:
    _check_name(values["name_en"])
    data = logos.process_logo(logo) if logo is not None else None
    with transaction.atomic():
        last = Service.objects.aggregate(last=Max("order"))["last"]
        service = Service.objects.create(
            key=_new_key(values["name_en"]),
            order=0 if last is None else last + 1,
            **{name: values[name] for name in SERVICE_FIELDS if name in values},
        )
        if data is not None:
            _store_logo(service, data)
            service.save(update_fields=["logo"])
        record("service.created", actor=user, target=service, target_label=service.name_en,
               data={"key": service.key, "logo": data is not None})
    return service


def update_service(user, service: Service, changes: dict, logo=None, remove_logo: bool = False) -> Service:
    """Change fields; `logo` replaces the logo, `remove_logo` takes it away (the app then draws `icon`)."""
    if "name_en" in changes:
        _check_name(changes["name_en"], service)
    data = logos.process_logo(logo) if logo is not None else None
    changed = [name for name in SERVICE_FIELDS if name in changes and changes[name] != getattr(service, name)]
    drop_logo = remove_logo and data is None and bool(service.logo)
    if not changed and data is None and not drop_logo:
        return service
    with transaction.atomic():
        for name in changed:
            setattr(service, name, changes[name])
        if data is not None or drop_logo:
            _delete_logo_after_commit(service.logo.name)
            if data is not None:
                _store_logo(service, data)
            else:
                service.logo = ""
            changed.append("logo")
        service.save()
        details = [name for name in changed if name != "is_active"]
        if details:
            record("service.updated", actor=user, target=service, target_label=service.name_en,
                   data={"key": service.key, "changed": details})
        if "is_active" in changed:
            record("service.shown" if service.is_active else "service.hidden", actor=user, target=service,
                   target_label=service.name_en, data={"key": service.key})
    return service


def reorder_services(user, keys: list[str]) -> None:
    """`keys` is every service's key in the new order; anything else is 400 on `keys`."""
    existing = dict(Service.objects.values_list("key", "order"))
    if len(keys) != len(set(keys)) or set(keys) != set(existing):
        raise _validation("keys", _("Send every service's key exactly once."))
    with transaction.atomic():
        for position, key in enumerate(keys):
            if existing[key] != position:
                Service.objects.filter(key=key).update(order=position, updated_at=timezone.now())
        record("service.reordered", actor=user, data={"keys": keys})


# --- Citizens ------------------------------------------------------------------------------------


def create_request(device, *, service: str, name: str, phone: str, address: str, email: str = "",
                   details: str = "") -> ServiceRequest:
    """A citizen's request for the phone's village, assigned to its Village Admin. Officials are told after commit."""
    now = timezone.now()
    block = citizens.active_block(device, now)
    if block is not None:
        raise citizens.blocked_error(block)
    chosen = Service.objects.filter(key=service, is_active=True).first()
    if chosen is None:
        raise _validation("service", _("This service is not available."))
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
        assignee = village_admin_of(device.village_id)
        request = ServiceRequest.objects.create(
            service=chosen, name=name, phone=phone, email=email, address=address, details=details,
            village_id=device.village_id, device=device, assigned_to=assignee,
        )
        area = area_of(request)
        # No name, phone, email or address in the audit log: only the service and the phone's device id.
        record("service_request.created", target=request, target_label=chosen.name_en, area=area, via="citizen",
               data={"service": chosen.key, "citizen": str(device.pk)})
        if assignee is not None:
            ServiceRequestEvent.objects.create(
                request=request, by=None, assigned_changed=True, assigned_to=assignee,
                note=_("Assigned to the Village Admin automatically"),
            )
            record("service_request.assigned", target=request, target_label=chosen.name_en, area=area,
                   via="system", data={"to": str(assignee.pk), "to_name": assignee.name, "auto": True})
        from apps.notifications import services as notifications

        _after_commit(notifications.notify_service_request, request)
    return request


# --- Officials ------------------------------------------------------------------------------------


def _assignee_or_raise(user_id, request: ServiceRequest):
    """An active official whose area contains the request's village."""
    from apps.accounts.models import User

    assignee = User.objects.filter(pk=user_id, is_active=True).first()
    if assignee is None or not authz.can(assignee, VIEW, area_of(request)):
        raise _validation("assigned_to", _("Assign the request to an active official of this area."))
    return assignee


UNSET = object()


def update_request(user, request: ServiceRequest, *, status=None, assigned_to=UNSET, note=None) -> ServiceRequest:
    """PATCH by an official: any of a new status, an assignee (a user id or None) and a note."""
    area = area_of(request)
    actions = allowed_actions(user, request)
    if not actions:
        raise permission_denied(_("Only the person this request is assigned to can change it."))
    note = (note or "").strip() if note is not None else None
    if (status is not None or note) and "set_status" not in actions:
        raise permission_denied(_("Assign the request first; then the assignee changes its status."))
    if assigned_to is not UNSET and "assign" not in actions:
        raise permission_denied(_("Only the person this request is assigned to can assign it."))
    grant = authz.best_grant(user, MANAGE, area) or authz.best_grant(user, VIEW, area)

    if status in NOTE_REQUIRED and not note:
        raise _validation("note", str(NOTE_REQUIRED[status]))

    new_status = status if status is not None and status != request.status else None
    assignee = None
    assign_changed = False
    if assigned_to is not UNSET:
        assignee = _assignee_or_raise(assigned_to, request) if assigned_to is not None else None
        assign_changed = (assignee.pk if assignee else None) != request.assigned_to_id
    if new_status is None and not assign_changed and not note:
        return request

    from apps.notifications import services as notifications

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
                  "target_label": request.service.name_en, "area": area}
        if new_status is not None:
            # The note is the official's words to the citizen; it holds no personal data of theirs.
            record("service_request.status_changed", **common,
                   data={"from": before, "to": new_status, **({"note": note} if note else {})})
            _after_commit(notifications.tell_citizen_service_request, request)
        if assign_changed:
            record("service_request.assigned", **common,
                   data={"to": str(assignee.pk) if assignee else None, "to_name": assignee.name if assignee else "",
                         "auto": False})
            if assignee is not None and assignee.pk != user.pk:
                _after_commit(notifications.tell_assignee, request)
    return request
