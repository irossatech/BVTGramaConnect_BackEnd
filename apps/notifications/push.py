"""Push notifications through a pluggable gateway.

PUSH_GATEWAY names the class:
- notifications.push.FcmGateway: Firebase Cloud Messaging HTTP v1. Needs
  FCM_CREDENTIALS_FILE (a service-account JSON). Without it, it logs
  "FCM disabled: no credentials" once and sends nothing, so development needs
  no Firebase project.
- notifications.push.LocmemPushGateway: tests. Keeps messages in .outbox.

Citizens never log in, so they get pushes through topics their app subscribes
to: village_<id>, mandal_<id>, district_<id>, state_<id>. Officials also get
pushes on their own device tokens.
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


def topic_for(level: str, area_id: int) -> str:
    """FCM topic of an area, e.g. ("VILLAGE", 12) -> "village_12"."""
    return f"{level.lower()}_{int(area_id)}"


@dataclass
class PushMessage:
    title: str
    body: str
    data: dict = field(default_factory=dict)
    topic: str | None = None
    token: str | None = None

    def __post_init__(self):
        if bool(self.topic) == bool(self.token):
            raise ValueError("A push message goes to exactly one topic or one device token.")
        # FCM data values must be strings.
        self.data = {key: str(value) for key, value in self.data.items() if value is not None}


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
        payload = {
            "message": {
                "notification": {"title": message.title, "body": message.body},
                "data": message.data,
                "android": {"priority": "high"},
            }
        }
        if message.topic:
            payload["message"]["topic"] = message.topic
        else:
            payload["message"]["token"] = message.token
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
