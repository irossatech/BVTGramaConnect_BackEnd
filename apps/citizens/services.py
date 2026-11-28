"""Citizen phones: posting tokens, registration, posting and blocking. docs/api-contract.md, section 15.

- A token is issued once per install, after Google Play Integrity confirms our
  genuine app on a real phone (integrity.py). CITIZEN_REGISTRATIONS_PER_IP_PER_DAY
  limits new phones per IP address only when set (0, the default, is no limit).
  Only the token's SHA-256 hash is stored; the IP address only as a keyed hash.
- One phone may have had several installs, so several tokens. They are linked
  by a keyed hash of Android's app-scoped device ID (same_phone), which survives
  a reinstall: the daily limit and a block apply to the phone, not the install.
- Posts go through posts.services.create_citizen_post: straight to PENDING
  for every reviewer of the phone's village, at most CITIZEN_POSTS_PER_DAY a
  day (a runtime setting the Super Admin changes; 0 is no limit).
- Only the Super Admin (posts.block_citizen) blocks a phone, for a number of
  days chosen each time, and may discard all its pending posts at once. The
  citizen sees the reason and the end of the block. A block stops new posts
  and service requests only; it changes no post (owner's request of
  2026-11-21). Every block is kept as a CitizenBlock row with how it ended,
  for the Super Admin's blocked citizens list.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import serializers as drf_serializers
from rest_framework import status as http

from apps.accounts import authz
from apps.audit.services import record
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from apps.posts import media, services as post_services
from apps.posts.models import Post, PostStatus
from grama.exceptions import ApiError, out_of_area, permission_denied

from . import integrity
from .models import CitizenBlock, CitizenDevice

logger = logging.getLogger("grama.citizens")

TOKEN_HEADER = "HTTP_X_CITIZEN_TOKEN"
DAY = timedelta(hours=24)
#: How long the app is told to wait when Google's Play Integrity service cannot be reached.
INTEGRITY_RETRY_SECONDS = 60


# --- Errors -----------------------------------------------------------------------------------


def token_invalid() -> ApiError:
    return ApiError(
        "citizen_token_invalid",
        _("This phone is not registered for posting. Register again."),
        status=http.HTTP_401_UNAUTHORIZED,
    )


def throttled(message, seconds: int) -> ApiError:
    error = ApiError("throttled", message, status=http.HTTP_429_TOO_MANY_REQUESTS, retry_after_s=seconds)
    error.wait = seconds  # DRF turns this into the Retry-After header
    return error


def blocked_error(block_row: CitizenDevice) -> ApiError:
    """403 citizen_blocked with the reason and the end of the block, for the app to show."""
    until = drf_serializers.DateTimeField().to_representation(block_row.blocked_until) if block_row.blocked_until else None
    return ApiError(
        "citizen_blocked",
        _("This phone is blocked from posting. Reason: %(reason)s") % {"reason": block_row.block_reason},
        status=http.HTTP_403_FORBIDDEN,
        reason=block_row.block_reason,
        blocked_until=until,
    )


def _validation(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


# --- Tokens and phones ------------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def phone_hash_key() -> bytes:
    """PHONE_HASH_KEY, or SECRET_KEY while it is empty (BE-SEC-109, BVTGC-111). Its own setting, so that
    changing SECRET_KEY does not make every phone look new: blocks and daily limits survive a reinstall
    only while the Android ID hashes match."""
    return (settings.PHONE_HASH_KEY or settings.SECRET_KEY).encode()


def _keyed_hash(kind: bytes, value: str | None) -> str:
    if not value:
        return ""
    return hmac.new(phone_hash_key(), kind + b":" + value.encode(), hashlib.sha256).hexdigest()


def hash_ip(ip: str | None) -> str:
    return _keyed_hash(b"citizen-ip", ip)


def hash_android_id(android_id: str | None) -> str:
    return _keyed_hash(b"citizen-android-id", (android_id or "").strip().lower())


def device_from_request(request) -> CitizenDevice:
    """The phone behind X-Citizen-Token, or 401 citizen_token_invalid."""
    token = (request.META.get(TOKEN_HEADER) or "").strip()
    if not token:
        raise token_invalid()
    device = CitizenDevice.objects.filter(token_hash=hash_token(token)).first()
    if device is None:
        raise token_invalid()
    return device


def same_phone(device: CitizenDevice):
    """Every install of this phone: the same row, plus rows with the same Android device ID."""
    query = Q(pk=device.pk)
    if device.android_id_hash:
        query |= Q(android_id_hash=device.android_id_hash)
    return CitizenDevice.objects.filter(query)


def active_block(device: CitizenDevice, now=None) -> CitizenDevice | None:
    """The running block on this phone (any of its installs), or None. The longest one wins; a block
    without an end date (older blocks, or one set in Django admin) is the longest, on every database."""
    now = now or timezone.now()
    return (
        same_phone(device)
        .filter(blocked_at__isnull=False)
        .filter(Q(blocked_until__isnull=True) | Q(blocked_until__gt=now))
        .order_by(F("blocked_until").desc(nulls_first=True), "pk")
        .first()
    )


def phone_key(device: CitizenDevice) -> str:
    """What same_phone() groups by: the Android ID's hash, or the install itself when there is none."""
    return device.android_id_hash or str(device.pk)


