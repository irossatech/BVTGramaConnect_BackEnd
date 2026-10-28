"""Services and citizens' service requests. docs/api-contract.md, section 16.

The Super Admin manages the list of services (Service) the app shows on Home.
A citizen's phone (citizens.CitizenDevice, no login) asks for one of them; the
request belongs to the phone's village and is assigned to that village's
Village Admin automatically. The admins of the village, its mandal and district
and the Super Admin handle it. Every change is kept in ServiceRequestEvent;
`note` is the latest note, which the citizen sees.

The first version (fixed service list) was removed in migration 0002; this one
starts at 0003.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geography.models import AreaLevel

#: Icons the app draws (the server only checks the key).
ICONS = [
    "aadhaar", "eci", "id_badge", "card",
    "certificate", "document", "scheme", "loan",
    "land", "farmer", "house", "ration",
    "health", "education", "pension", "women",
    "job", "water", "power", "police",
    "help", "services",
]
COLORS = ["orange", "blue", "green", "purple", "teal"]
#: The icon a service gets when the Super Admin picks none (a logo usually replaces it).
DEFAULT_ICON = "services"


def service_logo_path(instance, filename):
    return f"services/{filename}"


class Service(models.Model):
    """One service on the citizen's Home. Never deleted: hidden with is_active.

    The app shows `logo` (an image the Super Admin uploaded) when there is one, else draws `icon`.
    """

    #: Made by the server from name_en on create; never changes. Requests refer to it.
    key = models.SlugField(max_length=40, unique=True)
    name_en = models.CharField(max_length=60)
    name_te = models.CharField(max_length=60, blank=True)
    icon = models.CharField(max_length=30, choices=[(icon, icon) for icon in ICONS], default=DEFAULT_ICON)
    #: Optional uploaded logo, re-encoded as WebP by services.process_logo. Empty: the app draws `icon`.
    logo = models.FileField(upload_to=service_logo_path, max_length=200, blank=True)
    color = models.CharField(max_length=10, choices=[(color, color) for color in COLORS])
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        permissions = [("manage_services", "Add, change, hide and reorder the services citizens ask for")]

    def __str__(self):
        return self.name_en

    def display_name(self, language: str) -> str:
        """Telugu when asked for and filled in, else English."""
        if (language or "te").startswith("te") and self.name_te:
            return self.name_te
        return self.name_en


class RequestStatus(models.TextChoices):
    NEW = "NEW", _("New")
    APPROVED = "APPROVED", _("Approved")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    COMPLETED = "COMPLETED", _("Completed")
    REJECTED = "REJECTED", _("Rejected")


STATUS_TE = {
    RequestStatus.NEW: "కొత్తది",
    RequestStatus.APPROVED: "ఆమోదించబడింది",
    RequestStatus.IN_PROGRESS: "ప్రక్రియలో ఉంది",
    RequestStatus.COMPLETED: "పూర్తయింది",
    RequestStatus.REJECTED: "తిరస్కరించబడింది",
}


def status_label(status: str, language: str) -> str:
    """The status's name in Telugu or English (for push texts)."""
    if (language or "te").startswith("te") and status in STATUS_TE:
        return STATUS_TE[status]
    return str(RequestStatus(status).label)


class ServiceRequest(models.Model):
    service = models.ForeignKey(
        Service, to_field="key", db_column="service", on_delete=models.PROTECT, related_name="requests"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=10)
    email = models.EmailField(blank=True)
    address = models.TextField(max_length=300)
    details = models.TextField(max_length=500, blank=True)
    #: The phone's village when the request was made.
    village = models.ForeignKey("geography.Village", on_delete=models.PROTECT, related_name="service_requests")
    #: Always VILLAGE: with village_id it is the (level, id) pair authz.area_q filters on.
    area_level = models.CharField(max_length=10, default=AreaLevel.VILLAGE, editable=False)
    device = models.ForeignKey("citizens.CitizenDevice", on_delete=models.PROTECT, related_name="service_requests")
    status = models.CharField(max_length=12, choices=RequestStatus.choices, default=RequestStatus.NEW)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="service_requests"
    )
    #: The latest note to the citizen.
    note = models.TextField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["village", "status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["device", "created_at"]),
        ]
        # Checked per area by accounts.authz (docs/authorization.md).
        permissions = [
            ("view_service_requests", "See citizens' service requests of the area, or those assigned to you"),
            ("manage_service_requests", "Change the status of, assign and answer service requests of the area"),
        ]

    def __str__(self):
        return f"{self.service_id} request {self.pk}"


class ServiceRequestEvent(models.Model):
    """One change to a request: a new status, a new assignee, a note, or several at once.

    `by` is empty for the system (the automatic assignment on create).
    """

    request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE, related_name="history")
    at = models.DateTimeField(auto_now_add=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    #: The key of the group the official acted under, for display.
    by_role = models.CharField(max_length=20, blank=True)
    #: Empty when the status did not change.
    status = models.CharField(max_length=12, choices=RequestStatus.choices, blank=True)
    assigned_changed = models.BooleanField(default=False)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.TextField(max_length=500, blank=True)

    class Meta:
        ordering = ["at", "id"]
