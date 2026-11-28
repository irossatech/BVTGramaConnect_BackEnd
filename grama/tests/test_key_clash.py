"""Two Super Admins creating a service or a bill at the same moment (audit finding BE-DB-014).

Two different names can make the same key ("PAN Card" and "PAN-Card" both make pan_card). When
another create takes the key between the check and the insert, the second create now takes the
next free key instead of failing with a 500. The same name twice still answers 400 name_en.
"""

import pytest

from apps.bills import services as bill_services
from apps.bills.models import Bill
from apps.service_requests import services as service_services
from apps.service_requests.models import Service

pytestmark = pytest.mark.django_db

SERVICES = "/api/v1/admin/services/"
BILLS = "/api/v1/admin/bills/"
SERVICE = {"name_te": "ఆధార్ సేవ", "icon": "ration", "color": "teal"}


def taken_first(monkeypatch, module, key):
    """The first key made is `key`, already in use, as if another create had just taken it."""
    real = module._new_key
    calls = []

    def new_key(name_en):
        calls.append(name_en)
        return key if len(calls) == 1 else real(name_en)

    monkeypatch.setattr(module, "_new_key", new_key)
    return calls


def test_a_service_whose_key_was_just_taken_gets_the_next_one(api, world, monkeypatch):
    calls = taken_first(monkeypatch, service_services, "aadhaar")
    response = api(world.users["super"]).post(SERVICES, {**SERVICE, "name_en": "Aadhaar-Seva"}, format="json")
    assert response.status_code == 201, response.json()
    assert response.json()["key"] == "aadhaar_seva" and len(calls) == 2
    assert Service.objects.filter(key="aadhaar").count() == 1


def test_a_bill_whose_key_was_just_taken_gets_the_next_one(api, world, monkeypatch):
    calls = taken_first(monkeypatch, bill_services, "electricity")
    response = api(world.users["super"]).post(
        BILLS, {"name_en": "Power Bill", "name_te": "కరెంటు బిల్లు", "icon": "gas", "color": "orange"}, format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["key"] == "power_bill" and len(calls) == 2
    assert Bill.objects.filter(key="electricity").count() == 1


def test_the_same_name_at_the_same_moment_still_answers_400(api, world, monkeypatch):
    # The name check before the insert missed the other create; the retry's check sees it.
    original = service_services._check_name
    checks = []

    def late_check(name_en, service=None):
        checks.append(name_en)
        if len(checks) > 1:
            original(name_en, service)

    monkeypatch.setattr(service_services, "_check_name", late_check)
    taken_first(monkeypatch, service_services, "aadhaar")
    Service.objects.filter(key="aadhaar").update(name_en="Aadhaar Seva")
    response = api(world.users["super"]).post(SERVICES, {**SERVICE, "name_en": "Aadhaar Seva"}, format="json")
    assert response.status_code == 400 and "name_en" in response.json()["error"]["fields"]
    assert Service.objects.filter(name_en__iexact="Aadhaar Seva").count() == 1