def _longest_running(blocks: list[CitizenDevice], now) -> CitizenDevice | None:
    """active_block()'s choice among rows already loaded: running blocks only, no end date first."""
    running = [
        row for row in blocks
        if row.blocked_at is not None and (row.blocked_until is None or row.blocked_until > now)
    ]

    def order(row):  # as active_block(): no end date first, then the latest end, then by id
        if row.blocked_until is None:
            return (0, 0.0, str(row.pk))
        return (1, -row.blocked_until.timestamp(), str(row.pk))

    return min(running, key=order, default=None)


@dataclass(frozen=True)
class PhoneRecord:
    """A phone's record for reviewers (contract section 15): its posts, discards and running block."""

    posts_total: int
    posts_discarded: int
    block: CitizenDevice | None
    #: Posts a block closed between 2026-11-20 and 2026-11-21 (none after posts/0013 reopened them).
    posts_blocked: int = 0


def phone_records(devices, now=None) -> dict:
    """{install id: PhoneRecord} for many installs at once, with two queries whatever their number.

    The same answers as same_phone(), Post counts and active_block() one install at a time.
    """
    devices = {device.pk: device for device in devices}
    if not devices:
        return {}
    now = now or timezone.now()
    hashes = {device.android_id_hash for device in devices.values() if device.android_id_hash}
    installs = list(CitizenDevice.objects.filter(Q(pk__in=devices) | Q(android_id_hash__in=hashes)))
    key_of = {install.pk: phone_key(install) for install in installs}
    totals: dict[str, list[int]] = {}
    counts = (
        Post.objects.filter(citizen__in=key_of)
        .order_by()
        .values("citizen_id")
        .annotate(
            total=Count("id"),
            discarded=Count("id", filter=Q(status=PostStatus.DISCARDED)),
            blocked=Count("id", filter=Q(status=PostStatus.BLOCKED)),
        )
    )
    for row in counts:
        entry = totals.setdefault(key_of[row["citizen_id"]], [0, 0, 0])
        entry[0] += row["total"]
        entry[1] += row["discarded"]
        entry[2] += row["blocked"]
    blocks: dict[str, list[CitizenDevice]] = {}
    for install in installs:
        blocks.setdefault(key_of[install.pk], []).append(install)
    records = {}
    for pk, device in devices.items():
        key = phone_key(device)
        total, discarded, blocked = totals.get(key, (0, 0, 0))
        records[pk] = PhoneRecord(total, discarded, _longest_running(blocks.get(key, []), now), blocked)
    return records


# --- Registration -------------------------------------------------------------------------------


def _active_village(village_id) -> int:
    if not Village.objects.active().filter(id=village_id).exists():
        raise _validation("village_id", _("No such active village."))
    return int(village_id)


def _seconds_until(oldest, now) -> int:
    return max(1, int((oldest + DAY - now).total_seconds()) + 1)


def _registrations_within(ip_hash: str, limit: int, now, *, written: bool = False) -> None:
    """429 throttled once `limit` phones registered from this network in 24 hours (written: counting this one)."""
    recent = CitizenDevice.objects.filter(registered_ip_hash=ip_hash, created_at__gt=now - DAY)
    count = recent.count()
    if count > limit or (count == limit and not written):
        oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
        raise throttled(_("Too many new registrations from this network today."), _seconds_until(oldest, now))


