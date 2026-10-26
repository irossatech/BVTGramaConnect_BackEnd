"""State > District > Mandal > Village.

Each level has an English and a Telugu name. Villages carry latitude and
longitude as DecimalField(9, 6) for the nearest-village lookup. Everything
here is portable ORM: no spatial types and no raw SQL.
"""

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class AreaLevel(models.TextChoices):
    STATE = "STATE", _("State")
    DISTRICT = "DISTRICT", _("District")
    MANDAL = "MANDAL", _("Mandal")
    VILLAGE = "VILLAGE", _("Village")


def clean_name(value: str) -> str:
    """Collapse runs of whitespace and trim."""
    return " ".join((value or "").split())


def name_key(value: str) -> str:
    """Comparison key for names: whitespace collapsed, case folded."""
    return clean_name(value).casefold()


def wants_telugu(language: str | None = None) -> bool:
    return (language or get_language() or "te").lower().startswith("te")


class AreaQuerySet(models.QuerySet):
    #: Lookups from this model up to each ancestor's is_active flag.
    ancestor_active_lookups: tuple[str, ...] = ()

    def active(self):
        """Active rows whose ancestors are all active too."""
        filters = {"is_active": True}
        for lookup in self.ancestor_active_lookups:
            filters[lookup] = True
        return self.filter(**filters)

    def ordered_for_language(self, language: str | None = None):
        return self.order_by("name_te" if wants_telugu(language) else "name_en", "id")


class Area(models.Model):
    level: str = ""

    name_en = models.CharField(_("name (English)"), max_length=120)
    name_te = models.CharField(_("name (Telugu)"), max_length=120, blank=True)
    name_key = models.CharField(max_length=120, editable=False, db_index=True)
    lgd_code = models.CharField(
        _("LGD code"),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        help_text=_("Local Government Directory code, if known."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Inactive areas and everything below them are hidden from citizens."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["name_en"]

    def save(self, *args, **kwargs):
        self.name_en = clean_name(self.name_en)
        # A missing Telugu name copies the English one, so Telugu screens
        # always have something to show.
        self.name_te = clean_name(self.name_te) or self.name_en
        self.name_key = name_key(self.name_en)
        self.lgd_code = (self.lgd_code or "").strip() or None
        super().save(*args, **kwargs)

    def display_name(self, language: str | None = None) -> str:
        if wants_telugu(language):
            return self.name_te or self.name_en
        return self.name_en

    def __str__(self):
        return self.name_en


class State(Area):
    level = AreaLevel.STATE

    objects = AreaQuerySet.as_manager()

    class Meta(Area.Meta):
        verbose_name = _("state")
        verbose_name_plural = _("states")
        permissions = [("manage_master_data", "Add, edit and import districts, mandals and villages")]


class DistrictQuerySet(AreaQuerySet):
    ancestor_active_lookups = ("state__is_active",)


class District(Area):
    level = AreaLevel.DISTRICT

    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="districts")

    objects = DistrictQuerySet.as_manager()

    class Meta(Area.Meta):
        verbose_name = _("district")
        verbose_name_plural = _("districts")
        # The real guard against two admins adding the same name at once (the constraint is also the index).
        constraints = [models.UniqueConstraint(fields=["state", "name_key"], name="geo_district_name_unique_in_state")]


class MandalQuerySet(AreaQuerySet):
    ancestor_active_lookups = ("district__is_active", "district__state__is_active")


class Mandal(Area):
    level = AreaLevel.MANDAL

    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="mandals")

    objects = MandalQuerySet.as_manager()

    class Meta(Area.Meta):
        verbose_name = _("mandal")
        verbose_name_plural = _("mandals")
        constraints = [models.UniqueConstraint(fields=["district", "name_key"], name="geo_mandal_name_unique_in_district")]


class VillageQuerySet(AreaQuerySet):
    ancestor_active_lookups = (
        "mandal__is_active",
        "mandal__district__is_active",
        "mandal__district__state__is_active",
    )

    def with_coordinates(self):
        return self.filter(latitude__isnull=False, longitude__isnull=False)


class Village(Area):
    level = AreaLevel.VILLAGE

    mandal = models.ForeignKey(Mandal, on_delete=models.PROTECT, related_name="villages")
    latitude = models.DecimalField(
        _("latitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        _("longitude"),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    pin_code = models.CharField(
        _("PIN code"),
        max_length=6,
        blank=True,
        validators=[RegexValidator(r"^\d{6}$", _("A PIN code has 6 digits."))],
    )

    objects = VillageQuerySet.as_manager()

    class Meta(Area.Meta):
        verbose_name = _("village")
        verbose_name_plural = _("villages")
        indexes = [models.Index(fields=["latitude", "longitude"])]  # nearest-village search
        constraints = [models.UniqueConstraint(fields=["mandal", "name_key"], name="geo_village_name_unique_in_mandal")]

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


MODEL_BY_LEVEL = {
    AreaLevel.STATE: State,
    AreaLevel.DISTRICT: District,
    AreaLevel.MANDAL: Mandal,
    AreaLevel.VILLAGE: Village,
}
