"""Fixtures for important numbers. The map and officials come from the root conftest (world, api)."""

import pytest

from apps.contacts.models import Contact, ContactType, PhoneKind
from apps.contacts.phones import normalise_phone

URL = "/api/v1/contacts/"
ADMIN_URL = "/api/v1/admin/contacts/"


@pytest.fixture
def number(world):
    """number(area_key, type_key, name, phone, **fields) writes a row directly, bypassing the API's rules."""

    def make(area_key, type_key="official", name="Office", phone="08644222333", **fields) -> Contact:
        contact_type = ContactType.objects.get(key=type_key)
        digits, kind = normalise_phone(phone, allow_short=True)
        ref = world.ref(area_key)
        return Contact.objects.create(
            type=contact_type, name=name, phone=digits, phone_kind=fields.pop("phone_kind", kind),
            area_level=ref.level, area_id=ref.id, consent_recorded=kind == PhoneKind.MOBILE, **fields,
        )

    return make


def area_body(world, area_key, **extra) -> dict:
    """A valid POST body for an area number (a landline, so no consent is needed)."""
    return {"type": "official", "name": "MPDO Office", "phone": "08644222333", "area": world.area_json(area_key), **extra}


def helpline_body(world, **extra) -> dict:
    return {"type": "health", "name": "Ambulance", "phone": "108", "area": world.area_json("ap"), **extra}
