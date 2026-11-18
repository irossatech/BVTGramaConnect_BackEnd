"""A service request sent again after a lost answer (client_ref, MOB-ERR-004): one request, not two."""

import uuid

import pytest

from apps.audit.models import AuditLog
from apps.service_requests import services
from apps.service_requests.models import ServiceRequest

from .conftest import ask

pytestmark = pytest.mark.django_db


def test_the_same_client_ref_twice_is_one_request(world, phone):
    citizen = phone()
    ref = str(uuid.uuid4())
    first = ask(citizen, client_ref=ref)
    assert first.status_code == 201
    again = ask(citizen, client_ref=ref)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert ServiceRequest.objects.count() == 1
    assert AuditLog.objects.filter(action="service_request.created").count() == 1


def test_a_repeat_does_not_use_a_daily_request(world, phone, settings):
    settings.SERVICE_REQUESTS_PER_DAY = 1
    citizen = phone()
    ref = str(uuid.uuid4())
    assert ask(citizen, client_ref=ref).status_code == 201
    assert ask(citizen, client_ref=ref).status_code == 200
    assert ask(citizen, name="రమేష్ బాబు", client_ref=str(uuid.uuid4())).status_code == 429


def test_another_phone_may_use_the_same_client_ref(world, phone):
    ref = str(uuid.uuid4())
    assert ask(phone(), client_ref=ref).status_code == 201
    assert ask(phone("nandivelugu", ip="10.9.9.9"), client_ref=ref).status_code == 201
    assert ServiceRequest.objects.count() == 2


def test_without_client_ref_every_send_is_a_new_request(world, phone):
    citizen = phone()
    assert ask(citizen).status_code == 201
    assert ask(citizen).status_code == 201
    assert ServiceRequest.objects.count() == 2


def test_a_client_ref_that_is_not_a_uuid_is_refused(world, phone):
    response = ask(phone(), client_ref="12345")
    assert response.status_code == 400
    assert "client_ref" in response.json()["error"]["fields"]


def test_two_repeats_at_the_same_moment_make_one_request(world, phone, monkeypatch):
    citizen = phone()
    ref = str(uuid.uuid4())
    first = ask(citizen, client_ref=ref).json()
    real = services._earlier_request
    looks = []

    def first_look_misses(device, client_ref):
        looks.append(client_ref)
        return None if len(looks) == 1 else real(device, client_ref)

    monkeypatch.setattr(services, "_earlier_request", first_look_misses)
    again = ask(citizen, client_ref=ref)
    assert len(looks) == 2
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert ServiceRequest.objects.count() == 1