def register(
    *, nonce: str, integrity_token: str, platform: str, village_id: int, app_version: str, ip: str | None,
    android_id: str = "", fcm_token: str = "", language: str = "te",
):
    """A new install: returns (device, token). The token is shown once and never stored."""
    now = timezone.now()
    if not integrity.nonce_is_fresh(nonce) or CitizenDevice.objects.filter(nonce=nonce).exists():
        raise _validation("nonce", _("This challenge expired or was already used. Ask for a new one."))
    village_id = _active_village(village_id)

    ip_hash = hash_ip(ip)
    limit = settings.CITIZEN_REGISTRATIONS_PER_IP_PER_DAY
    if ip_hash and limit:
        _registrations_within(ip_hash, limit, now)

    checked = settings.CITIZEN_INTEGRITY_REQUIRED
    if checked:
        try:
            if platform != "android":
                raise integrity.IntegrityFailed(f"No integrity check for platform {platform!r} yet.")
            integrity.get_verifier().verify(integrity_token, nonce)
        except integrity.IntegrityFailed as exc:
            logger.warning("Citizen registration refused: %s", exc)
            raise ApiError(
                "integrity_failed",
                _("This app could not be verified. Install it from the Play Store and try again."),
                status=http.HTTP_403_FORBIDDEN,
            ) from None
        except Exception as exc:  # noqa: BLE001 - Google unreachable or failing, credentials missing, an unreadable answer
            # Not a verdict about the phone: Google could not be asked. Decision of 2026-11-11 (audit
            # BE_02, BE-ERR-103): 503 integrity_unavailable, so the app says "try again later",
            # instead of a server error. Only the error type is logged. The nonce is not used up.
            logger.warning("Citizen registration: Play Integrity unavailable (%s)", type(exc).__name__)
            error = ApiError(
                "integrity_unavailable",
                _("The phone check is not available right now. Try again in a few minutes."),
                status=http.HTTP_503_SERVICE_UNAVAILABLE,
                retry_after_s=INTEGRITY_RETRY_SECONDS,
            )
            error.wait = INTEGRITY_RETRY_SECONDS  # DRF turns this into the Retry-After header
            raise error from None

    token = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            device = CitizenDevice.objects.create(
                token_hash=hash_token(token),
                platform=platform,
                app_version=(app_version or "")[:20],
                village_id=village_id,
                registered_ip_hash=ip_hash,
                android_id_hash=hash_android_id(android_id),
                fcm_token=(fcm_token or "")[:255],
                language=language,
                nonce=nonce,
                integrity_checked=checked,
            )
            if ip_hash and limit:
                # Counted again with this phone written (audit BE-DB-014): over the limit undoes it.
                _registrations_within(ip_hash, limit, now, written=True)
            record(
                "citizen.registered",
                target=device,
                target_label=str(device),
                area=AreaRef(AreaLevel.VILLAGE, village_id),
                via="citizen",
                data={
                    "platform": platform,
                    "integrity_checked": checked,
                    "reinstall": same_phone(device).exclude(pk=device.pk).exists(),
                },
            )
    except IntegrityError:
        # The same nonce in two requests at once: only one gets a token.
        raise _validation("nonce", _("This challenge expired or was already used. Ask for a new one.")) from None
    return device, token


def update_device(device: CitizenDevice, *, village_id=None, fcm_token=None, language=None) -> CitizenDevice:
    """PATCH /citizen/me/: the village posts go to, the push token, the language of pushes."""
    fields = ["last_seen"]
    if village_id is not None:
        device.village_id = _active_village(village_id)
        fields.append("village_id")
    if fcm_token is not None:
        device.fcm_token = fcm_token[:255]
        fields.append("fcm_token")
    if language is not None:
        device.language = language
        fields.append("language")
    device.save(update_fields=fields)
    return device


# --- Posting ---------------------------------------------------------------------------------------


def posts_per_day() -> int:
    """The Super Admin's value (console Settings), else CITIZEN_POSTS_PER_DAY; 0 is no limit."""
    return int(post_services.runtime("CITIZEN_POSTS_PER_DAY"))


def hold_phone(device: CitizenDevice) -> None:
    """Inside a transaction: wait for any other write from this phone (any install) to finish (row locks).

    With the count made again after the write, this keeps a daily limit from being passed by requests
    sent at the same moment. Databases without row locks (SQLite) run one write at a time anyway.
    """
    list(same_phone(device).select_for_update().values_list("pk", flat=True))


