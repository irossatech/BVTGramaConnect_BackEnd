"""Service requests (docs/api-contract.md, section 16): citizens' phones come from the citizens tests."""

import pytest

from apps.citizens.tests.conftest import phone  # noqa: F401  (fixture: registers a citizen phone)

MINE = "/api/v1/citizen/service-requests/"
LIST = "/api/v1/service-requests/"


def ask(citizen, **changes):
    body = {"service": "aadhaar", "name": "Lakshmi Devi", "phone": "9876543210",
            "email": "lakshmi@example.com", "details": "Address change in my Aadhaar card", **changes}
    return citizen.client.post(MINE, body, format="json")


@pytest.fixture
def request_from(phone, django_capture_on_commit_callbacks):  # noqa: F811
    """request_from("kolakaluru") -> (phone, request id): a NEW request from a phone in that village."""

    def make(village="kolakaluru", ip="10.20.30.40", **changes):
        citizen = phone(village, ip=ip)
        with django_capture_on_commit_callbacks(execute=True):
            response = ask(citizen, **changes)
        assert response.status_code == 201, response.json()
        return citizen, response.json()["id"]

    return make
