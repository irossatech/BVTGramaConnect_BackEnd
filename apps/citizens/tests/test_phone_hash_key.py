"""PHONE_HASH_KEY: changing SECRET_KEY keeps citizen phones recognised (BE-SEC-109, BVTGC-111).

A phone is recognised across reinstalls by a keyed hash of its Android ID; its daily limit and any
block follow that hash. The key is PHONE_HASH_KEY, or SECRET_KEY while it is empty.
"""

import hashlib
import hmac
import secrets

import pytest

from apps.citizens.models import CitizenDevice
from apps.posts.models import Post
from apps.citizens.services import hash_android_id, hash_ip
from grama.tests.test_security_gaps import import_prod

pytestmark = pytest.mark.django_db

ANDROID_ID = "a1b2c3d4e5f60718"


def expected(key: str, kind: bytes, value: str) -> str:
    return hmac.new(key.encode(), kind + b":" + value.encode(), hashlib.sha256).hexdigest()


def rotate_secret_key(settings, *, keep_phones: bool):
    """What deploy/RUNBOOK.md says: copy the old SECRET_KEY to PHONE_HASH_KEY, then change SECRET_KEY."""
    if keep_phones:
        settings.PHONE_HASH_KEY = settings.SECRET_KEY
    settings.SECRET_KEY = secrets.token_urlsafe(50)


def test_without_the_setting_the_hashes_are_exactly_as_before(settings):
    settings.PHONE_HASH_KEY = ""
    assert hash_android_id(ANDROID_ID) == expected(settings.SECRET_KEY, b"citizen-android-id", ANDROID_ID)
    assert hash_ip("10.20.30.40") == expected(settings.SECRET_KEY, b"citizen-ip", "10.20.30.40")


def test_with_the_setting_the_hashes_use_it(settings):
    settings.PHONE_HASH_KEY = key = secrets.token_urlsafe(50)
    assert hash_android_id(ANDROID_ID) == expected(key, b"citizen-android-id", ANDROID_ID)
    assert hash_ip("10.20.30.40") == expected(key, b"citizen-ip", "10.20.30.40")
    assert hash_android_id(ANDROID_ID) != expected(settings.SECRET_KEY, b"citizen-android-id", ANDROID_ID)


def test_the_stored_hash_of_a_phone_stays_valid_after_the_rotation(world, phone, settings):
    phone(android_id=ANDROID_ID)
    stored = CitizenDevice.objects.get().android_id_hash
    rotate_secret_key(settings, keep_phones=True)
    assert hash_android_id(ANDROID_ID) == stored


def test_the_daily_limit_survives_a_secret_key_change_and_a_reinstall(world, phone, settings):
    settings.CITIZEN_POSTS_PER_DAY = 2
    first = phone(android_id=ANDROID_ID)
    first.post(title="ఒకటి")
    first.post(title="రెండు")
    rotate_secret_key(settings, keep_phones=True)
    again = phone(android_id=ANDROID_ID, ip="10.8.8.8")  # same phone, new install, new token
    assert again.token != first.token
    assert again.post(title="మూడు").status_code == 429


def block_for_30_days(api, world, citizen, capture):
    with capture(execute=True):
        post = Post.objects.get(pk=citizen.post().json()["id"])
    response = api(world.users["super"]).post(
        f"/api/v1/posts/{post.id}/block-citizen/", {"comment": "Spam", "days": 30}, format="json"
    )
    assert response.status_code == 200, response.json()


def test_a_block_survives_a_secret_key_change_and_a_reinstall(api, world, phone, settings, django_capture_on_commit_callbacks):
    first = phone(android_id=ANDROID_ID)
    block_for_30_days(api, world, first, django_capture_on_commit_callbacks)
    rotate_secret_key(settings, keep_phones=True)
    reinstalled = phone(android_id=ANDROID_ID, ip="10.7.7.7")
    assert reinstalled.token != first.token
    refused = reinstalled.post(title="కొత్త ఇన్‌స్టాల్")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "citizen_blocked"


def test_without_the_first_step_the_phone_looks_new(api, world, phone, settings, django_capture_on_commit_callbacks):
    """Why the RUNBOOK step matters: SECRET_KEY changed alone, the same phone is a stranger again."""
    settings.PHONE_HASH_KEY = ""
    first = phone(android_id=ANDROID_ID)
    block_for_30_days(api, world, first, django_capture_on_commit_callbacks)
    rotate_secret_key(settings, keep_phones=False)
    reinstalled = phone(android_id=ANDROID_ID, ip="10.7.7.7")
    assert reinstalled.token != first.token
    assert reinstalled.post(title="కొత్త ఇన్‌స్టాల్").status_code == 201


def test_the_default_is_empty():
    from django.conf import settings

    assert settings.PHONE_HASH_KEY == ""


@pytest.mark.parametrize("key", ["change-me-to-a-long-random-string", "short-Q7x9", "x" * 60])
def test_production_refuses_a_weak_phone_hash_key(key, monkeypatch):
    monkeypatch.setenv("PHONE_HASH_KEY", key)
    result = import_prod(secrets.token_urlsafe(50))
    assert result.returncode != 0 and "PHONE_HASH_KEY is an example value or too weak" in result.stderr


def test_production_starts_with_the_previous_secret_key_as_phone_hash_key(monkeypatch):
    monkeypatch.setenv("PHONE_HASH_KEY", secrets.token_urlsafe(50))
    result = import_prod(secrets.token_urlsafe(50))
    assert result.returncode == 0, result.stderr
