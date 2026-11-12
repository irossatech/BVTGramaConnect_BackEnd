"""Photo and video uploads for posts.

Photos: JPEG, PNG and WebP up to MAX_IMAGE_UPLOAD_MB, turned upright from their
EXIF orientation, re-encoded as WebP (which drops all metadata), large side at
most POST_IMAGE_MAX_PX (1280), plus a POST_MEDIUM_PX (640) copy for big cards
and a POST_THUMB_PX (320) thumbnail.

Videos (owner decision Q8): MP4 or MOV up to MAX_VIDEO_UPLOAD_MB and
VIDEO_MAX_SECONDS, one per post and never together with photos. The app
compresses it first. With ffprobe and ffmpeg on PATH (the server installs
them) the server measures the length, cuts a poster frame and stores a copy
without metadata (strip_metadata, G7). Without them the app must send
`duration_s` and a `poster` photo, and the file is stored as uploaded.

Time limits (audit BE-PERF-003): uploads are checked inside the request, so citizens get an
answer at once, but never for long. Each ffprobe/ffmpeg step stops after VIDEO_*_TIMEOUT_S and
answers 415, and all photos and videos of one request share MEDIA_PROCESSING_BUDGET_S (see
media_budget); a request over it answers 400 and stores nothing. Normal videos take a few
seconds: the streams are copied, never re-encoded.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile, File
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


def _unreadable_video() -> ApiError:
    return _unsupported(_("This video could not be read. Upload an MP4 or MOV file."))


# --- Time budget ------------------------------------------------------------------------


class MediaBudget:
    """The time one request may spend checking its photos and videos (MEDIA_PROCESSING_BUDGET_S).

    `field` is the form field a refusal names: "videos" for citizen posts, "file" for an
    official's upload, "video" for a banner ad.
    """

    def __init__(self, field: str = "file", seconds: float | None = None):
        self.field = field
        self.deadline = time.monotonic() + (seconds if seconds is not None else settings.MEDIA_PROCESSING_BUDGET_S)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def exceeded(self) -> ApiError:
        return ApiError(
            "validation_error",
            _("These files took too long to check. Send fewer or shorter videos in one post."),
            fields={self.field: [_("Took too long to check.")]},
        )

    def check(self) -> None:
        if self.remaining() <= 0:
            raise self.exceeded()


#: The budget of the request being handled; read by every step, so the steps keep their arguments.
_budget: ContextVar[MediaBudget | None] = ContextVar("media_budget", default=None)


@contextmanager
def media_budget(field: str = "file"):
    """Give the code inside one MediaBudget. Nested calls share the outer one, so a citizen post
    with many files has one budget for all of them."""
    current = _budget.get()
    if current is not None:
        yield current
        return
    budget = MediaBudget(field)
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def check_budget() -> None:
    """Refuse when the current request has used up its budget; nothing to do outside one."""
    budget = _budget.get()
    if budget is not None:
        budget.check()


def _run_tool(command: list[str], step_limit: float, **kwargs) -> subprocess.CompletedProcess:
    """Run ffprobe or ffmpeg for at most `step_limit` seconds, or what is left of the budget.

    Running out of the step's own time: 415, the file is treated as unreadable. Running out of
    the request's budget: 400 from MediaBudget.exceeded. Either way subprocess stops the tool.
    """
    budget = _budget.get()
    timeout = step_limit
    if budget is not None:
        budget.check()
        timeout = min(step_limit, budget.remaining())
    try:
        return subprocess.run(command, timeout=timeout, check=False, **kwargs)
    except subprocess.TimeoutExpired:
        if budget is not None and timeout < step_limit:
            raise budget.exceeded() from None
        raise _unreadable_video() from None


def _encode(image: Image.Image, max_px: int) -> tuple[bytes, int, int]:
    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, "WEBP", quality=settings.POST_IMAGE_QUALITY, method=4)
    return buffer.getvalue(), copy.width, copy.height


def process_image(upload) -> tuple[bytes, bytes, bytes, int, int]:
    """(large WebP, medium WebP, thumbnail WebP, width, height) for an uploaded photo."""
    check_budget()
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
    result = _run_tool(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        settings.VIDEO_PROBE_TIMEOUT_S,
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise _unreadable_video() from None


def extract_poster(path: str, at_seconds: float) -> bytes | None:
    """A PNG frame from the video, or None when ffmpeg is not installed."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    result = _run_tool(
        [ffmpeg, "-v", "error", "-ss", f"{at_seconds:.2f}", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        settings.VIDEO_POSTER_TIMEOUT_S,
        capture_output=True,
    )
    return result.stdout or None


def strip_metadata(path: str, container: str) -> File | None:
    """The video without its metadata (GPS place, phone model, dates), or None when ffmpeg is not installed.

    Security gap G7, owner decision of 2026-11-03. The streams are copied, not re-encoded, so it
    takes a fraction of a second; data streams (where some phones keep a GPS track) and
    chapters are dropped, and the index moves to the front so playback starts sooner. Raises
    unsupported_media when ffmpeg is there but cannot copy the file: a video is never kept
    with its metadata when the server could have removed it.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    handle, out = tempfile.mkstemp(suffix=f".{container}")
    os.close(handle)
    try:
        result = _run_tool(
            [ffmpeg, "-v", "error", "-y", "-i", path, "-map", "0:v", "-map", "0:a?", "-c", "copy",
             "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart", "-f", container, out],
            settings.VIDEO_STRIP_TIMEOUT_S,
            capture_output=True,
        )
        if result.returncode != 0 or os.path.getsize(out) == 0:
            raise _unreadable_video()
        # An anonymous temporary file goes away by itself when it is closed or collected.
        copy = tempfile.TemporaryFile()
        with open(out, "rb") as stripped:
            shutil.copyfileobj(stripped, copy)
        size = copy.tell()
        copy.seek(0)
    finally:
        os.unlink(out)
    file = File(copy, name=f"video.{container}")
    file.size = size
    return file


def _sniff_video(upload) -> str:
    """'mp4' or 'mov' from the name and the file's first bytes, else 415."""
    extension = os.path.splitext(upload.name or "")[1].lower()
    head = upload.read(12)
    upload.seek(0)
    if extension not in VIDEO_EXTENSIONS or len(head) < 12 or head[4:8] != b"ftyp":
        raise _unsupported(_("Upload an MP4 or MOV video."))
    return VIDEO_EXTENSIONS[extension]


