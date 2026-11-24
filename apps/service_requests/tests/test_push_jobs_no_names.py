"""A citizen's name never sits in the push job tables (BVTGC-78, audit CROSS-PII-001).

Officials' pushes about a service request name the citizen ("Ravi sent a new … request"). The job
is queued with the request id, the officials' ids and the template key only, and writes the text
itself when it sends; a retry is scheduled the same way. The pushes read exactly as before.
"""

import ast

import pytest
from django_q.models import Schedule

from apps.notifications import services as notification_services
from apps.notifications import tasks
from apps.notifications.models import Device
from apps.notifications.push import LocmemPushGateway

pytestmark = pytest.mark.django_db
NAME = "రవి కుమార్ Xyzname"


@pytest.fixture
def queued(monkeypatch):
    """Every async_task the notification services queue: (func, args), and the job still runs."""
    calls = []
    original = notification_services.async_task

    def record(func, *args, **kwargs):
        calls.append((func, args))
        return original(func, *args, **kwargs)

    monkeypatch.setattr(notification_services, "async_task", record)
    return calls


@pytest.fixture
def admin_phone(world):
    LocmemPushGateway.outbox.clear()
    LocmemPushGateway.failures.clear()
    return Device.objects.create(fcm_token="tok-va", platform="android", user=world.users["va_kolakaluru"])


def test_the_job_gets_ids_only_and_the_push_still_names_the_citizen(world, request_from, queued, admin_phone):
    _phone, request_id = request_from("kolakaluru", name=NAME)
    jobs = [(func, args) for func, args in queued if func == notification_services.SERVICE_REQUEST_PUSHES_TASK]
    assert jobs, "the service-request push job was queued"
    for _func, args in jobs:
        assert args[0] == request_id
        assert "Xyzname" not in repr(args) and "రవి" not in repr(args)
    sent = [message for message in LocmemPushGateway.outbox if message.token == "tok-va"]
    assert sent and NAME in sent[0].title  # the Village Admin's push reads as before
    assert sent[0].data["request_id"] == str(request_id)


def test_a_retry_carries_ids_only_and_writes_the_text_again(world, request_from, admin_phone, settings):
    settings.DELIVERY_RETRY_BASE_SECONDS = 1.0  # a passing failure becomes a scheduled retry
    LocmemPushGateway.failures.append(RuntimeError("503"))
    _phone, request_id = request_from("kolakaluru", name=NAME)
    retry = Schedule.objects.get(func=tasks.SERVICE_REQUEST_DEVICE_TASK)
    assert "Xyzname" not in retry.args and "రవి" not in retry.args
    device_id, scheduled_request, template_key, attempt = ast.literal_eval(retry.args)
    assert (device_id, scheduled_request, attempt) == (str(admin_phone.pk), request_id, 2)

    assert tasks.send_service_request_push_to_device(*ast.literal_eval(retry.args)) is True
    [message] = [m for m in LocmemPushGateway.outbox if m.token == "tok-va"]
    assert NAME in message.title and message.data["request_id"] == str(request_id)
    assert template_key in {"service_request_new", "service_request_assigned_you"}
