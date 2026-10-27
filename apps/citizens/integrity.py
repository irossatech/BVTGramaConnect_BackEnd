"""One-time challenges and Google Play Integrity verdicts for citizen registration.

The app asks Play Integrity (classic request) for a token bound to a nonce
from POST /citizen/challenge/, and sends it to POST /citizen/register/. The
server asks Google to decode it and accepts only our package, recognised by
Play, on a device that meets device integrity, for the same nonce, issued
within CITIZEN_CHALLENGE_SECONDS.

Nonces are stateless: random bytes, the issue time and an HMAC with the
SECRET_KEY, base64url-encoded as Play Integrity requires. They are single-use
because CitizenDevice.nonce is unique.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import struct
import time

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger("grama.citizens")

PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
_RANDOM_BYTES = 24
_MAC_BYTES = 32


class IntegrityFailed(Exception):
    """Google did not confirm a genuine app on a real phone. The message is for logs, never for the client."""


# --- Challenges --------------------------------------------------------------------------------


def _mac(payload: bytes) -> bytes:
    return hmac.new(settings.SECRET_KEY.encode(), b"citizen-nonce:" + payload, hashlib.sha256).digest()


def new_nonce(now: float | None = None) -> str:
    payload = secrets.token_bytes(_RANDOM_BYTES) + struct.pack(">Q", int(now if now is not None else time.time()))
    return base64.urlsafe_b64encode(payload + _mac(payload)).decode().rstrip("=")


def nonce_is_fresh(nonce: str, now: float | None = None) -> bool:
    """Signed by this server and not older than CITIZEN_CHALLENGE_SECONDS. Single use is checked by the caller."""
    try:
        raw = base64.urlsafe_b64decode(nonce + "=" * (-len(nonce) % 4))
    except (ValueError, TypeError):
        return False
    if len(raw) != _RANDOM_BYTES + 8 + _MAC_BYTES:
        return False
    payload, mac = raw[:-_MAC_BYTES], raw[-_MAC_BYTES:]
    if not hmac.compare_digest(mac, _mac(payload)):
        return False
    (issued,) = struct.unpack(">Q", payload[_RANDOM_BYTES:])
    age = (now if now is not None else time.time()) - issued
    return 0 <= age <= settings.CITIZEN_CHALLENGE_SECONDS


# --- Verdicts ---------------------------------------------------------------------------------------


class PlayIntegrityVerifier:
    """Decodes the token with Google's Play Integrity API and checks the verdict."""

    def __init__(self):
        self.package = settings.PLAY_INTEGRITY_PACKAGE
        self.credentials_file = settings.PLAY_INTEGRITY_CREDENTIALS_FILE or settings.FCM_CREDENTIALS_FILE

    def decode(self, integrity_token: str) -> dict:
        """Google's tokenPayloadExternal for the token. Raises IntegrityFailed when Google refuses it."""
        if not self.package or not self.credentials_file:
            raise IntegrityFailed("Play Integrity is not configured (PLAY_INTEGRITY_PACKAGE, credentials).")
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            self.credentials_file, scopes=[PLAY_INTEGRITY_SCOPE]
        )
        url = f"https://playintegrity.googleapis.com/v1/{self.package}:decodeIntegrityToken"
        response = AuthorizedSession(credentials).post(
            url, data=json.dumps({"integrityToken": integrity_token}), headers={"Content-Type": "application/json"}, timeout=10
        )
        if response.status_code != 200:
            raise IntegrityFailed(f"Google answered {response.status_code}: {response.text[:200]}")
        return response.json().get("tokenPayloadExternal", {})

    def verify(self, integrity_token: str, nonce: str, now: float | None = None) -> None:
        payload = self.decode(integrity_token)
        request = payload.get("requestDetails", {})
        app = payload.get("appIntegrity", {})
        device = payload.get("deviceIntegrity", {})
        checks = {
            "package": request.get("requestPackageName") == self.package and app.get("packageName") == self.package,
            "nonce": request.get("nonce") == nonce,
            "fresh": _issued_recently(request.get("timestampMillis"), now),
            "app": app.get("appRecognitionVerdict") == "PLAY_RECOGNIZED",
            "device": "MEETS_DEVICE_INTEGRITY" in (device.get("deviceRecognitionVerdict") or []),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise IntegrityFailed(f"Verdict failed: {', '.join(failed)}")


def _issued_recently(timestamp_millis, now: float | None) -> bool:
    try:
        issued = int(timestamp_millis) / 1000
    except (TypeError, ValueError):
        return False
    age = (now if now is not None else time.time()) - issued
    return -60 <= age <= settings.CITIZEN_CHALLENGE_SECONDS


def get_verifier():
    return import_string(settings.CITIZEN_INTEGRITY_VERIFIER)()
