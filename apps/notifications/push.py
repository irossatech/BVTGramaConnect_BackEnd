"""Push notifications through a pluggable gateway.

PUSH_GATEWAY names the class:
- notifications.push.FcmGateway: Firebase Cloud Messaging HTTP v1. Needs
  FCM_CREDENTIALS_FILE (a service-account JSON). Without it, it logs
  "FCM disabled: no credentials" once and sends nothing, so development needs
  no Firebase project.
- notifications.push.LocmemPushGateway: tests. Keeps messages in .outbox.

Citizens never log in, so they get pushes through topics their app subscribes
to: village_<id>, mandal_<id>, district_<id>, state_<id>, each with
FCM_TOPIC_PREFIX in front ("qa_" on the qa server, empty in production, so a
test post on qa never reaches real phones in the same Firebase project).
Officials also get pushes on their own device tokens, which need no prefix.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.utils.module_loading import import_string

from grama.retry import PermanentDeliveryError

logger = logging.getLogger("grama.push")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


#: Android notification channels the app creates: "news" (high importance) and "urgent" (max).
NEWS_CHANNEL = "news"
URGENT_CHANNEL = "urgent"


def topic_for(level: str, area_id: int) -> str:
    """FCM topic of an area, e.g. ("VILLAGE", 12) -> "village_12", or "qa_village_12" on qa."""
    return f"{settings.FCM_TOPIC_PREFIX}{level.lower()}_{int(area_id)}"


@dataclass
class PushMessage:
    title: str
    body: str
    data: dict = field(default_factory=dict)
    topic: str | None = None
    token: str | None = None
    #: "high" wakes the phone at once; "normal" may wait for the phone to be awake.
    #: Messages to officials are high; posts to citizens too while PUSH_ALL_POSTS_HIGH_PRIORITY
    #: is on (the default), else only urgent posts.
    priority: str = "high"

    def __post_init__(self):
        if bool(self.topic) == bool(self.token):
            raise ValueError("A push message goes to exactly one topic or one device token.")
        if self.priority not in ("high", "normal"):
            raise ValueError("Push priority is high or normal.")
        # FCM data values must be strings.
        self.data = {key: str(value) for key, value in self.data.items() if value is not None}


def build_payload(message: PushMessage) -> dict:
    """The FCM HTTP v1 request body for one message.

    - channel_id: urgent posts and escalations on the app's "urgent" channel, the rest on "news".
    - tag: a later push about the same post or service request replaces the earlier one on the phone.
    - ttl: news older than PUSH_TTL_SECONDS (a day) is dropped, not delivered late to a phone that was off.
    - apns sound: iPhones otherwise show the notification silently.
    FCM rejects null fields, so empty ones are left out.
    """
    data = message.data
    urgent = data.get("urgent") == "1" or data.get("type") == "ESCALATION"
    android_note = {"channel_id": URGENT_CHANNEL if urgent else NEWS_CHANNEL}
    tag = data.get("post_id") or data.get("request_id")
    if tag:
        android_note["tag"] = tag
    body = {
        "notification": {"title": message.title, "body": message.body},
        "data": data,
        "android": {
            "priority": message.priority,
            "ttl": f"{int(settings.PUSH_TTL_SECONDS)}s",
            "notification": android_note,
        },
        "apns": {
            "headers": {"apns-priority": "10" if message.priority == "high" else "5"},
            "payload": {"aps": {"sound": "default"}},
        },
    }
    if message.topic:
        body["topic"] = message.topic
    else:
        body["token"] = message.token
    return {"message": body}


class InvalidDeviceToken(PermanentDeliveryError):
    """The device token is unknown to FCM; the device should be forgotten."""


class PushGateway(ABC):
    enabled = True

    @abstractmethod
    def send(self, message: PushMessage) -> None:
        """Deliver one message or raise. InvalidDeviceToken means drop the device."""


class LocmemPushGateway(PushGateway):
    outbox: list[PushMessage] = []
    #: Tests may put exceptions here; send() raises the next one instead of delivering.
    failures: list[Exception] = []

    def send(self, message: PushMessage) -> None:
        if LocmemPushGateway.failures:
            raise LocmemPushGateway.failures.pop(0)
        LocmemPushGateway.outbox.append(message)


_disabled_logged = False


class FcmGateway(PushGateway):
    def __init__(self):
        global _disabled_logged
        path = settings.FCM_CREDENTIALS_FILE
        self.enabled = bool(path) and Path(path).is_file()
        self._session = None
        if not self.enabled and not _disabled_logged:
            logger.warning("FCM disabled: no credentials (set FCM_CREDENTIALS_FILE). Pushes are skipped.")
            _disabled_logged = True

    def _authorized_session(self):
        if self._session is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                settings.FCM_CREDENTIALS_FILE, scopes=[FCM_SCOPE]
            )
            self.project_id = settings.FCM_PROJECT_ID or credentials.project_id
            self._session = AuthorizedSession(credentials)
        return self._session

    def send(self, message: PushMessage) -> None:
        if not self.enabled:
            return
        session = self._authorized_session()
        payload = build_payload(message)
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        response = session.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                headers={"Content-Type": "application/json; charset=utf-8"}, timeout=10)
        if response.status_code == 200:
            return
        detail = response.text[:300]
        if response.status_code in (400, 404) and ("UNREGISTERED" in detail or "registration-token" in detail):
            raise InvalidDeviceToken(detail)
        if response.status_code in (400, 401, 403):
            raise PermanentDeliveryError(f"FCM {response.status_code}: {detail}")
        raise RuntimeError(f"FCM {response.status_code}: {detail}")


def get_push_gateway() -> PushGateway:
    return import_string(settings.PUSH_GATEWAY)()
