"""Photo and video uploads for posts.

Photos: JPEG, PNG and WebP up to MAX_IMAGE_UPLOAD_MB, turned upright from their
EXIF orientation, re-encoded as WebP (which drops all metadata), large side at
most POST_IMAGE_MAX_PX (1280), plus a POST_MEDIUM_PX (640) copy for big cards
and a POST_THUMB_PX (320) thumbnail.

Videos (owner decision Q8): MP4 or MOV up to MAX_VIDEO_UPLOAD_MB and
VIDEO_MAX_SECONDS, one per post and never together with photos. The file is
stored as uploaded; the app compresses it first. With ffprobe and ffmpeg on
PATH (the server installs them) the server measures the length and cuts a
poster frame. Without them the app must send `duration_s` and a `poster` photo.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import warnings
from contextlib import contextmanager

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import status as http

from grama.exceptions import ApiError, permission_denied

from .models import MediaKind, Post, PostMedia
from .services import EDITABLE, invalid_transition

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}
#: Refuse images that would decompress to more than this many pixels.
MAX_PIXELS = 40_000_000
#: ISO base media files (MP4, MOV) carry "ftyp" at bytes 4 to 8.
VIDEO_EXTENSIONS = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov"}
#: A little slack for players that round 60.2 s up.
DURATION_SLACK_S = 0.5


def _unsupported(message=None) -> ApiError:
    return ApiError(
        "unsupported_media",
        message or _("Upload a JPEG, PNG or WebP photo."),
        status=http.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _too_large(mb: int, what: str) -> ApiError:
    return ApiError(
        "file_too_large",
        _("%(what)s can be at most %(mb)d MB.") % {"what": what, "mb": mb},
        status=http.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


def _media_conflict(message) -> ApiError:
    return ApiError("media_conflict", message, status=http.HTTP_409_CONFLICT)


def _encode(image: Image.Image, max_px: int) -> tuple[bytes, int, int]:
    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, "WEBP", quality=settings.POST_IMAGE_QUALITY, method=4)
    return buffer.getvalue(), copy.width, copy.height


def process_image(upload) -> tuple[bytes, bytes, bytes, int, int]:
    """(large WebP, medium WebP, thumbnail WebP, width, height) for an uploaded photo."""
    if upload.size > settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024:
        raise _too_large(settings.MAX_IMAGE_UPLOAD_MB, _("Photos"))
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
    medium, _w, _h = _encode(image, settings.POST_MEDIUM_PX)
    thumb, _w, _h = _encode(image, settings.POST_THUMB_PX)
    return large, medium, thumb, width, height


def add_medium(media: PostMedia) -> bool:
    """Make the POST_MEDIUM_PX file from the stored large one (backfill_medium_images).

    False when the large file is missing or unreadable; the row then keeps no medium file.
    """
    try:
        with media.image.open("rb") as handle, Image.open(handle) as stored:
            stored.load()
            image = stored.convert("RGBA" if stored.mode in ("RGBA", "LA") else "RGB")
    except (OSError, UnidentifiedImageError, ValueError):
        return False
    medium, _w, _h = _encode(image, settings.POST_MEDIUM_PX)
    prefix = "poster-" if media.kind == MediaKind.VIDEO else ""
    media.medium.save(f"{media.id}-{prefix}{settings.POST_MEDIUM_PX}.webp", ContentFile(medium), save=False)
    PostMedia.objects.filter(pk=media.pk).update(medium=media.medium.name)
    return True


def _check_can_add(user, post: Post) -> None:
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can add photos or a video."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, "changed")


def add_photo(user, post: Post, upload, focus_y=None) -> PostMedia:
    _check_can_add(user, post)
    if post.media.filter(kind=MediaKind.VIDEO).exists():
        raise _media_conflict(_("This post has a video. A post has either photos or one video."))
    if post.media.count() >= settings.MAX_PHOTOS_PER_POST:
        raise ApiError(
            "validation_error",
            _("A post can have at most %(n)d photos.") % {"n": settings.MAX_PHOTOS_PER_POST},
            fields={"file": [_("Too many photos.")]},
        )
    processed = process_image(upload)
    with transaction.atomic():
        return store_photo(post, processed, order=post.media.count())


def store_photo(post: Post, processed: tuple, order: int) -> PostMedia:
    """Save a process_image() result as the post's photo number `order`. Checks are the caller's."""
    large, medium, thumb, width, height = processed
    media = PostMedia(post=post, width=width, height=height, bytes=len(large), order=order)
    media.image.save(f"{media.id}-{settings.POST_IMAGE_MAX_PX}.webp", ContentFile(large), save=False)
    media.medium.save(f"{media.id}-{settings.POST_MEDIUM_PX}.webp", ContentFile(medium), save=False)
    media.thumbnail.save(f"{media.id}-{settings.POST_THUMB_PX}.webp", ContentFile(thumb), save=False)
    media.save()
    return media


# --- Video ------------------------------------------------------------------------------


@contextmanager
def _local_path(upload, suffix: str):
    """A filesystem path for the upload, for ffprobe and ffmpeg."""
    path = getattr(upload, "temporary_file_path", None)
    if callable(path):
        yield path()
        return
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in upload.chunks():
            handle.write(chunk)
        handle.close()
        yield handle.name
    finally:
        handle.close()
        os.unlink(handle.name)
        upload.seek(0)


def probe_duration(path: str) -> float | None:
    """Length in seconds from ffprobe, or None when ffprobe is not installed.

    Raises unsupported_media when ffprobe is there but cannot read the file.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise _unsupported(_("This video could not be read. Upload an MP4 or MOV file.")) from None


