"""Three small races (audit finding BE-DB-022). The other, simultaneous request is stood in for by letting
the first check through, or by changing the row behind the caller's back.

- A refresh token used by two requests at the same moment gives new tokens to one of them only.
- A service-request change starts from the saved status, so history and audit name the real "from".
- One django-q2 schedule per name, whatever ran before; duplicates are removed.
"""

import uuid
from unittest import mock

import pytest
from django_q.models import Schedule
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenDevice
from apps.posts.tasks import ESCALATION_SCHEDULE, ensure_escalation_schedule
from apps.service_requests import services as requests
from apps.service_requests.models import Service, ServiceRequest
from grama.housekeeping import HOUSEKEEPING_SCHEDULE, ensure_housekeeping_schedule

pytestmark = pytest.mark.django_db
REFRESH = "/api/v1/auth/refresh/"


def test_a_refresh_token_used_twice_at_once_works_once(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    first = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200
    # The second request read the token before the first one blacklisted it: its check saw nothing.
    with mock.patch.object(RefreshToken, "check_blacklist"):
        second = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert second.status_code == 401 and second.json()["error"]["code"] == "session_revoked"


def test_a_service_request_change_starts_from_the_saved_status(world):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n",
    )
    request = ServiceRequest.objects.create(
        service=Service.objects.first(), name="x", phone="9000000000", address="a",
        village_id=world.ref("kolakaluru").id, device=device, assigned_to=world.users["va_kolakaluru"],
    )
    stale = ServiceRequest.objects.select_related("service").get(pk=request.pk)  # read while still NEW
    ServiceRequest.objects.filter(pk=request.pk).update(status="IN_PROGRESS")  # another change landed
    requests.update_request(world.users["va_kolakaluru"], stale, status="COMPLETED", note="done")
    entry = AuditLog.objects.get(action="service_request.status_changed")
    assert entry.data["from"] == "IN_PROGRESS" and entry.data["to"] == "COMPLETED"


def test_a_change_the_other_request_already_made_writes_nothing(world):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n",
    )
    request = ServiceRequest.objects.create(
        service=Service.objects.first(), name="x", phone="9000000000", address="a",
        village_id=world.ref("kolakaluru").id, device=device, assigned_to=world.users["va_kolakaluru"],
    )
    stale = ServiceRequest.objects.select_related("service").get(pk=request.pk)
    ServiceRequest.objects.filter(pk=request.pk).update(status="APPROVED")
    requests.update_request(world.users["va_kolakaluru"], stale, status="APPROVED")
    assert not AuditLog.objects.filter(action="service_request.status_changed").exists()


@pytest.mark.parametrize(
    "ensure, name",
    [(ensure_escalation_schedule, ESCALATION_SCHEDULE), (ensure_housekeeping_schedule, HOUSEKEEPING_SCHEDULE)],
)
def test_one_schedule_per_name(db, ensure, name):
    ensure()
    Schedule.objects.create(name=name, func="old.duplicate")  # what a second, simultaneous deploy left
    ensure()
    ensure()
    assert Schedule.objects.filter(name=name).count() == 1
