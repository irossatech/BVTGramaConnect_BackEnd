"""Push jobs, run by django-q2.

A send that fails for a passing reason (Firebase busy, a timeout) is tried again, at most
DELIVERY_RETRY_ATTEMPTS times in all, waiting 1 s, then 4 s (grama.retry.backoff_delays). The wait
is never spent asleep in the worker, which has only Q_WORKERS (2) processes: the next try is a
one-off scheduled job instead, so other jobs (escalation alerts, other pushes) keep moving. With no
wait configured (DELIVERY_RETRY_BASE_SECONDS=0, as in the tests) the next try runs at once.

When the last try fails, the failure is written to the audit log as push.failed. Unregistered
device tokens are deleted instead of retried; other permanent errors are not retried.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from grama.retry import PermanentDeliveryError, backoff_delays

from .models import Device
from .push import InvalidDeviceToken, PushMessage, get_push_gateway

logger = logging.getLogger("grama.push")

TOPIC_TASK = "apps.notifications.tasks.send_push_to_topic"
DEVICE_TASK = "apps.notifications.tasks.send_push_to_device"
CITIZEN_TASK = "apps.notifications.tasks.send_push_to_citizen"
SERVICE_REQUEST_DEVICE_TASK = "apps.notifications.tasks.send_service_request_push_to_device"
SERVICE_REQUEST_UPDATE_TASK = "apps.notifications.tasks.send_service_request_update"
#: The longest Retry-After from FCM a retry honours.
MAX_RETRY_AFTER_SECONDS = 3600


def _audit_failure(target: str, error: Exception) -> None:
    from apps.audit.services import record

    logger.error("Push to %s failed after retries: %s", target, type(error).__name__)
    record(
        "push.failed",
        via="system",
        data={"target": target, "attempts": settings.DELIVERY_RETRY_ATTEMPTS, "error": type(error).__name__},
    )


def _schedule_retry(func: str, args: tuple, wait_seconds: float) -> None:
    from django_q.models import Schedule

    Schedule.objects.create(
        name=f"push-retry-{uuid.uuid4().hex[:12]}",
        func=func,
        args=repr(args),
        schedule_type=Schedule.ONCE,
        next_run=timezone.now() + timedelta(seconds=wait_seconds),
    )


def _deliver(gateway, message: PushMessage, target: str, func: str, args: tuple, attempt: int) -> bool:
    """Send `message`. On a passing failure, try again: at once when no wait is configured, else as a
    one-off job of `func` with `args` + (attempt + 1,). Returns whether it went out now.

    InvalidDeviceToken is raised to the caller, which forgets the token.
    """
    attempts = settings.DELIVERY_RETRY_ATTEMPTS
    delays = backoff_delays(attempts, settings.DELIVERY_RETRY_BASE_SECONDS)
    while True:
        try:
            gateway.send(message)
            return True
        except InvalidDeviceToken:
            raise
        except PermanentDeliveryError as exc:
            _audit_failure(target, exc)
            return False
        except Exception as exc:
            if attempt >= attempts:
                _audit_failure(target, exc)
                return False
            wait = delays[attempt - 1]
            if wait > 0:
                # FCM's Retry-After (429, 503): wait at least that long, at most an hour (BE-INT-203).
                wait = max(wait, min(getattr(exc, "retry_after", 0) or 0, MAX_RETRY_AFTER_SECONDS))
            attempt += 1
            if wait > 0:
                logger.info("Push to %s failed (%s); trying again in %s s.", target, type(exc).__name__, wait)
                _schedule_retry(func, (*args, attempt), wait)
                return False


def send_push_to_topic(
    topic: str, title: str, body: str, data: dict, priority: str = "high", attempt: int = 1,
) -> bool:
    gateway = get_push_gateway()
    if not gateway.enabled:
        return False
    message = PushMessage(title=title, body=body, data=data, topic=topic, priority=priority)
    return _deliver(gateway, message, f"topic {topic}", TOPIC_TASK, (topic, title, body, data, priority), attempt)


def send_push_to_topics(topics: list[str], title: str, body: str, data: dict, priority: str = "high") -> int:
    """The same push to several topics in one job (a post for many areas). Returns how many went out.

    After PUSH_TOPICS_JOB_SECONDS the job starts no new topic and queues the ones left as a new
    job. A post can have up to 200 areas; while Firebase is slow, sending them all in turn could
    pass the worker's time limit, and the worker would stop the job and run it again from the
    start, pushing the same post twice to the topics already done.
    """
    import time

    from django.conf import settings
    from django_q.tasks import async_task

    deadline = time.monotonic() + settings.PUSH_TOPICS_JOB_SECONDS
    sent = 0
    for index, topic in enumerate(topics):
        if index and time.monotonic() >= deadline:
            async_task("apps.notifications.tasks.send_push_to_topics", topics[index:], title, body, data, priority, group="push")
            break
        sent += send_push_to_topic(topic, title, body, data, priority)
    return sent


def send_push_to_citizen(device_id: str, title: str, body: str, data: dict, attempt: int = 1) -> bool:
    """Push to the phone that wrote a citizen post. An unregistered token is forgotten."""
    from apps.citizens.models import CitizenDevice

    gateway = get_push_gateway()
    device = CitizenDevice.objects.filter(pk=device_id).exclude(fcm_token="").first()
    if not gateway.enabled or device is None:
        return False
    message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
    try:
        return _deliver(
            gateway, message, f"citizen phone {device.pk}", CITIZEN_TASK, (str(device_id), title, body, data), attempt,
        )
    except InvalidDeviceToken:
        CitizenDevice.objects.filter(pk=device.pk).update(fcm_token="")
        return False


def _send_to_device(gateway, device: Device, title: str, body: str, data: dict, attempt: int = 1) -> bool:
    message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
    try:
        return _deliver(
            gateway, message, f"device {device.pk}", DEVICE_TASK, (str(device.pk), title, body, data), attempt,
        )
    except InvalidDeviceToken:
        device.delete()
        return False


def send_push_to_device(device_id: str, title: str, body: str, data: dict, attempt: int = 1) -> bool:
    """One official's device: the scheduled retry of a push that failed for a passing reason."""
    gateway = get_push_gateway()
    device = Device.objects.filter(pk=device_id).first()
    if not gateway.enabled or device is None:
        return False
    return _send_to_device(gateway, device, title, body, data, attempt)


