import pytest

from apps.notifications.models import Device
from apps.notifications.push import LocmemPushGateway


@pytest.fixture(autouse=True)
def clean_gateways():
    for box in (LocmemPushGateway.outbox, LocmemPushGateway.failures):
        box.clear()
    yield
    for box in (LocmemPushGateway.outbox, LocmemPushGateway.failures):
        box.clear()


@pytest.fixture
def push_outbox():
    return LocmemPushGateway.outbox


@pytest.fixture
def devices(world):
    """One registered phone per official, token "tok-<name>"."""
    return {
        name: Device.objects.create(fcm_token=f"tok-{name}", platform="android", user=user)
        for name, user in world.users.items()
    }
