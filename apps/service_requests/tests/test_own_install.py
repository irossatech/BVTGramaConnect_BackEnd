"""A phone reads only the service requests made from its own install (decided 2026-11-09 on audit
BE_01, BE-SEC-002). The Android ID that links installs is sent by the app and not verified."""

from apps.citizens.tests.conftest import phone  # noqa: F401  (fixture)

from .conftest import MINE, ask


def test_another_install_with_the_same_android_id_cannot_read_the_requests(world, phone):  # noqa: F811
    lakshmi = phone("kolakaluru", android_id="a1b2c3d4e5f6a7b8")
    assert ask(lakshmi).status_code == 201
    # Someone registers claiming Lakshmi's Android ID (or Lakshmi reinstalls the app).
    other = phone("kolakaluru", android_id="a1b2c3d4e5f6a7b8")
    assert other.client.get(MINE).json() == []
    mine = lakshmi.client.get(MINE).json()
    assert len(mine) == 1 and mine[0]["phone"] == "9876543210"


def test_the_daily_limit_still_counts_every_install(world, phone, settings):  # noqa: F811
    settings.SERVICE_REQUESTS_PER_DAY = 1
    first = phone("kolakaluru", android_id="a1b2c3d4e5f6a7b8")
    assert ask(first).status_code == 201
    reinstalled = phone("kolakaluru", android_id="a1b2c3d4e5f6a7b8")
    response = ask(reinstalled)
    assert response.status_code == 429 and response.json()["error"]["code"] == "throttled"