def _within_daily_limit(recent, limit: int, now, *, written: bool = False) -> None:
    """429 throttled once the phone reached `limit` in the last 24 hours.

    written=True counts the row this request just added: then only more than `limit` is refused.
    """
    count = recent.count()
    if count > limit or (count == limit and not written):
        oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
        raise throttled(_("You can send at most %(n)d posts a day.") % {"n": limit}, _seconds_until(oldest, now))


def posts_today(device: CitizenDevice, now=None):
    """Posts from this phone, any of its installs, in the last 24 hours."""
    now = now or timezone.now()
    return Post.objects.filter(citizen__in=same_phone(device), created_at__gt=now - DAY)


def create_post(
    device: CitizenDevice, *, category, language: str, title: str, body: str, photos=(), videos=(),
    client_ref=None,
) -> Post:
    """Checks, then posts.services.create_citizen_post with the photos and videos attached in its transaction.

    Up to MAX_PHOTOS_PER_POST (3) photos, or one video, never both, as for officials (lead's decision
    of 2026-11-13). Media is optional.

    `client_ref`: the app's id for this post. Sent again by this install after a lost answer, it returns
    the first post (`repeated`) before any check, so the repeat costs no daily post and its files are
    not even opened (MOB-ERR-004).
    """
    earlier = post_services.earlier_post(client_ref, citizen=device)
    if earlier is not None:
        return earlier
    if not settings.CITIZEN_POSTS_ENABLED:
        raise permission_denied(_("Citizen posts are switched off."))
    now = timezone.now()
    block_row = active_block(device, now)
    if block_row is not None:
        raise blocked_error(block_row)
    limit = posts_per_day()
    if limit:
        _within_daily_limit(posts_today(device, now), limit, now)
    if photos and videos:
        raise media.media_conflict(_("A post has either photos or one video."))
    if len(videos) > 1:
        raise _validation("videos", _("A post can have one video."))
    if len(photos) > settings.MAX_PHOTOS_PER_POST:
        raise _validation("photos", _("At most %(n)d photos.") % {"n": settings.MAX_PHOTOS_PER_POST})
    if videos and not settings.VIDEO_POSTS_ENABLED:
        raise permission_denied(_("Video posts are switched off."))
    # Every file is checked (and photos re-encoded) before anything is written: 413/415 leave no post behind.
    # One time budget for all of them (BE-PERF-003).
    with media.media_budget("videos" if videos else "photos"):
        processed = [media.process_image(photo) for photo in photos]
        prepared = [media.prepare_video(video, lenient=True) for video in videos]

    def before():
        # Inside the post's transaction, before the post is written: lock the phone's installs. Locking
        # after the insert deadlocks on MySQL, whose foreign-key check already holds a shared lock on the
        # phone's row in every request sent at the same moment.
        if limit:
            hold_phone(device)

    def attach(post):
        # Once the post is written, still holding the lock: the daily limit again, so two posts sent at
        # the same moment cannot both pass it (audit BE-DB-014).
        if limit:
            _within_daily_limit(posts_today(device, now), limit, now, written=True)
        for order, item in enumerate(processed):
            media.store_photo(post, item, order)
        for order, item in enumerate(prepared, start=len(processed)):
            media.store_video(post, item, order)

    data = post_services.PostInput(
        category=category, language=language, title=title, body=body,
        target=AreaRef(AreaLevel.VILLAGE, device.village_id),
    )
    # The files are written inside the post's transaction; if it fails, they are deleted again.
    try:
        with media.files_removed_on_failure():
            return post_services.create_citizen_post(
                device, data, attach=attach, client_ref=client_ref, before=before,
            )
    except IntegrityError:
        # The same repeat arrived twice at the same moment: the first one's post is the answer.
        earlier = post_services.earlier_post(client_ref, citizen=device)
        if earlier is None:
            raise
        return earlier


# --- Super Admin: block, unblock, discard all ---------------------------------------------------


def _super_admin_or_raise(user, post: Post):
    if not post.citizen_id:
        raise ApiError("validation_error", _("This is not a citizen post."), fields={"post": [_("Not a citizen post.")]})
    grant = authz.best_grant(user, post_services.BLOCK_CITIZEN, post.target)
    if grant is None:
        if authz.can(user, post_services.BLOCK_CITIZEN):
            raise out_of_area(_("This post is outside your area."))
        raise permission_denied(_("Only the Super Admin can block citizens or discard all their posts."))
    return grant