def extract_poster(path: str, at_seconds: float) -> bytes | None:
    """A PNG frame from the video, or None when ffmpeg is not installed."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", f"{at_seconds:.2f}", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    return result.stdout or None


def _sniff_video(upload) -> str:
    """'mp4' or 'mov' from the name and the file's first bytes, else 415."""
    extension = os.path.splitext(upload.name or "")[1].lower()
    head = upload.read(12)
    upload.seek(0)
    if extension not in VIDEO_EXTENSIONS or len(head) < 12 or head[4:8] != b"ftyp":
        raise _unsupported(_("Upload an MP4 or MOV video."))
    return VIDEO_EXTENSIONS[extension]


def add_video(user, post: Post, upload, *, duration_s=None, poster=None) -> PostMedia:
    if not settings.VIDEO_POSTS_ENABLED:
        raise permission_denied(_("Video posts are switched off."))
    _check_can_add(user, post)
    if post.media.exists():
        raise _media_conflict(_("A post has either photos or one video."))
    if upload.size > settings.MAX_VIDEO_UPLOAD_MB * 1024 * 1024:
        raise _too_large(settings.MAX_VIDEO_UPLOAD_MB, _("Videos"))
    container = _sniff_video(upload)

    with _local_path(upload, f".{container}") as path:
        measured = probe_duration(path)
        duration = measured if measured is not None else duration_s
        if duration is None:
            raise ApiError(
                "validation_error",
                _("Send the video length in seconds."),
                fields={"duration_s": [_("Required when the server cannot measure the video.")]},
            )
        if float(duration) > settings.VIDEO_MAX_SECONDS + DURATION_SLACK_S:
            raise ApiError(
                "video_too_long",
                _("Videos can be at most %(s)d seconds.") % {"s": settings.VIDEO_MAX_SECONDS},
                fields={"file": [_("Too long.")]},
            )
        frame = extract_poster(path, min(1.0, float(duration) / 2))

    if frame is not None:
        poster_file = ContentFile(frame, name="poster.png")
    elif poster is not None:
        poster_file = poster
    else:
        raise ApiError(
            "validation_error",
            _("Send a poster photo for the video."),
            fields={"poster": [_("Required when the server cannot make a poster.")]},
        )
    large, medium, thumb, width, height = process_image(poster_file)

    with transaction.atomic():
        media = PostMedia(
            post=post,
            kind=MediaKind.VIDEO,
            width=width,
            height=height,
            bytes=upload.size,
            duration_s=max(1, round(float(duration))),
            order=0,
        )
        upload.seek(0)
        media.video.save(f"{media.id}.{container}", upload, save=False)
        media.image.save(f"{media.id}-poster-{settings.POST_IMAGE_MAX_PX}.webp", ContentFile(large), save=False)
        media.medium.save(f"{media.id}-poster-{settings.POST_MEDIUM_PX}.webp", ContentFile(medium), save=False)
        media.thumbnail.save(f"{media.id}-poster-{settings.POST_THUMB_PX}.webp", ContentFile(thumb), save=False)
        media.save()
    return media


def remove_photo(user, post: Post, media: PostMedia) -> None:
    """Remove a photo or the video."""
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can remove photos or a video."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, "changed")
    media.delete()
