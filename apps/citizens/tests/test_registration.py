"""A phone gets its posting token: challenge, Play Integrity, limits. Contract section 15."""

import hashlib
import time

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.citizens import integrity
from apps.citizens.models import CitizenDevice
from apps.geography.models import Village

from .conftest import CHALLENGE, ME, REGISTER, nonce, register

pytestmark = pytest.mark.django_db


def code(response):
    return response.json()["error"]["code"]


def test_challenge_gives_a_fresh_single_use_nonce():
    data = APIClient().post(CHALLENGE).json()
    assert data["expires_in"] == 300
    assert integrity.nonce_is_fresh(data["nonce"])
    assert set(data["nonce"]) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")  # base64url


def test_register_returns_the_token_once_and_stores_only_its_hash(world):
    response = register(world)
    assert response.status_code == 201
    data = response.json()
    token = data["token"]
    assert len(token) >= 40
    assert data["device"]["village"] == {"level": "VILLAGE", "id": world.areas["kolakaluru"].id, "name": "కొలకలూరు"}
    assert data["device"]["blocked"] is False and data["device"]["posts_per_day"] == 3
    device = CitizenDevice.objects.get()
    assert device.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(CitizenDevice.objects.values().get())
    assert device.integrity_checked is False  # dev and tests: CITIZEN_INTEGRITY_REQUIRED off
    me = APIClient(HTTP_X_CITIZEN_TOKEN=token).get(ME)
    assert me.status_code == 200 and me.json()["id"] == str(device.id)


def test_registration_is_audited_without_ip_or_token(world):
    token = register(world).json()["token"]
    entry = AuditLog.objects.get(action="citizen.registered")
    assert entry.via == "citizen" and entry.actor is None
    assert entry.data == {"platform": "android", "integrity_checked": False}
    assert "10.20.30.40" not in str(entry.__dict__) and token not in str(entry.__dict__)


def test_a_nonce_works_once(world):
    used = nonce()
    body = {"nonce": used, "platform": "android", "village_id": world.areas["kolakaluru"].id}
    assert APIClient().post(REGISTER, body, format="json").status_code == 201
    again = APIClient().post(REGISTER, body, format="json")
    assert again.status_code == 400 and "nonce" in again.json()["error"]["fields"]


@pytest.mark.parametrize("bad", ["", "not-a-nonce", "A" * 86])
def test_forged_nonces_are_refused(world, bad):
    body = {"nonce": bad or "x", "platform": "android", "village_id": world.areas["kolakaluru"].id}
    response = APIClient().post(REGISTER, body, format="json")
    assert response.status_code == 400 and "nonce" in response.json()["error"]["fields"]


def test_an_old_nonce_is_refused(world):
    old = integrity.new_nonce(now=time.time() - 301)
    body = {"nonce": old, "platform": "android", "village_id": world.areas["kolakaluru"].id}
    assert APIClient().post(REGISTER, body, format="json").status_code == 400


def test_the_village_must_be_active(world):
    Village.objects.filter(pk=world.areas["kolakaluru"].pk).update(is_active=False)
    response = register(world)
    assert response.status_code == 400 and "village_id" in response.json()["error"]["fields"]
    assert register(world, village="nandivelugu").status_code == 201


def test_five_registrations_per_ip_a_day(world):
    for _ in range(5):
        assert register(world, ip="10.0.0.1").status_code == 201
    sixth = register(world, ip="10.0.0.1")
    assert sixth.status_code == 429 and code(sixth) == "throttled"
    assert 86000 < sixth.json()["error"]["retry_after_s"] <= 86401 and sixth["Retry-After"]
    assert register(world, ip="10.0.0.2").status_code == 201  # another network is not affected
    assert not CitizenDevice.objects.filter(registered_ip_hash="").exists()
    assert "10.0.0.1" not in str(list(CitizenDevice.objects.values_list("registered_ip_hash", flat=True)))