def _comment_or_raise(comment: str) -> str:
    comment = (comment or "").strip()
    if not comment:
        raise post_services.comment_required()
    return comment


def _close_pending(user, device: CitizenDevice, comment: str, action: str = "discard") -> int:
    """Close every pending post of this phone (all installs) inside the actor's area. Returns how many.

    Used by "Discard all" (DISCARDED). A block no longer closes posts (owner's request of 2026-11-21).
    """
    pending = (
        Post.objects.filter(citizen__in=same_phone(device), status=PostStatus.PENDING)
        .filter(post_services.review_scope_q(user, post_services.DECISION_PERMISSION[action]))
        .order_by("submitted_at")
    )
    count = 0
    for item in pending:
        try:
            post_services.review(user, item, action, comment)
        except (post_services.AlreadyDecided, ApiError):
            continue  # another reviewer decided it first
        count += 1
    return count


def _end_open_blocks(device: CitizenDevice, now, user=None, role: str = "") -> int:
    """Mark the phone's running CitizenBlock rows ended now, by `user` (None: the time was up)."""
    return CitizenBlock.objects.filter(phone=phone_key(device), ended_at__isnull=True).update(
        ended_at=now, ended_by=user, ended_by_role=role
    )


def block(user, post: Post, comment: str, days: int) -> CitizenDevice:
    """Block the phone that wrote `post` (all its installs) for `days`. It changes no post.

    Owner's request of 2026-11-21: a block only stops the phone's new posts and service requests. Its
    waiting posts stay PENDING and are reviewed as usual; "Discard all" stays a separate action. (From
    2026-11-20 to 2026-11-21 a block moved them to BLOCKED; posts/0013 reopened those.)

    A block made while another runs replaces it: the old CitizenBlock row ends now, by this Super Admin.
    The phone's fields, the new row and the audit entry are saved together (BE-DB-023).
    """
    grant = _super_admin_or_raise(user, post)
    comment = _comment_or_raise(comment)
    device = post.citizen
    now = timezone.now()
    until = now + timedelta(days=days)
    with transaction.atomic():
        same_phone(device).update(blocked_at=now, blocked_until=until, blocked_by=user, block_reason=comment[:500])
        _end_open_blocks(device, now, user, grant.key)
        CitizenBlock.objects.create(
            phone=phone_key(device), device=device, village_id=device.village_id, blocked_at=now,
            blocked_until=until, blocked_by=user, blocked_by_role=grant.key, reason=comment[:500],
        )
        record(
            "citizen.blocked", actor=user, actor_role=grant.key, target=device, target_label=str(device),
            area=post.target,
            data={"post_id": str(post.pk), "comment": comment, "days": days,
                  "until": drf_serializers.DateTimeField().to_representation(until)},
        )
    device.refresh_from_db()
    return device


def _unblock(user, role: str, device: CitizenDevice, data: dict, via: str) -> CitizenDevice:
    now = timezone.now()
    with transaction.atomic():
        lifted = same_phone(device).filter(blocked_at__isnull=False).update(
            blocked_at=None, blocked_until=None, blocked_by=None, block_reason=""
        )
        ended = _end_open_blocks(device, now, user, role)
        if lifted or ended:
            record(
                "citizen.unblocked", actor=user, actor_role=role, target=device, target_label=str(device),
                area=AreaRef(AreaLevel.VILLAGE, device.village_id), via=via, data=data,
            )
    device.refresh_from_db()
    return device


def unblock(user, post: Post) -> CitizenDevice:
    """Lift the block on the phone that wrote `post` at once (POST /posts/{id}/unblock-citizen/)."""
    grant = _super_admin_or_raise(user, post)
    return _unblock(user, grant.key, post.citizen, {"post_id": str(post.pk)}, via="app")


def unblock_phone(user, device: CitizenDevice) -> CitizenDevice:
    """The same, from the blocked citizens list, without a post (POST /admin/citizens/{id}/unblock/)."""
    grant = authz.best_grant(user, post_services.BLOCK_CITIZEN, AreaRef(AreaLevel.VILLAGE, device.village_id))
    if grant is None:
        raise permission_denied(_("Only the Super Admin can block citizens or discard all their posts."))
    return _unblock(user, grant.key, device, {}, via="console")


