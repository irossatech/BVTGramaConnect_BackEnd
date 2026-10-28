"""Home banner ads: the only writer, and the citizen's query. docs/api-contract.md, section 17.

- Images: JPEG, PNG or WebP up to ADS_MAX_UPLOAD_MB and at least
  ADS_MIN_WIDTH_PX wide, turned upright and re-encoded by Pillow as WebP with
  the longest side at most ADS_IMAGE_MAX_PX. That drops EXIF and GPS and
  proves the file is an image. The uploaded name is never kept: the file is
  ads/<id>-<random>-1600.webp, new on every replacement, because phones may
  cache /media/ for days.
- Writes check the area, then save and audit in one transaction. A replaced
  or deleted image file is removed after the commit.
"""

from __future__ import annotations

import io
import secrets
import warnings

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.audit.services import record
from apps.geography.areas import AreaRef, ancestry, area_exists
from apps.geography.models import AreaLevel
from apps.posts.media import ALLOWED_FORMATS, MAX_PIXELS
from grama.exceptions import ApiError

from .models import Ad

#: Fields an ad's audit entry may list as changed.
FIELDS = ("title", "link", "area_level", "area_id", "starts_on", "ends_on", "is_active")


def _invalid(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


# --- Image -------------------------------------------------------------------------------------


def process_image(upload) -> bytes:
    """The upload as a WebP banner, or 400 on `image`."""
    if upload.size > settings.ADS_MAX_UPLOAD_MB * 1024 * 1024:
        raise _invalid("image", _("The image can be at most %(mb)d MB.") % {"mb": settings.ADS_MAX_UPLOAD_MB})
    not_an_image = _("Upload a JPEG, PNG or WebP image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            if image.format not in ALLOWED_FORMATS or image.width * image.height > MAX_PIXELS:
                raise _invalid("image", not_an_image)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _invalid("image", not_an_image) from None
    image = ImageOps.exif_transpose(image)
    if image.width < settings.ADS_MIN_WIDTH_PX:
        raise _invalid(
            "image", _("The image must be at least %(px)d pixels wide.") % {"px": settings.ADS_MIN_WIDTH_PX}
        )
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    image.thumbnail((settings.ADS_IMAGE_MAX_PX, settings.ADS_IMAGE_MAX_PX), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=settings.POST_IMAGE_QUALITY, method=4)
    return buffer.getvalue()


def _store_image(ad: Ad, data: bytes) -> None:
    ad.image.save(f"{ad.pk}-{secrets.token_hex(4)}-{settings.ADS_IMAGE_MAX_PX}.webp", ContentFile(data), save=False)


def _delete_file_after_commit(name: str) -> None:
    if name:
        storage = Ad._meta.get_field("image").storage
        transaction.on_commit(lambda: storage.delete(name))


# --- Checks --------------------------------------------------------------------------------------


def _check(values: dict) -> None:
    """Area exists and is active; dates in order. `values` holds the ad's final fields."""
    level, area_id = values.get("area_level"), values.get("area_id")
    if level not in AreaLevel.values or area_id is None or not area_exists(AreaRef(level, area_id), active_only=True):
        raise _invalid("area_id", _("No such active area."))
    starts, ends = values.get("starts_on"), values.get("ends_on")
    if starts and ends and ends < starts:
        raise _invalid("ends_on", _("The end date cannot be before the start date."))


# --- Writes ----------------------------------------------------------------------------------------


def create(user, values: dict, image) -> Ad:
    if image is None:
        raise _invalid("image", _("Choose a banner image."))
    _check(values)
    data = process_image(image)
    with transaction.atomic():
        ad = Ad.objects.create(created_by=user, image="", **{name: values[name] for name in FIELDS if name in values})
        _store_image(ad, data)
        ad.save(update_fields=["image"])
        record("ad.created", actor=user, target=ad, target_label=ad.title, area=ad.area, via="app")
    return ad


def update(user, ad: Ad, changes: dict, image=None) -> Ad:
    final = {name: changes.get(name, getattr(ad, name)) for name in FIELDS}
    _check(final)
    data = process_image(image) if image is not None else None
    changed = [name for name in FIELDS if name in changes and changes[name] != getattr(ad, name)]
    if data is None and not changed:
        return ad
    with transaction.atomic():
        for name in changed:
            setattr(ad, name, changes[name])
        old_image = None
        if data is not None:
            old_image = ad.image.name
            _store_image(ad, data)
            changed.append("image")
        ad.save()
        record("ad.updated", actor=user, target=ad, target_label=ad.title, area=ad.area, via="app",
               data={"changed": changed})
        _delete_file_after_commit(old_image)
    return ad


def delete(user, ad: Ad) -> None:
    with transaction.atomic():
        image, area, title, pk = ad.image.name, ad.area, ad.title, ad.pk
        ad.delete()
        record("ad.deleted", actor=user, target_label=title, area=area, via="app", data={"ad_id": pk})
        _delete_file_after_commit(image)


# --- Citizens ------------------------------------------------------------------------------------


def for_village(village_id: int) -> QuerySet:
    """Ads switched on, inside their dates today (India time), for the village or an area above it."""
    today = timezone.localdate()
    chain = Q(pk__in=[])
    for level, area_id in ancestry(AreaRef(AreaLevel.VILLAGE, village_id)).items():
        chain |= Q(area_level=level, area_id=area_id)
    return (
        Ad.objects.filter(chain, is_active=True)
        .filter(Q(starts_on__isnull=True) | Q(starts_on__lte=today))
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .order_by("-created_at", "-id")[: settings.ADS_PUBLIC_LIMIT]
    )