@dataclass
class PreparedVideo:
    """A checked video, ready to store: its container, length (None if unknown), processed poster
    (None with process_poster=False) and the poster picture as cut or sent, for callers with their own sizes."""

    upload: object
    container: str
    duration: float | None
    poster: tuple | None
    poster_source: ContentFile | None = None


def _placeholder_poster() -> ContentFile:
    """A plain 16:9 picture for a video whose frame the server cannot cut (no ffmpeg) and the phone did not send."""
    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), (38, 50, 56)).save(buffer, "PNG")
    return ContentFile(buffer.getvalue(), name="placeholder.png")


def prepare_video(
    upload,
    *,
    duration_s=None,
    poster=None,
    lenient: bool = False,
    max_mb: int | None = None,
    max_seconds: int | None = None,
    process_poster: bool = True,
) -> PreparedVideo:
    """Check a video upload and make its poster. Writes nothing.

    Officials (lenient=False): without ffprobe and ffmpeg the phone must send
    duration_s and a poster. Citizens (lenient=True, contract section 15): the
    length may stay unknown and a placeholder poster is used instead.
    max_mb and max_seconds default to the post limits (None); banner ads pass their own, and
    max_seconds=0 means any length (owner decision of 2026-11-02 for banners).
    Runs inside the request's media_budget, or a budget of its own when called alone.
    """
    with media_budget():
        return _prepare_video(
            upload, duration_s=duration_s, poster=poster, lenient=lenient,
            max_mb=max_mb, max_seconds=max_seconds, process_poster=process_poster,
        )


def _prepare_video(upload, *, duration_s, poster, lenient, max_mb, max_seconds, process_poster) -> PreparedVideo:
    max_mb = max_mb if max_mb is not None else settings.MAX_VIDEO_UPLOAD_MB
    max_seconds = max_seconds if max_seconds is not None else settings.VIDEO_MAX_SECONDS
    if upload.size > max_mb * 1024 * 1024:
        raise _too_large(max_mb, _("Videos"))
    container = _sniff_video(upload)

    with _local_path(upload, f".{container}") as path:
        measured = probe_duration(path)
        duration = measured if measured is not None else duration_s
        if duration is None and not lenient:
            raise ApiError(
                "validation_error",
                _("Send the video length in seconds."),
                fields={"duration_s": [_("Required when the server cannot measure the video.")]},
            )
        if max_seconds and duration is not None and float(duration) > max_seconds + DURATION_SLACK_S:
            raise ApiError(
                "video_too_long",
                _("Videos can be at most %(s)d seconds.") % {"s": max_seconds},
                fields={"file": [_("Too long.")]},
            )
        frame = extract_poster(path, min(1.0, float(duration) / 2) if duration is not None else 1.0)
        stripped = strip_metadata(path, container)

    if stripped is not None:
        upload = stripped
    if frame is not None:
        poster_file = ContentFile(frame, name="poster.png")
    elif poster is not None:
        poster_file = poster
    elif lenient:
        poster_file = _placeholder_poster()
    else:
        raise ApiError(
            "validation_error",
            _("Send a poster photo for the video."),
            fields={"poster": [_("Required when the server cannot make a poster.")]},
        )
    return PreparedVideo(
        upload,
        container,
        float(duration) if duration is not None else None,
        process_image(poster_file) if process_poster else None,
        poster_file,
    )


def store_video(post: Post, prepared: PreparedVideo, order: int) -> PostMedia:
    """Save a prepare_video() result as the post's media number `order`. Checks are the caller's."""
    large, medium, thumb, width, height = prepared.poster
    upload = prepared.upload
    media = PostMedia(
        post=post,
        kind=MediaKind.VIDEO,
        width=width,
        height=height,
        bytes=upload.size,
        duration_s=max(1, round(prepared.duration)) if prepared.duration is not None else None,
        order=order,
    )
    upload.seek(0)
    media.video.save(f"{media.id}.{prepared.container}", upload, save=False)
    media.image.save(f"{media.id}-poster-{settings.POST_IMAGE_MAX_PX}.webp", ContentFile(large), save=False)
    media.medium.save(f"{media.id}-poster-{settings.POST_MEDIUM_PX}.webp", ContentFile(medium), save=False)
    media.thumbnail.save(f"{media.id}-poster-{settings.POST_THUMB_PX}.webp", ContentFile(thumb), save=False)
    media.save()
    return media


def add_video(user, post: Post, upload, *, duration_s=None, poster=None) -> PostMedia:
    if not settings.VIDEO_POSTS_ENABLED:
        raise permission_denied(_("Video posts are switched off."))
    _check_can_add(user, post)
    if post.media.exists():
        raise _media_conflict(_("A post has either photos or one video."))
    prepared = prepare_video(upload, duration_s=duration_s, poster=poster)
    with transaction.atomic():
        return store_video(post, prepared, order=0)


def remove_photo(user, post: Post, media: PostMedia) -> None:
    """Remove a photo or the video."""
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can remove photos or a video."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, "changed")
    media.delete()
