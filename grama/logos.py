"""Uploaded logos for lists the Super Admin manages on Home: services and bills.

    data = process_logo(upload)                 # bytes of a WebP, or 400 on `logo`
    store_logo(service.logo, service.key, data)  # services/<key>-<random>-256.webp
    delete_after_commit(old_name, Service._meta.get_field("logo").storage)

JPEG, PNG or WebP only, checked by Pillow from the content, never the extension; decompression
bombs refused. At most SERVICE_LOGO_MAX_UPLOAD_MB and at least SERVICE_LOGO_MIN_PX a side.
Re-encoded as WebP with the longest side at most SERVICE_LOGO_PX, transparency kept and EXIF
gone. The file name gets a new random part on every replacement, because phones cache /media/.
"""

from __future__ import annotations

import io
import secrets
import warnings

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from grama.exceptions import ApiError


def _logo_error(message) -> ApiError:
    return ApiError("validation_error", message, fields={"logo": [message]})


def process_logo(upload) -> bytes:
    """The upload as a WebP logo (transparency kept), or 400 on `logo`."""
    from apps.posts.media import ALLOWED_FORMATS, MAX_PIXELS

    if upload.size > settings.SERVICE_LOGO_MAX_UPLOAD_MB * 1024 * 1024:
        raise _logo_error(_("The logo can be at most %(mb)d MB.") % {"mb": settings.SERVICE_LOGO_MAX_UPLOAD_MB})
    not_an_image = _("Upload a JPEG, PNG or WebP image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            if image.format not in ALLOWED_FORMATS or image.width * image.height > MAX_PIXELS:
                raise _logo_error(not_an_image)
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _logo_error(not_an_image) from None
    image = ImageOps.exif_transpose(image)
    if min(image.width, image.height) < settings.SERVICE_LOGO_MIN_PX:
        raise _logo_error(
            _("The logo must be at least %(px)d pixels on each side.") % {"px": settings.SERVICE_LOGO_MIN_PX}
        )
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    image.thumbnail((settings.SERVICE_LOGO_PX, settings.SERVICE_LOGO_PX), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=settings.POST_IMAGE_QUALITY, method=4)
    return buffer.getvalue()


def store_logo(field_file, key: str, data: bytes) -> None:
    """Put the WebP on the model's FileField (its upload_to picks the folder); the caller saves the row."""
    field_file.save(f"{key}-{secrets.token_hex(4)}-{settings.SERVICE_LOGO_PX}.webp", ContentFile(data), save=False)


def delete_after_commit(name: str, storage) -> None:
    """Delete a replaced or removed logo once the change is saved, never before."""
    if name:
        transaction.on_commit(lambda: storage.delete(name))
