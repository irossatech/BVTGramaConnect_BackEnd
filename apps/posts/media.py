"""Photo uploads: check, re-encode as WebP (which drops EXIF), make a thumbnail.

- Accepts JPEG, PNG and WebP up to MAX_IMAGE_UPLOAD_MB.
- Turns the photo upright from its EXIF orientation, then drops all metadata.
- Large side at most POST_IMAGE_MAX_PX (1280); thumbnail at most POST_THUMB_PX (320).
"""

from __future__ import annotations

import io
import warnings

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import status as http

from grama.exceptions import ApiError

from .models import Post, PostMedia
from .services import EDITABLE, invalid_transition
from grama.exceptions import permission_denied

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}
#: Refuse images that would decompress to more than this many pixels.
MAX_PIXELS = 40_000_000


def _unsupported() -> ApiError:
    return ApiError(
        "unsupported_media",
        _("Upload a JPEG, PNG or WebP photo."),
        status=http.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _encode(image: Image.Image, max_px: int) -> tuple[bytes, int, int]:
    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, "WEBP", quality=settings.POST_IMAGE_QUALITY, method=4)
    return buffer.getvalue(), copy.width, copy.height


def process_image(upload) -> tuple[bytes, bytes, int, int]:
    """(large WebP, thumbnail WebP, width, height) for an uploaded file."""
    limit = settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024
    if upload.size > limit:
        raise ApiError(
            "file_too_large",
            _("Photos can be at most %(mb)d MB.") % {"mb": settings.MAX_IMAGE_UPLOAD_MB},
            status=http.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            if image.format not in ALLOWED_FORMATS:
                raise _unsupported()
            if image.width * image.height > MAX_PIXELS:
                raise _unsupported()
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _unsupported() from None

    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    large, width, height = _encode(image, settings.POST_IMAGE_MAX_PX)
    thumb, _w, _h = _encode(image, settings.POST_THUMB_PX)
    return large, thumb, width, height


def add_photo(user, post: Post, upload, focus_y=None) -> PostMedia:
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can add photos."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, "changed")
    if post.media.count() >= settings.MAX_PHOTOS_PER_POST:
        raise ApiError(
            "validation_error",
            _("A post can have at most %(n)d photos.") % {"n": settings.MAX_PHOTOS_PER_POST},
            fields={"file": [_("Too many photos.")]},
        )
    large, thumb, width, height = process_image(upload)
    with transaction.atomic():
        media = PostMedia(post=post, width=width, height=height, bytes=len(large), order=post.media.count())
        media.image.save(f"{media.id}-{settings.POST_IMAGE_MAX_PX}.webp", ContentFile(large), save=False)
        media.thumbnail.save(f"{media.id}-{settings.POST_THUMB_PX}.webp", ContentFile(thumb), save=False)
        media.save()
    return media


def remove_photo(user, post: Post, media: PostMedia) -> None:
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can remove photos."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, "changed")
    media.delete()
