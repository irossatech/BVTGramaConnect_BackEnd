"""Banner ads on the citizen's Home page. docs/api-contract.md, section 17.

The Super Admin makes an ad for one area, stored like a contact's area:
STATE means every village. A citizen sees the ads that are switched on,
inside their dates (both days included, India time) and meant for their
village or an area above it.
"""

from django.conf import settings
from django.db import models

from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel


def ad_image_path(instance, filename):
    return f"ads/{filename}"


def ad_video_path(instance, filename):
    return f"ads/{filename}"


class Ad(models.Model):
    title = models.CharField(max_length=80)
    #: Always a WebP the server made (longest side ADS_IMAGE_MAX_PX), never the uploaded file.
    #: A picture ad's picture, or a video ad's poster frame.
    image = models.FileField(upload_to=ad_image_path, max_length=200)
    #: MP4 or MOV as uploaded; empty for a picture ad.
    video = models.FileField(upload_to=ad_video_path, max_length=200, blank=True, default="")
    #: Whole seconds; null for a picture ad or when the length is unknown.
    duration_s = models.PositiveSmallIntegerField(null=True, blank=True)
    link = models.URLField(max_length=500, blank=True)
    area_level = models.CharField(max_length=10, choices=AreaLevel.choices)
    area_id = models.PositiveIntegerField()
    #: Null: from now on. Null ends_on: no end. Both days are included.
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["is_active", "area_level", "area_id"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True)
                | models.Q(starts_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="ad_ends_on_after_starts_on",
            ),
        ]
        # Platform-wide: only the Super Admin holds it (accounts/role_matrix.py).
        permissions = [("manage_ad", "Create, change and delete Home banner ads")]

    def __str__(self):
        return self.title

    @property
    def area(self) -> AreaRef:
        return AreaRef(self.area_level, self.area_id)
