"""Citizens' service requests (Aadhaar, PAN, certificates …). docs/api-contract.md, section 16.

A citizen's phone (citizens.CitizenDevice, no login) asks for help with a
government service. The request belongs to the phone's village; the admins of
that village, its mandal and district and the Super Admin handle it, and may
assign it to an official of the area. Every change is kept in
ServiceRequestEvent; `note` is the latest note, which the citizen sees.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geography.models import AreaLevel


class Service(models.TextChoices):
    AADHAAR = "aadhaar", _("Aadhaar")
    PAN = "pan", _("PAN")
    VOTER_ID = "voter_id", _("Voter ID")
    CERTIFICATES = "certificates", _("Certificates")
    LOANS_INSURANCE = "loans_insurance", _("Loans & Insurance")
    GOVT_SCHEMES = "govt_schemes", _("Govt Schemes")
    LAND_RECORDS = "land_records", _("Land Records")
    FARMER = "farmer", _("Farmer Services")


#: Telugu names for notification texts (Accept-Language is not known when a push is built).
SERVICE_TE = {
    Service.AADHAAR: "ఆధార్",
    Service.PAN: "పాన్",
    Service.VOTER_ID: "ఓటర్ ఐడీ",
    Service.CERTIFICATES: "సర్టిఫికెట్లు",
    Service.LOANS_INSURANCE: "రుణాలు & బీమా",
    Service.GOVT_SCHEMES: "ప్రభుత్వ పథకాలు",
    Service.LAND_RECORDS: "భూ రికార్డులు",
    Service.FARMER: "రైతు సేవలు",
}


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


def label(choice, language: str) -> str:
    """The service's or status's name in Telugu or English."""
    telugu = (language or "te").startswith("te")
    table = SERVICE_TE if choice in Service.values else STATUS_TE
    if telugu and choice in table:
        return table[choice]
    enum = Service if choice in Service.values else RequestStatus
    return str(enum(choice).label)


class ServiceRequest(models.Model):
    service = models.CharField(max_length=20, choices=Service.choices)
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=10)
    email = models.EmailField(blank=True)
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
        return f"{self.get_service_display()} request {self.pk}"


class ServiceRequestEvent(models.Model):
    """One change to a request: a new status, a new assignee, a note, or several at once."""

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
