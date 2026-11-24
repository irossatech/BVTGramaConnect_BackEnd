"""Banners per citizen Home, set by the Super Admin (BVTGC-92, owner's rule of 2026-11-24).

ads_banner_limit in GET/PATCH /admin/settings/: 0 = no limit (the default), N = at most N, newest first.
GET /ads/?village_id= follows it, and GET /app-config/ carries the same number for the app.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ads.models import Ad
from apps.audit.models import AuditLog
from apps.geography.models import AreaLevel

pytestmark = pytest.mark.django_db
SETTINGS = "/api/v1/admin/settings/"
ADS = "/api/v1/ads/"
CONFIG = "/api/v1/app-config/"


@pytest.fixture
def banners(world):
    """12 live banners for kolakaluru (titles b01..b12, b12 the newest), and some that never show."""
    village = world.areas["kolakaluru"]
    for n in range(1, 13):
        Ad.objects.create(title=f"b{n:02d}", image=f"ads/b{n}.webp", area_level=AreaLevel.VILLAGE, area_id=village.id)
    today = timezone.localdate()
    Ad.objects.create(title="off", image="ads/off.webp", area_level=AreaLevel.VILLAGE, area_id=village.id, is_active=False)
    Ad.objects.create(title="ended", image="ads/e.webp", area_level=AreaLevel.VILLAGE, area_id=village.id,
                      ends_on=today - timedelta(days=1))
    Ad.objects.create(title="elsewhere", image="ads/x.webp", area_level=AreaLevel.VILLAGE,
                      area_id=world.areas["kaza"].id)
    return village


def titles(api, village):
    response = api().get(ADS, {"village_id": village.id})
    assert response.status_code == 200
    return [row["title"] for row in response.json()]


def set_limit(api, world, value, user="super"):
    return api(world.users[user]).patch(SETTINGS, {"ads_banner_limit": value}, format="json")


def test_by_default_every_live_banner_is_shown(api, world, banners):
    assert api(world.users["super"]).get(SETTINGS).json()["ads_banner_limit"] == 0
    shown = titles(api, banners)
    assert shown == [f"b{n:02d}" for n in range(12, 0, -1)]  # all 12, newest first
    assert not {"off", "ended", "elsewhere"} & set(shown)  # area, dates and switch still apply


def test_a_limit_shows_the_newest_n_and_0_brings_all_back(api, world, banners):
    response = set_limit(api, world, 3)
    assert response.status_code == 200 and response.json()["ads_banner_limit"] == 3
    assert api(world.users["super"]).get(SETTINGS).json()["ads_banner_limit"] == 3
    assert titles(api, banners) == ["b12", "b11", "b10"]

    assert set_limit(api, world, 0).status_code == 200
    assert len(titles(api, banners)) == 12


def test_the_app_gets_the_same_number(api, world):
    assert api().get(CONFIG).json()["ads_banner_limit"] == 0
    set_limit(api, world, 5)
    assert api().get(CONFIG).json()["ads_banner_limit"] == 5


@pytest.mark.parametrize("value", [-1, 1001, "many"])
def test_out_of_range_values_are_refused(api, world, value):
    response = set_limit(api, world, value)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error" and "ads_banner_limit" in error["fields"]


@pytest.mark.parametrize("user", ["da_guntur", "va_kolakaluru"])
def test_only_the_super_admin_may_change_it(api, world, user):
    response = set_limit(api, world, 3, user=user)
    assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"
    assert api(world.users["super"]).get(SETTINGS).json()["ads_banner_limit"] == 0


def test_the_change_is_audited_with_before_and_after(api, world):
    set_limit(api, world, 4)
    entry = AuditLog.objects.filter(action="setting.changed").latest("created_at")
    assert entry.data["before"]["ads_banner_limit"] == 0 and entry.data["after"]["ads_banner_limit"] == 4
