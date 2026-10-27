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
  citizen sees the reason and the end of the block.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
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
from .models import CitizenDevice

logger = logging.getLogger("grama.citizens")

TOKEN_HEADER = "HTTP_X_CITIZEN_TOKEN"
DAY = timedelta(hours=24)


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


def _keyed_hash(kind: bytes, value: str | None) -> str:
    if not value:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), kind + b":" + value.encode(), hashlib.sha256).hexdigest()


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
    """The running block on this phone (any of its installs), or None. The longest one wins."""
    now = now or timezone.now()
    return (
        same_phone(device)
        .filter(blocked_at__isnull=False)
        .filter(Q(blocked_until__isnull=True) | Q(blocked_until__gt=now))
        .order_by("-blocked_until")
        .first()
    )


# --- Registration -------------------------------------------------------------------------------


def _active_village(village_id) -> int:
    if not Village.objects.active().filter(id=village_id).exists():
        raise _validation("village_id", _("No such active village."))
    return int(village_id)


def _seconds_until(oldest, now) -> int:
    return max(1, int((oldest + DAY - now).total_seconds()) + 1)


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
        recent = CitizenDevice.objects.filter(registered_ip_hash=ip_hash, created_at__gt=now - DAY)
        if recent.count() >= limit:
            oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
            raise throttled(_("Too many new registrations from this network today."), _seconds_until(oldest, now))

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


def posts_today(device: CitizenDevice, now=None):
    """Posts from this phone, any of its installs, in the last 24 hours."""
    now = now or timezone.now()
    return Post.objects.filter(citizen__in=same_phone(device), created_at__gt=now - DAY)


def create_post(
    device: CitizenDevice, *, category, language: str, title: str, body: str, photos=(), videos=()
) -> Post:
    """Checks, then posts.services.create_citizen_post with the photos and videos attached in its transaction.

    Photos and videos may be mixed, any number of each (decision of 2026-10-27).
    """
    if not settings.CITIZEN_POSTS_ENABLED:
        raise permission_denied(_("Citizen posts are switched off."))
    now = timezone.now()
    block_row = active_block(device, now)
    if block_row is not None:
        raise blocked_error(block_row)
    limit = posts_per_day()
    if limit:
        recent = posts_today(device, now)
        if recent.count() >= limit:
            oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
            raise throttled(
                _("You can send at most %(n)d posts a day.") % {"n": limit}, _seconds_until(oldest, now)
            )
    if settings.CITIZEN_MAX_PHOTOS and len(photos) > settings.CITIZEN_MAX_PHOTOS:
        raise _validation("photos", _("At most %(n)d photos.") % {"n": settings.CITIZEN_MAX_PHOTOS})
    if videos and not settings.VIDEO_POSTS_ENABLED:
        raise permission_denied(_("Video posts are switched off."))
    # Every file is checked (and photos re-encoded) before anything is written: 413/415 leave no post behind.
    processed = [media.process_image(photo) for photo in photos]
    prepared = [media.prepare_video(video, lenient=True) for video in videos]

    def attach(post):
        for order, item in enumerate(processed):
            media.store_photo(post, item, order)
        for order, item in enumerate(prepared, start=len(processed)):
            media.store_video(post, item, order)

    data = post_services.PostInput(
        category=category, language=language, title=title, body=body,
        target=AreaRef(AreaLevel.VILLAGE, device.village_id),
    )
    return post_services.create_citizen_post(device, data, attach=attach)


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


def _discard_pending(user, device: CitizenDevice, comment: str) -> int:
    """Discard every pending post of this phone (all installs) inside the actor's area. Returns how many."""
    pending = (
        Post.objects.filter(citizen__in=same_phone(device), status=PostStatus.PENDING)
        .filter(post_services.review_scope_q(user, post_services.DECISION_PERMISSION["discard"]))
        .order_by("submitted_at")
    )
    count = 0
    for item in pending:
        try:
            post_services.review(user, item, "discard", comment)
        except (post_services.AlreadyDecided, ApiError):
            continue  # another reviewer decided it first
        count += 1
    return count


def block(user, post: Post, comment: str, days: int) -> CitizenDevice:
    """Block the phone that wrote `post` (all its installs) for `days`, then discard its pending posts."""
    grant = _super_admin_or_raise(user, post)
    comment = _comment_or_raise(comment)
    device = post.citizen
    now = timezone.now()
    until = now + timedelta(days=days)
    with transaction.atomic():
        same_phone(device).update(blocked_at=now, blocked_until=until, blocked_by=user, block_reason=comment[:500])
        record(
            "citizen.blocked", actor=user, actor_role=grant.key, target=device, target_label=str(device),
            area=post.target,
            data={"post_id": str(post.pk), "comment": comment, "days": days,
                  "until": drf_serializers.DateTimeField().to_representation(until)},
        )
    _discard_pending(user, device, comment)
    device.refresh_from_db()
    return device


def unblock(user, post: Post) -> CitizenDevice:
    grant = _super_admin_or_raise(user, post)
    device = post.citizen
    with transaction.atomic():
        lifted = same_phone(device).filter(blocked_at__isnull=False).update(
            blocked_at=None, blocked_until=None, blocked_by=None, block_reason=""
        )
        if lifted:
            record(
                "citizen.unblocked", actor=user, actor_role=grant.key, target=device, target_label=str(device),
                area=post.target, data={"post_id": str(post.pk)},
            )
    device.refresh_from_db()
    return device


def discard_all(user, post: Post, comment: str) -> int:
    """Discard every pending post of the phone that wrote `post`, in one action. Returns how many."""
    grant = _super_admin_or_raise(user, post)
    comment = _comment_or_raise(comment)
    count = _discard_pending(user, post.citizen, comment)
    record(
        "citizen.posts_discarded", actor=user, actor_role=grant.key, target=post.citizen,
        target_label=str(post.citizen), area=post.target,
        data={"post_id": str(post.pk), "comment": comment, "discarded": count},
    )
    return count
