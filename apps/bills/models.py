"""Bills and recharges on the citizen's Home (docs/api-contract.md, section 18).

A list of its own, next to the services (apps.service_requests): the Super Admin adds,
changes, hides and reorders them. Tapping one shows "coming soon" in the app: there is
no payment and no request behind a bill yet.
"""

from django.db import models

#: Icons the app and the console draw (the server only checks the key).
ICONS = ["power", "water", "gas", "mobile", "landline", "dth", "broadband", "fastag", "credit_card", "bill"]
COLORS = ["orange", "blue", "green", "purple", "teal"]
DEFAULT_ICON = "bill"


def bill_logo_path(instance, filename):
    return f"bills/{filename}"


class Bill(models.Model):
    """One bill or recharge on the citizen's Home. Never deleted: hidden with is_active.

    The app shows `logo` (an image the Super Admin uploaded) when there is one, else draws `icon`.
    """

    #: Made by the server from name_en on create; never changes. Unique among bills only.
    key = models.SlugField(max_length=40, unique=True)
    name_en = models.CharField(max_length=60)
    name_te = models.CharField(max_length=60, blank=True)
    icon = models.CharField(max_length=30, choices=[(icon, icon) for icon in ICONS], default=DEFAULT_ICON)
    #: Optional uploaded logo, re-encoded as WebP by grama.logos. Empty: the app draws `icon`.
    logo = models.FileField(upload_to=bill_logo_path, max_length=200, blank=True)
    color = models.CharField(max_length=10, choices=[(color, color) for color in COLORS])
    is_active = models.BooleanField(default=True)
    #: Bills have their own order, independent of the services'.
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        permissions = [("manage_bills", "Add, change, hide and reorder the bills and recharges on Home")]

    def __str__(self):
        return self.name_en