def end_finished_blocks(now=None) -> int:
    """Mark ended every block whose time is up, once, and audit `citizen.block_ended` (actor System).

    Run by the citizens.tasks schedule. Each row is ended with a conditional UPDATE, so two workers
    running at once end and announce it only once. Returns how many ended.
    """
    now = now or timezone.now()
    finished = list(
        CitizenBlock.objects.filter(ended_at__isnull=True, blocked_until__isnull=False, blocked_until__lte=now)
        .select_related("device")
        .order_by("blocked_until")[:1000]
    )
    count = 0
    for row in finished:
        with transaction.atomic():
            if not CitizenBlock.objects.filter(pk=row.pk, ended_at__isnull=True).update(ended_at=row.blocked_until):
                continue
            record(
                "citizen.block_ended", target=row.device, target_label=str(row.device),
                area=AreaRef(AreaLevel.VILLAGE, row.village_id), via="system",
                data={"until": drf_serializers.DateTimeField().to_representation(row.blocked_until)},
            )
        count += 1
    return count


# --- Super Admin: the blocked citizens list (owner's request of 2026-11-21) -------------------------


def latest_blocks() -> QuerySet:
    """Each phone's latest CitizenBlock: one row per phone, every install included."""
    newest = CitizenBlock.objects.filter(phone=OuterRef("phone")).order_by("-blocked_at", "-pk").values("pk")[:1]
    return CitizenBlock.objects.filter(pk=Subquery(newest))


def running_q(now) -> Q:
    """A block that has not ended: no one ended it and its time is not up (or it has no end)."""
    return Q(ended_at__isnull=True) & (Q(blocked_until__isnull=True) | Q(blocked_until__gt=now))


def phone_anchors(phones) -> dict:
    """{phone key: the phone's first install}: the stable id the blocked citizens list shows for a phone."""
    phones = set(phones)
    if not phones:
        return {}
    installs = CitizenDevice.objects.filter(Q(android_id_hash__in=phones) | Q(pk__in=_uuids(phones)))
    anchors: dict = {}
    for install in installs.order_by("created_at", "pk"):
        anchors.setdefault(phone_key(install), install)
    return anchors


def _uuids(values) -> list:
    out = []
    for value in values:
        try:
            out.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def latest_posts(installs) -> dict:
    """{phone key: id of the phone's newest post} with one query."""
    installs = list(installs)
    if not installs:
        return {}
    keys = {install.pk: phone_key(install) for install in installs}
    newest = Post.objects.filter(citizen=OuterRef("pk")).order_by("-created_at", "-pk")
    rows = (
        CitizenDevice.objects.filter(pk__in=keys)
        .annotate(post_id=Subquery(newest.values("pk")[:1]), post_at=Subquery(newest.values("created_at")[:1]))
        .values("pk", "post_id", "post_at")
    )
    best: dict = {}
    for row in rows:
        if row["post_id"] is None:
            continue
        key = keys[row["pk"]]
        if key not in best or row["post_at"] > best[key][1]:
            best[key] = (row["post_id"], row["post_at"])
    return {key: value[0] for key, value in best.items()}


def phone_installs(phones) -> list[CitizenDevice]:
    """Every install of these phones."""
    phones = set(phones)
    if not phones:
        return []
    return list(CitizenDevice.objects.filter(Q(android_id_hash__in=phones) | Q(pk__in=_uuids(phones))))


def device_or_404(device_id) -> CitizenDevice:
    """Any install of a phone, by its id; 404 not_found for anything else."""
    device = CitizenDevice.objects.filter(pk=device_id).first() if device_id else None
    if device is None:
        raise ApiError("not_found", _("No such citizen."), status=http.HTTP_404_NOT_FOUND)
    return device


def discard_all(user, post: Post, comment: str) -> int:
    """Discard every pending post of the phone that wrote `post`, in one action. Returns how many.

    The discards and their summary audit entry are saved together, in one transaction (BE-DB-023).
    """
    grant = _super_admin_or_raise(user, post)
    comment = _comment_or_raise(comment)
    with transaction.atomic():
        count = _close_pending(user, post.citizen, comment)
        record(
            "citizen.posts_discarded", actor=user, actor_role=grant.key, target=post.citizen,
            target_label=str(post.citizen), area=post.target,
            data={"post_id": str(post.pk), "comment": comment, "discarded": count},
        )
    return count
