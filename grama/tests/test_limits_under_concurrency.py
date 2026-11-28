"""Limits and unique values hold when two requests arrive at the same moment (audit finding BE-DB-014).

Each limit is checked before the write and again, inside the same transaction, after it. Here the early
check is let through to stand for the other request that passed it at the same time: the second check
must refuse and undo the write. A unique value taken that way answers 409 duplicate, never a 500.
"""

import uuid
from unittest import mock

import pytest
from django.db import IntegrityError
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.citizens import services as citizens
from apps.citizens.models import CitizenDevice
from apps.contacts import services as contacts
from apps.contacts.models import Contact, ContactType
from apps.notifications.models import Device
from apps.posts.models import Category, Post, PostStatus
from apps.service_requests import services as requests
from apps.service_requests.models import Service, ServiceRequest
from conftest import TEST_PASSWORD, make_bare_post

pytestmark = pytest.mark.django_db


def early_check_passes(module, name):
    """Patch a limit helper so its first call (before the write) passes, as for the concurrent request."""
    real = getattr(module, name)

    def check(*args, written=False, **kwargs):
        if written:
            return real(*args, written=True, **kwargs)
        return None

    return mock.patch.object(module, name, side_effect=check)


@pytest.fixture
def device(world):
    return CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id,
        nonce=uuid.uuid4().hex, registered_ip_hash="ip-1",
    )


def citizen_post(world, device):
    ref = world.ref("kolakaluru")
    return make_bare_post(
        citizen=device, posted_as_role="CITIZEN", posted_as_level=ref.level, posted_as_area_id=ref.id,
        category=Category.objects.first(), title="t", body="b", target_level=ref.level, target_area_id=ref.id,
        status=PostStatus.PENDING, submitted_at=timezone.now(),
    )


def send_post(device):
    return citizens.create_post(device, category=Category.objects.get(key="news"), language="te", title="t", body="b")


def test_citizen_posts_per_day(world, device, settings):
    settings.CITIZEN_POSTS_PER_DAY = 2
    citizen_post(world, device)
    send_post(device)  # the second of two: fine
    with early_check_passes(citizens, "_within_daily_limit"), pytest.raises(Exception) as refused:
        send_post(device)  # a third that slipped past the early check
    assert refused.value.error_code == "throttled"
    assert Post.objects.filter(citizen=device).count() == 2


def test_service_requests_per_day(world, device, settings):
    settings.SERVICE_REQUESTS_PER_DAY = 1
    ask = {"service": Service.objects.first().key, "name": "రాము", "phone": "9000000000", "address": "a"}
    requests.create_request(device, **ask)
    with early_check_passes(requests, "_within_daily_limit"), pytest.raises(Exception) as refused:
        requests.create_request(device, **ask)
    assert refused.value.error_code == "throttled"
    assert ServiceRequest.objects.count() == 1


def test_registrations_per_network(world, settings):
    from apps.citizens.tests.conftest import register

    settings.CITIZEN_REGISTRATIONS_PER_IP_PER_DAY = 1
    assert register(world, ip="10.9.9.9").status_code == 201
    with early_check_passes(citizens, "_registrations_within"):
        response = register(world, ip="10.9.9.9")
    assert response.status_code == 429
    assert CitizenDevice.objects.count() == 1


def test_numbers_per_area(world, settings):
    settings.CONTACTS_MAX_PER_AREA = 1
    area = world.ref("kolakaluru")
    contact_type = ContactType.objects.live().filter(section="AREA", is_active=True).exclude(key="other").first()
    data = {"type": contact_type.key, "area": area, "name": "Office", "phone": "08644222333"}
    contacts.create_contact(world.users["super"], data)
    real = contacts.check_capacity

    def capacity(area, exclude_id=None, extra=1):
        return None if extra else real(area, exclude_id, extra)

    with mock.patch.object(contacts, "check_capacity", side_effect=capacity), pytest.raises(Exception) as refused:
        contacts.create_contact(world.users["super"], {**data, "phone": "08644222444"})
    assert refused.value.error_code == "validation_error"
    assert Contact.objects.filter(is_active=True).count() == 1


def test_a_username_taken_at_the_same_moment_is_409(api, world):
    User.objects.create_user(username="ravi.va", name="Other Ravi", password="x")  # the other request, saved
    real_exists = QuerySet.exists
    missed = {"done": False}

    def early_check_misses_it(self):
        # The early "is this username taken?" ran before the other request saved: it saw nothing.
        if self.model is User and "username" in str(self.query) and not missed["done"]:
            missed["done"] = True
            return False
        return real_exists(self)

    body = {"name": "Ravi", "username": "ravi.va", "role": Role.VILLAGE_ADMIN,
            "area": world.area_json("kolakaluru"), "current_password": TEST_PASSWORD}
    with mock.patch.object(QuerySet, "exists", autospec=True, side_effect=early_check_misses_it):
        response = api(world.users["da_guntur"]).post("/api/v1/users/", body, format="json")
    assert missed["done"]
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate" and "username" in response.json()["error"]["fields"]


def test_a_push_token_registered_at_the_same_moment_is_updated(world):
    Device.objects.create(fcm_token="tok-1", platform="android")  # the other request, saved

    def other_request_first(fcm_token, defaults):
        # update_or_create looked before the other request saved, so it tried an insert.
        raise IntegrityError("UNIQUE constraint failed: notifications_device.fcm_token")

    with mock.patch.object(Device.objects, "update_or_create", side_effect=other_request_first):
        response = APIClient().post(
            "/api/v1/devices/", {"fcm_token": "tok-1", "platform": "android", "language": "en"}, format="json"
        )
    assert response.status_code == 200
    assert Device.objects.get(fcm_token="tok-1").language == "en"
