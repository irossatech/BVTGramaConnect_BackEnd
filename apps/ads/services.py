"""Home banner ads: the only writer, and the citizen's query. docs/api-contract.md, section 17.

- Images: JPEG, PNG or WebP up to ADS_MAX_UPLOAD_MB and at least
  ADS_MIN_WIDTH_PX wide, turned upright and re-encoded by Pillow as WebP with
  the longest side at most ADS_IMAGE_MAX_PX. That drops EXIF and GPS and
  proves the file is an image. The uploaded name is never kept: the file is
  ads/<id>-<random>-1600.webp, new on every replacement, because phones may
  cache /media/ for days.
- Videos: MP4 or MOV up to ADS_VIDEO_MAX_MB and ADS_VIDEO_MAX_SECONDS,
  checked and measured by the post video code (apps.posts.media), stored as
  uploaded under ads/<id>-<random>.<mp4|mov>. The poster frame, cut by
  ffmpeg (or a plain placeholder without it), becomes the ad's `image`, so
  apps that only read `image` show it as a picture banner. An ad has a
  picture or a video: a new one of either replaces the other.
- Writes check the area and the files first (ffprobe and ffmpeg never run
  inside a transaction), then save and audit in one transaction. Replaced or
  deleted files are removed after the commit.
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
from apps.posts.media import ALLOWED_FORMATS, MAX_PIXELS, PreparedVideo, prepare_video
from grama.exceptions import ApiError

from .models import Ad

#: Fields an ad's audit entry may list as changed.
FIELDS = ("title", "link", "area_level", "area_id", "starts_on", "ends_on", "is_active")


def _invalid(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


# --- Image -------------------------------------------------------------------------------------


def process_image(upload, *, min_width: bool = True) -> bytes:
    """The upload as a WebP banner, or 400 on `image`. A video's poster skips the width check
    (min_width=False): a portrait or 480p phone video gives a narrower frame."""
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
    if min_width and image.width < settings.ADS_MIN_WIDTH_PX:
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


# --- Video -------------------------------------------------------------------------------------


def prepare(video, duration_s=None) -> tuple[PreparedVideo, bytes]:
    """The checked video and its poster as banner WebP. Runs ffprobe and ffmpeg: call outside a transaction."""
    prepared = prepare_video(
        video,
        duration_s=duration_s,
        lenient=True,
        max_mb=settings.ADS_VIDEO_MAX_MB,
        max_seconds=settings.ADS_VIDEO_MAX_SECONDS,
        process_poster=False,
    )
    return prepared, process_image(prepared.poster_source, min_width=False)


def _store_video(ad: Ad, prepared: PreparedVideo, poster: bytes) -> None:
    upload = prepared.upload
    upload.seek(0)
    ad.video.save(f"{ad.pk}-{secrets.token_hex(4)}.{prepared.container}", upload, save=False)
    ad.duration_s = max(1, round(prepared.duration)) if prepared.duration is not None else None
    _store_image(ad, poster)


def _files(image, video, duration_s) -> tuple[bytes | None, tuple | None]:
    """The processed picture or video, at most one of them."""
    if image is not None and video is not None:
        raise _invalid("video", _("Send a picture or a video, not both."))
    if video is not None:
        return None, prepare(video, duration_s)
    return (process_image(image) if image is not None else None), None


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


def create(user, values: dict, image=None, video=None, duration_s=None) -> Ad:
    if image is None and video is None:
        raise _invalid("image", _("Choose a banner image or video."))
    _check(values)
    data, prepared = _files(image, video, duration_s)
    with transaction.atomic():
        ad = Ad.objects.create(created_by=user, image="", **{name: values[name] for name in FIELDS if name in values})
        if prepared is not None:
            _store_video(ad, *prepared)
        else:
            _store_image(ad, data)
        ad.save(update_fields=["image", "video", "duration_s"])
        record("ad.created", actor=user, target=ad, target_label=ad.title, area=ad.area, via="app",
               data={"kind": "video" if prepared is not None else "image"})
    return ad


def update(user, ad: Ad, changes: dict, image=None, video=None, duration_s=None) -> Ad:
    final = {name: changes.get(name, getattr(ad, name)) for name in FIELDS}
    _check(final)
    data, prepared = _files(image, video, duration_s)
    changed = [name for name in FIELDS if name in changes and changes[name] != getattr(ad, name)]
    if data is None and prepared is None and not changed:
        return ad
    with transaction.atomic():
        for name in changed:
            setattr(ad, name, changes[name])
        old_files = []
        if prepared is not None:
            old_files = [ad.image.name, ad.video.name]
            _store_video(ad, *prepared)
            changed += ["image", "video"]
        elif data is not None:
            old_files = [ad.image.name, ad.video.name]
            _store_image(ad, data)
            changed.append("image")
            if ad.video:
                # A new picture turns a video ad back into a picture ad.
                ad.video, ad.duration_s = "", None
                changed.append("video")
        ad.save()
        record("ad.updated", actor=user, target=ad, target_label=ad.title, area=ad.area, via="app",
               data={"changed": changed})
        for name in old_files:
            _delete_file_after_commit(name)
    return ad


def delete(user, ad: Ad) -> None:
    with transaction.atomic():
        files, area, title, pk = [ad.image.name, ad.video.name], ad.area, ad.title, ad.pk
        ad.delete()
        record("ad.deleted", actor=user, target_label=title, area=area, via="app", data={"ad_id": pk})
        for name in files:
            _delete_file_after_commit(name)


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
