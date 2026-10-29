"""Push jobs, run by django-q2. Each send retries with backoff (grama.retry);
when every try fails the failure is written to the audit log as push.failed.
Unregistered device tokens are deleted instead of retried.
"""

from __future__ import annotations

import logging

from grama.retry import with_retries

from .models import Device
from .push import InvalidDeviceToken, PushMessage, get_push_gateway

logger = logging.getLogger("grama.push")


def _audit_failure(target: str, error: Exception) -> None:
    from django.conf import settings

    from apps.audit.services import record

    logger.error("Push to %s failed after retries: %s", target, type(error).__name__)
    record(
        "push.failed",
        via="system",
        data={"target": target, "attempts": settings.DELIVERY_RETRY_ATTEMPTS, "error": type(error).__name__},
    )


def send_push_to_topic(topic: str, title: str, body: str, data: dict, priority: str = "high") -> bool:
    gateway = get_push_gateway()
    if not gateway.enabled:
        return False
    message = PushMessage(title=title, body=body, data=data, topic=topic, priority=priority)
    try:
        with_retries(lambda: gateway.send(message))
    except Exception as exc:
        _audit_failure(f"topic {topic}", exc)
        return False
    return True


def send_push_to_topics(topics: list[str], title: str, body: str, data: dict, priority: str = "high") -> int:
    """The same push to several topics in one job (a post for many areas). Returns how many went out."""
    return sum(send_push_to_topic(topic, title, body, data, priority) for topic in topics)


def send_push_to_citizen(device_id: str, title: str, body: str, data: dict) -> bool:
    """Push to the phone that wrote a citizen post. An unregistered token is forgotten."""
    from apps.citizens.models import CitizenDevice

    gateway = get_push_gateway()
    device = CitizenDevice.objects.filter(pk=device_id).exclude(fcm_token="").first()
    if not gateway.enabled or device is None:
        return False
    message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
    try:
        with_retries(lambda: gateway.send(message))
    except InvalidDeviceToken:
        CitizenDevice.objects.filter(pk=device.pk).update(fcm_token="")
        return False
    except Exception as exc:
        _audit_failure(f"citizen phone {device.pk}", exc)
        return False
    return True


def send_push_to_user(user_id: str, title: str, body: str, data: dict) -> int:
    """Push to every device of one official. Returns how many devices got it."""
    gateway = get_push_gateway()
    if not gateway.enabled:
        return 0
    sent = 0
    for device in Device.objects.filter(user_id=user_id):
        message = PushMessage(title=title, body=body, data=data, token=device.fcm_token)
        try:
            with_retries(lambda: gateway.send(message))
        except InvalidDeviceToken:
            device.delete()
        except Exception as exc:
            _audit_failure(f"device {device.pk}", exc)
        else:
            sent += 1
    return sent