# --- Play Integrity required (production) ---------------------------------------------------------


class Verifier:
    calls = []
    failure = None

    def verify(self, integrity_token, nonce):
        Verifier.calls.append((integrity_token, nonce))
        if Verifier.failure:
            raise integrity.IntegrityFailed(Verifier.failure)


@pytest.fixture
def required(settings):
    settings.CITIZEN_INTEGRITY_REQUIRED = True
    settings.CITIZEN_INTEGRITY_VERIFIER = "apps.citizens.tests.test_registration.Verifier"
    Verifier.calls, Verifier.failure = [], None
    return Verifier


def test_genuine_app_gets_a_token(world, required):
    response = register(world)
    assert response.status_code == 201
    assert required.calls and required.calls[0][0] == "play-token"
    assert CitizenDevice.objects.get().integrity_checked is True


def test_failed_verdict_gets_nothing(world, required):
    required.failure = "Verdict failed: device"
    response = register(world)
    assert response.status_code == 403 and code(response) == "integrity_failed"
    assert "device" not in response.json()["error"]["message"]  # the reason stays in the server log
    assert not CitizenDevice.objects.exists()


def test_iphones_wait_for_app_attest_while_integrity_is_required(world, required):
    response = register(world, platform="ios")
    assert response.status_code == 403 and code(response) == "integrity_failed"
    assert not required.calls


# --- The verdict itself -----------------------------------------------------------------------------


def verdict(nonce_value, **changes):
    payload = {
        "requestDetails": {
            "requestPackageName": "com.bvt.gramaconnect", "nonce": nonce_value,
            "timestampMillis": str(int(time.time() * 1000)),
        },
        "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED", "packageName": "com.bvt.gramaconnect"},
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    for path, value in changes.items():
        section, key = path.split("__")
        payload[section][key] = value
    return payload


@pytest.fixture
def play(settings, monkeypatch):
    settings.PLAY_INTEGRITY_PACKAGE = "com.bvt.gramaconnect"
    verifier = integrity.PlayIntegrityVerifier()
    box = {}
    monkeypatch.setattr(verifier, "decode", lambda token: box["payload"])
    return verifier, box


def test_a_genuine_verdict_passes(play):
    verifier, box = play
    box["payload"] = verdict("n-1")
    verifier.verify("token", "n-1")


@pytest.mark.parametrize(
    "changes",
    [
        {"requestDetails__nonce": "another"},
        {"requestDetails__requestPackageName": "com.fake.app"},
        {"appIntegrity__packageName": "com.fake.app"},
        {"appIntegrity__appRecognitionVerdict": "UNRECOGNIZED_VERSION"},
        {"deviceIntegrity__deviceRecognitionVerdict": []},
        {"deviceIntegrity__deviceRecognitionVerdict": ["MEETS_VIRTUAL_INTEGRITY"]},
        {"requestDetails__timestampMillis": str(int((time.time() - 3600) * 1000))},
        {"requestDetails__timestampMillis": "soon"},
    ],
)
def test_any_doubt_fails_the_verdict(play, changes):
    verifier, box = play
    box["payload"] = verdict("n-1", **changes)
    with pytest.raises(integrity.IntegrityFailed):
        verifier.verify("token", "n-1")


def test_unconfigured_play_integrity_fails_closed(settings):
    settings.PLAY_INTEGRITY_PACKAGE = ""
    with pytest.raises(integrity.IntegrityFailed, match="not configured"):
        integrity.PlayIntegrityVerifier().verify("token", "n-1")


def test_prod_settings_turn_integrity_on():
    """prod.py needs production env to import, so read it: integrity is on unless env turns it off."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "grama" / "settings" / "prod.py").read_text(encoding="utf-8")
    assert 'CITIZEN_INTEGRITY_REQUIRED = env.bool("CITIZEN_INTEGRITY_REQUIRED", default=True)' in text
