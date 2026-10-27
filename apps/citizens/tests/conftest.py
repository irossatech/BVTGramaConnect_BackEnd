"""Citizen phones and their posts (docs/api-contract.md, section 15)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

CHALLENGE = "/api/v1/citizen/challenge/"
REGISTER = "/api/v1/citizen/register/"
ME = "/api/v1/citizen/me/"
POSTS = "/api/v1/citizen/posts/"


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def photo(size=(1600, 1200), name="photo.jpg") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, (31, 138, 59)).save(buffer, "JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def nonce(ip="10.20.30.40") -> str:
    return APIClient(REMOTE_ADDR=ip).post(CHALLENGE).json()["nonce"]


def register(world, village="kolakaluru", ip="10.20.30.40", **extra):
    """POST /citizen/register/ from `ip`; returns the response."""
    body = {"nonce": nonce(ip), "integrity_token": "play-token", "platform": "android",
            "village_id": world.areas[village].id, "app_version": "1.4.0", **extra}
    return APIClient(REMOTE_ADDR=ip).post(REGISTER, body, format="json")


class Phone:
    """A registered citizen phone: phone.client sends its X-Citizen-Token."""

    def __init__(self, token: str, device_id: str):
        self.token = token
        self.device_id = device_id
        self.client = APIClient(HTTP_X_CITIZEN_TOKEN=token)

    def post(self, title="తాగునీటి పైపు పగిలింది", body="మెయిన్ రోడ్డు దగ్గర నీరు వృథా అవుతోంది.", photos=(), **extra):
        data = {"category": "news", "language": "te", "title": title, "body": body, **extra}
        if photos:
            data["photos"] = list(photos)
        return self.client.post(POSTS, data, format="multipart")


@pytest.fixture
def phone(world):
    """make_phone("kolakaluru") registers a phone for that village."""

    def make(village="kolakaluru", ip="10.20.30.40") -> Phone:
        data = register(world, village, ip).json()
        return Phone(data["token"], data["device"]["id"])

    return make
