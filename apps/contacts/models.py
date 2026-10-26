"""Important numbers: phone numbers citizens may call (docs/api-contract.md, section 14).

Area numbers (VRO, Mee Seva, Tahsildar office …) belong to a district, mandal or
village; government helplines (108, 1098 …) belong to the state. A citizen sees
the numbers of their village, its mandal, its district and its state. Writes go
through services.py only.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, wants_telugu


class ContactSection(models.TextChoices):
    AREA = "AREA", _("Area number")  # district, mandal or village
    HELPLINE = "HELPLINE", _("Government helpline")  # always at STATE level


class PhoneKind(models.TextChoices):
    MOBILE = "MOBILE", _("Mobile")  # 10 digits starting 6-9
    LANDLINE = "LANDLINE", _("Landline")  # 0 + STD code + number, 10-11 digits
    TOLL_FREE = "TOLL_FREE", _("Toll-free")  # 1800 + 6-7 digits
    SHORT_CODE = "SHORT_CODE", _("Short code")  # 3-6 digits; helplines only


class ContactType(models.Model):
    """VRO, Mee Seva … for area numbers; Health, Police … for helplines. Keys match the app's icons."""

    key = models.SlugField(_("key"), max_length=40, unique=True)
    section = models.CharField(_("section"), max_length=10, choices=ContactSection.choices)
    name_en = models.CharField(_("name (English)"), max_length=60)
    name_te = models.CharField(_("name (Telugu)"), max_length=60)
    is_other = models.BooleanField(_("admin types the name"), default=False)
    sort_order = models.PositiveSmallIntegerField(_("order"), default=100)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("contact type")
        verbose_name_plural = _("contact types")
        ordering = ["section", "sort_order", "id"]
        permissions = [("manage_contact_types", "Add, rename and switch off contact types")]

    def __str__(self):
        return self.key

    def display_name(self, language: str | None = None) -> str:
        return (self.name_te or self.name_en) if wants_telugu(language) else self.name_en


class Contact(models.Model):
    """One published phone number, visible to citizens in its area and below."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.ForeignKey(ContactType, on_delete=models.PROTECT, related_name="contacts")
    custom_type = models.CharField(_("other type"), max_length=40, blank=True)  # only when type.is_other
    name = models.CharField(_("name"), max_length=150)  # "Tahsildar Office, Tenali"
    name_te = models.CharField(_("name (Telugu)"), max_length=150, blank=True)
    phone = models.CharField(_("phone"), max_length=15)  # digits only, normalised
    phone_kind = models.CharField(_("phone kind"), max_length=10, choices=PhoneKind.choices)
    email = models.EmailField(_("email"), blank=True)
    area_level = models.CharField(_("area level"), max_length=10, choices=AreaLevel.choices)
    area_id = models.PositiveIntegerField(_("area id"))
    sort_order = models.PositiveSmallIntegerField(_("order"), default=100)
    consent_recorded = models.BooleanField(_("person agreed to publish this mobile"), default=False)
    verified_at = models.DateTimeField(_("last checked"), null=True, blank=True)
    is_active = models.BooleanField(_("shown"), default=True)  # removal is a soft delete
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("important number")
        verbose_name_plural = _("important numbers")
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["is_active", "area_level", "area_id"]),  # citizen read
            models.Index(fields=["type", "is_active"]),
            models.Index(fields=["phone"]),  # duplicate check
        ]
        permissions = [("manage_contact", "Add, edit and remove important numbers in own area")]

    def __str__(self):
        return f"{self.name} {self.phone}"

    @property
    def area(self) -> AreaRef:
        return AreaRef(self.area_level, self.area_id)

    def display_name(self, language: str | None = None) -> str:
        return (self.name_te or self.name) if wants_telugu(language) else self.name

    def label(self, language: str | None = None) -> str:
        """The group label: the admin's own type name for "Other" numbers, else the type's name."""
        return self.custom_type if self.type.is_other and self.custom_type else self.type.display_name(language)
