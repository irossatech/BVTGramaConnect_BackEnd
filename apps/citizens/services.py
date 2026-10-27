"""Citizen phones: posting tokens, registration, posting and blocking. docs/api-contract.md, section 15.

- A token is issued once per install, after Google Play Integrity confirms our
  genuine app on a real phone (integrity.py). CITIZEN_REGISTRATIONS_PER_IP_PER_DAY
  limits new phones per IP address only when set (0, the default, is no limit).
  Only the token's SHA-256 hash is stored; the IP address only as a keyed hash.
- Posts go through posts.services.create_citizen_post: straight to PENDING
  for every reviewer of the phone's village. CITIZEN_POSTS_PER_DAY and
  CITIZEN_MAX_PHOTOS limit them only when set (0, the default, is no limit).
- Officials holding posts.block_citizen over a citizen post's village block
  the phone; its other pending posts in their area are discarded.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
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


def _validation(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


# --- Tokens -------------------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str | None) -> str:
    if not ip:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), b"citizen-ip:" + ip.encode(), hashlib.sha256).hexdigest()


def device_from_request(request) -> CitizenDevice:
    """The phone behind X-Citizen-Token, or 401 citizen_token_invalid."""
    token = (request.META.get(TOKEN_HEADER) or "").strip()
    if not token:
        raise token_invalid()
    device = CitizenDevice.objects.filter(token_hash=hash_token(token)).first()
    if device is None:
        raise token_invalid()
    return device


# --- Registration -------------------------------------------------------------------------------


def _active_village(village_id) -> int:
    if not Village.objects.active().filter(id=village_id).exists():
        raise _validation("village_id", _("No such active village."))
    return int(village_id)


def _seconds_until(oldest, now) -> int:
    return max(1, int((oldest + DAY - now).total_seconds()) + 1)


def register(*, nonce: str, integrity_token: str, platform: str, village_id: int, app_version: str, ip: str | None):
    """A new phone: returns (device, token). The token is shown once and never stored."""
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
                nonce=nonce,
                integrity_checked=checked,
            )
            record(
                "citizen.registered",
                target=device,
                target_label=str(device),
                area=AreaRef(AreaLevel.VILLAGE, village_id),
                via="citizen",
                data={"platform": platform, "integrity_checked": checked},
            )
    except IntegrityError:
        # The same nonce in two requests at once: only one gets a token.
        raise _validation("nonce", _("This challenge expired or was already used. Ask for a new one.")) from None
    return device, token


def change_village(device: CitizenDevice, village_id) -> CitizenDevice:
    device.village_id = _active_village(village_id)
    device.save(update_fields=["village_id", "last_seen"])
    return device


# --- Posting ---------------------------------------------------------------------------------------


def posts_today(device: CitizenDevice, now=None):
    now = now or timezone.now()
    return Post.objects.filter(citizen=device, created_at__gt=now - DAY)


def create_post(device: CitizenDevice, *, category, language: str, title: str, body: str, photos=()) -> Post:
    """Checks, then posts.services.create_citizen_post with the photos attached in its transaction."""
    if not settings.CITIZEN_POSTS_ENABLED:
        raise permission_denied(_("Citizen posts are switched off."))
    if device.blocked:
        raise ApiError(
            "citizen_blocked", _("This phone has been blocked from posting."), status=http.HTTP_403_FORBIDDEN
        )
    now = timezone.now()
    limit = settings.CITIZEN_POSTS_PER_DAY
    if limit:
        recent = posts_today(device, now)
        if recent.count() >= limit:
            oldest = recent.order_by("-created_at").values_list("created_at", flat=True)[limit - 1]
            raise throttled(
                _("You can send at most %(n)d posts a day.") % {"n": limit}, _seconds_until(oldest, now)
            )
    if settings.CITIZEN_MAX_PHOTOS and len(photos) > settings.CITIZEN_MAX_PHOTOS:
        raise _validation("photos", _("At most %(n)d photos.") % {"n": settings.CITIZEN_MAX_PHOTOS})
    # Photos are checked and re-encoded before anything is written (413/415 leave no post behind).
    processed = [media.process_image(photo) for photo in photos]

    def attach(post):
        for order, item in enumerate(processed):
            media.store_photo(post, item, order)

    data = post_services.PostInput(
        category=category, language=language, title=title, body=body,
        target=AreaRef(AreaLevel.VILLAGE, device.village_id),
    )
    return post_services.create_citizen_post(device, data, attach=attach)


# --- Blocking (officials) --------------------------------------------------------------------------


def _blocker_or_raise(user, post: Post):
    if not post.citizen_id:
        raise ApiError("validation_error", _("This is not a citizen post."), fields={"post": [_("Not a citizen post.")]})
    grant = authz.best_grant(user, post_services.BLOCK_CITIZEN, post.target)
    if grant is None:
        if authz.can(user, post_services.BLOCK_CITIZEN):
            raise out_of_area(_("This post is outside your area."))
        raise permission_denied(_("Your role cannot block citizens."))
    return grant


def block(user, post: Post, comment: str) -> CitizenDevice:
    """Block the phone that wrote `post`, then discard its other pending posts inside the actor's area."""
    grant = _blocker_or_raise(user, post)
    comment = (comment or "").strip()
    if not comment:
        raise post_services.comment_required()
    device = post.citizen
    with transaction.atomic():
        newly = CitizenDevice.objects.filter(pk=device.pk, blocked_at__isnull=True).update(
            blocked_at=timezone.now(), blocked_by=user, block_reason=comment[:500]
        )
        device.refresh_from_db()
        if newly:
            record(
                "citizen.blocked", actor=user, actor_role=grant.key, target=device, target_label=str(device),
                area=post.target, data={"post_id": str(post.pk), "comment": comment},
            )
    pending = (
        Post.objects.filter(citizen=device, status=PostStatus.PENDING)
        .filter(post_services.review_scope_q(user, post_services.DECISION_PERMISSION["discard"]))
    )
    for item in pending:
        try:
            post_services.review(user, item, "discard", comment)
        except (post_services.AlreadyDecided, ApiError):
            continue  # another reviewer decided it first
    return device


def unblock(user, post: Post) -> CitizenDevice:
    grant = _blocker_or_raise(user, post)
    device = post.citizen
    with transaction.atomic():
        lifted = CitizenDevice.objects.filter(pk=device.pk, blocked_at__isnull=False).update(
            blocked_at=None, blocked_by=None, block_reason=""
        )
        device.refresh_from_db()
        if lifted:
            record(
                "citizen.unblocked", actor=user, actor_role=grant.key, target=device, target_label=str(device),
                area=post.target, data={"post_id": str(post.pk)},
            )
    return device