def alert_escalation(post_id: str, level: str, hours: int) -> int:
    """The escalation alert for one post (inbox items and pushes), queued by receivers.on_post_escalated.

    Skipped when the post was decided in the meantime: there is nothing left to review. Returns how many
    officials were told.
    """
    from apps.posts.models import Post, PostStatus

    from . import services

    post = Post.objects.select_related("category").filter(pk=post_id, status=PostStatus.PENDING).first()
    if post is None:
        return 0
    return len(services.alert_escalation(post, level, hours))


def send_push_to_user(user_id: str, title: str, body: str, data: dict) -> int:
    """Push to every device of one official. Returns how many devices got it."""
    gateway = get_push_gateway()
    if not gateway.enabled:
        return 0
    return sum(_send_to_device(gateway, device, title, body, data) for device in Device.objects.filter(user_id=user_id))


def _service_request_to_device(gateway, device: Device, request_id, template_key: str, parts, attempt: int = 1) -> bool:
    title, body, data = parts
    message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
    try:
        # The retry gets ids and the template key only, never the text with the citizen's name (BVTGC-78).
        return _deliver(
            gateway, message, f"device {device.pk}", SERVICE_REQUEST_DEVICE_TASK,
            (str(device.pk), request_id, template_key), attempt,
        )
    except InvalidDeviceToken:
        device.delete()
        return False


def send_service_request_pushes(request_id, recipients: list[list[str]]) -> int:
    """Pushes about one service request to officials; recipients is [[user_id, template_key], ...].

    The texts are written here, each in the device owner's language, so the citizen's name is never an
    argument of this job or of its retries (BVTGC-78). Returns how many devices got it.
    """
    from apps.service_requests.models import ServiceRequest

    from . import services

    gateway = get_push_gateway()
    if not gateway.enabled or not recipients:
        return 0
    request = ServiceRequest.objects.select_related("service").filter(pk=request_id).first()
    if request is None:
        return 0
    template_of = {str(user_id): template_key for user_id, template_key in recipients}
    parts = {}  # (template_key, language) -> (title, body, data)
    sent = 0
    for device in Device.objects.filter(user_id__in=list(template_of)).select_related("user"):
        key = (template_of[str(device.user_id)], device.user.preferred_language)
        if key not in parts:
            parts[key] = services.service_request_push(request, *key)
        sent += _service_request_to_device(gateway, device, request.pk, key[0], parts[key])
    return sent


def send_service_request_push_to_device(device_id: str, request_id, template_key: str, attempt: int = 1) -> bool:
    """One official's device: the scheduled retry of a service-request push. Writes the text again."""
    from apps.service_requests.models import ServiceRequest

    from . import services

    gateway = get_push_gateway()
    device = Device.objects.select_related("user").filter(pk=device_id, user__isnull=False).first()
    request = ServiceRequest.objects.select_related("service").filter(pk=request_id).first()
    if not gateway.enabled or device is None or request is None:
        return False
    parts = services.service_request_push(request, template_key, device.user.preferred_language)
    return _service_request_to_device(gateway, device, request.pk, template_key, parts, attempt)


def send_service_request_update(request_id, status: str, attempt: int = 1) -> bool:
    """The citizen's push about their service request's new status. The text, with the official's note,
    is written here from the request, so it is never an argument of this job or its retries (BE-JOB-201).
    An unregistered token is forgotten."""
    from apps.service_requests.models import ServiceRequest

    from . import services

    gateway = get_push_gateway()
    request = ServiceRequest.objects.select_related("service", "device").filter(pk=request_id).first()
    if not gateway.enabled or request is None or not request.device.fcm_token:
        return False
    device = request.device
    title, body, data = services.service_request_update_push(request, status, device.language)
    message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
    try:
        return _deliver(
            gateway, message, f"citizen phone {device.pk}", SERVICE_REQUEST_UPDATE_TASK, (request.pk, status), attempt,
        )
    except InvalidDeviceToken:
        type(device).objects.filter(pk=device.pk).update(fcm_token="")
        return False


def send_push_to_users(messages: list[list[str]], body: str, data: dict) -> int:
    """The same event for many officials in one job.

    `messages` is [[user_id, title], ...], each title in that official's language. One gateway, so
    one FCM session, serves all of them. Returns how many devices got it.
    """
    gateway = get_push_gateway()
    if not gateway.enabled or not messages:
        return 0
    titles = {str(user_id): title for user_id, title in messages}
    devices = Device.objects.filter(user_id__in=list(titles))
    return sum(_send_to_device(gateway, device, titles[str(device.user_id)], body, data) for device in devices)
