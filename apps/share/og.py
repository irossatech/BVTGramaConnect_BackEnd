"""The preview picture WhatsApp and Facebook show for a shared post (og:image).

1200 x 630 JPEG under 300 KB (WhatsApp skips bigger pictures), made once from the post's first
photo, or from a video's poster frame (both are PostMedia.image), and kept in the media folder as
og/<share_code>.jpg, so Nginx serves it like every other upload. A text post uses the category's
fixed picture, static/share/og-<category>.jpg, or og-default.jpg, when one is there.
"""

from __future__ import annotations

import io
import logging

from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.templatetags.static import static
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger("grama.share")

WIDTH, HEIGHT = 1200, 630
MAX_BYTES = 300_000


def _encode(source) -> bytes:
    with Image.open(source) as image:
        picture = ImageOps.fit(image.convert("RGB"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    for quality in (82, 74, 66, 58, 50, 42):
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= MAX_BYTES:
            break
    return buffer.getvalue()


def _from_media(post, media) -> str | None:
    """Storage name of the post's preview, made now if it is missing; None if the photo cannot be read."""
    name = f"og/{post.share_code}.jpg"
    if default_storage.exists(name):
        return name
    try:
        with media.image.open("rb") as handle:
            data = _encode(handle)
    except (OSError, UnidentifiedImageError, ValueError):
        logger.warning("No preview picture for shared post %s: its first picture cannot be read.", post.pk)
        return None
    saved = default_storage.save(name, ContentFile(data))
    if saved != name:  # another request made it at the same moment; keep one
        default_storage.delete(saved)
    return name


def preview_url(post, media) -> str | None:
    """Site-relative URL of the post's preview picture, or None (a text post with no fixed picture)."""
    if media is not None:
        name = _from_media(post, media)
        return default_storage.url(name) if name else None
    for path in (f"share/og-{post.category.key}.jpg", "share/og-default.jpg"):
        if finders.find(path):
            return static(path)
    return None
