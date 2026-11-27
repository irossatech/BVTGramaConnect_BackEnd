"""Photos and videos of hidden posts: out of the public folder, behind signed links (BVTGC-192, BE-SEC-208).

Every upload sits in MEDIA_ROOT, which Nginx (or, on a laptop or qa, Django) serves at /media/ to anyone
with the link. When a post is taken down (UNPUBLISHED) or discarded (DISCARDED, and the old BLOCKED), a link
seen before must stop working (decision of 2026-11-27). So:

- `sync_post` moves its files (photo in every size, video and poster) to PRIVATE_MEDIA_ROOT, which no web
  server serves, and deletes its WhatsApp preview picture (media/og/). When a reversal shows the post again
  (published, pending, recheck), the files move back and the old links work again. posts.services calls it
  after the status change is saved; the files carry the same name in both folders.
- `file_url` is the only place a post's file link is built for the API: the public /media/ link for a shown
  post, for a hidden one a signed link to `serve`, which works HIDDEN_MEDIA_LINK_SECONDS (an hour). Only
  someone the API already shows the post to (officials of its areas, the citizen's own phone) gets one; the
  link carries the file name and a signature, never personal data. A plain <img>/Image.network loads it, so
  the console and the app need no change.
- `reconcile`, run by the daily housekeeping, puts every file where its post's status says, so the first
  run after the deploy hides the files of posts taken down before, and a move a crash cut short is finished.

Portable: plain files and os.replace/shutil.move; no database feature. Files only ever move after the
status change is committed; a failed move is logged and left to `reconcile`.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.core.files.storage import FileSystemStorage, default_storage
from django.http import FileResponse, Http404
from django.urls import reverse

from .models import Post, PostMedia, PostStatus

logger = logging.getLogger("grama.media")

#: A post in one of these states shows to no citizen: its files are private.
HIDDEN = (PostStatus.UNPUBLISHED, PostStatus.DISCARDED, PostStatus.BLOCKED)
SALT = "grama.hidden-media"


def private_storage() -> FileSystemStorage:
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


def is_hidden(status: str) -> bool:
    return status in HIDDEN


def file_names(media: PostMedia) -> list[str]:
    return [field.name for field in (media.image, media.medium, media.thumbnail, media.video) if field]


# --- Links -------------------------------------------------------------------------------------------------


def file_url(context, status: str, name: str) -> str:
    """The full link to one of a post's files, as the API sends it: public while the post is shown, signed
    while it is hidden. `context` is a serializer context (its request makes the link absolute)."""
    if is_hidden(status):
        url = reverse("posts:hidden-media", kwargs={"token": signing.dumps(name, salt=SALT, compress=True)})
    else:
        url = default_storage.url(name)
    request = context.get("request") if context else None
    return request.build_absolute_uri(url) if request is not None else url


def serve(request, token: str):
    """GET /api/v1/media/hidden/<token>/: the file of a signed link, while the link is valid.

    A bad, altered or expired link answers 404, as an unknown file would. The file is looked for in the
    private folder first, then in the public one (a move not done yet). Never cached by a shared cache.
    """
    try:
        name = signing.loads(token, salt=SALT, max_age=settings.HIDDEN_MEDIA_LINK_SECONDS)
    except signing.BadSignature:  # also SignatureExpired
        raise Http404 from None
    if not isinstance(name, str) or not name.startswith("posts/") or ".." in Path(name).parts:
        raise Http404
    for storage in (private_storage(), default_storage):
        if storage.exists(name):
            response = FileResponse(storage.open(name, "rb"), content_type=mimetypes.guess_type(name)[0])
            response["Cache-Control"] = "private, max-age=600"
            response["X-Content-Type-Options"] = "nosniff"
            return response
    raise Http404


# --- Moving ------------------------------------------------------------------------------------------------


def _move(name: str, source: FileSystemStorage, target: FileSystemStorage) -> bool:
    """Move one file between the folders, keeping its name. True when it is in `target` afterwards."""
    if target.exists(name):
        if source.exists(name):  # left over from an interrupted move: the target copy wins
            source.delete(name)
        return True
    if not source.exists(name):
        return False
    destination = Path(target.path(name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source.path(name), destination)
    except OSError:  # another disk: copy, then delete
        shutil.move(source.path(name), destination)
    return True


def _drop_preview(post: Post) -> None:
    """The WhatsApp preview picture is made from the first photo; it must go too. Made again on next share."""
    if post.share_code:
        default_storage.delete(f"og/{post.share_code}.jpg")


def sync_post(post_id) -> int:
    """Put the post's files where its current status says. Returns how many files moved. Safe to repeat."""
    post = Post.objects.filter(pk=post_id).only("id", "status", "share_code").first()
    if post is None:
        return 0
    public, private = default_storage, private_storage()
    source, target = (public, private) if is_hidden(post.status) else (private, public)
    moved = 0
    for media in PostMedia.objects.filter(post_id=post.pk):
        for name in file_names(media):
            try:
                if source.exists(name) and _move(name, source, target):
                    moved += 1
            except OSError as error:
                logger.error("Could not move a file of post %s: %s", post.pk, type(error).__name__)
    if is_hidden(post.status):
        _drop_preview(post)
    return moved


def sync_after_commit(post_id) -> None:
    """Called by posts.services inside the transaction of a status change: move once it is saved."""
    from django.db import transaction

    def run():
        try:
            sync_post(post_id)
        except Exception:  # noqa: BLE001 - never fail the decision; reconcile finishes it
            logger.exception("Moving the files of post %s failed; the daily housekeeping retries.", post_id)

    transaction.on_commit(run)


def reconcile() -> int:
    """Every hidden post's files private, every shown post's files public. For the daily housekeeping."""
    moved = 0
    hidden_ids = Post.objects.filter(status__in=HIDDEN, media__isnull=False).values_list("pk", flat=True).distinct()
    for post_id in hidden_ids.iterator(chunk_size=500):
        moved += sync_post(post_id)
    # Files in the private folder whose post is shown again (a move back that did not finish).
    root = Path(settings.PRIVATE_MEDIA_ROOT)
    if root.is_dir():
        names = {
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        }
        if names:
            stray = set()
            for media in PostMedia.objects.exclude(post__status__in=HIDDEN).only(
                "post_id", "image", "medium", "thumbnail", "video"
            ).iterator(chunk_size=2000):
                if names.intersection(file_names(media)):
                    stray.add(media.post_id)
            for post_id in stray:
                moved += sync_post(post_id)
    return moved


def delete_files(media: PostMedia) -> None:
    """Delete a media row's files wherever they are (PostMedia.delete)."""
    private = private_storage()
    for name in file_names(media):
        default_storage.delete(name)
        private.delete(name)

