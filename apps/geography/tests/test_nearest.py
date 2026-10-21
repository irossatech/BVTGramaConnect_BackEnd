"""Nearest village: bounding box in the ORM, haversine in Python, widening box."""

from decimal import Decimal

import pytest

from apps.geography.models import Village
from apps.geography.nearest import KM_PER_DEGREE_LAT, bounding_box, haversine_km, nearest_village

pytestmark = pytest.mark.django_db


def test_haversine_one_degree_of_latitude():
    assert haversine_km(16.0, 80.0, 17.0, 80.0) == pytest.approx(111.19, abs=0.05)
    assert haversine_km(16.2381, 80.5924, 16.2381, 80.5924) == 0


def test_bounding_box_contains_the_circle():
    min_lat, max_lat, min_lng, max_lng = bounding_box(16.0, 80.0, 10)
    assert haversine_km(16.0, 80.0, float(max_lat), 80.0) == pytest.approx(10, abs=0.01)
    assert haversine_km(16.0, 80.0, 16.0, float(max_lng)) == pytest.approx(10, abs=0.05)
    assert min_lat < 16 < max_lat and min_lng < 80 < max_lng


def test_exact_position_returns_that_village(api, geo):
    response = api().get("/api/v1/geo/nearest/?lat=16.2381&lng=80.5924")
    assert response.status_code == 200
    data = response.json()
    assert data["village"]["id"] == geo.areas["kolakaluru"].id
    assert data["distance_km"] == 0
    assert data["village"]["path"] == "కొలకలూరు · తెనాలి · గుంటూరు"


def test_picks_the_closer_of_two_villages(api, geo):
    # Just north-east of Nandivelugu, about 3.9 km from Kolakaluru.
    data = api().get("/api/v1/geo/nearest/?lat=16.2200&lng=80.6200").json()
    assert data["village"]["id"] == geo.areas["nandivelugu"].id
    assert data["distance_km"] < 1


def test_box_widens_when_nothing_is_close(geo, settings):
    settings.NEAREST_VILLAGE_START_KM = 1
    settings.NEAREST_VILLAGE_MAX_KM = 50
    # About 20 km west of Kolakaluru: nothing within 1, 2, 4, 8 or 16 km.
    village, distance = nearest_village(16.2381, 80.4051)
    assert village == geo.areas["kolakaluru"]
    assert 19 < distance < 21


def test_nothing_within_the_limit_is_404(api, geo, settings):
    settings.NEAREST_VILLAGE_MAX_KM = 50
    response = api().get("/api/v1/geo/nearest/?lat=13.0&lng=80.2")  # Chennai
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_village_in_the_box_corner_does_not_beat_a_closer_one_outside(geo):
    """A box corner is farther than its edge, so the box must widen before answering."""
    Village.objects.update(is_active=False)
    lat, lng, radius = 20.0, 85.0, 5.0
    mandal = geo.areas["tenali"]
    deg_lat = radius / KM_PER_DEGREE_LAT
    _, _, _, max_lng = bounding_box(lat, lng, radius)
    corner = Village.objects.create(
        mandal=mandal, name_en="Corner",
        latitude=Decimal(str(round(lat + deg_lat * 0.98, 6))),
        longitude=Decimal(str(round(float(max_lng) - 0.0005, 6))),
    )
    north = Village.objects.create(
        mandal=mandal, name_en="North",
        latitude=Decimal(str(round(lat + deg_lat * 1.2, 6))), longitude=Decimal(str(lng)),
    )
    corner_km = haversine_km(lat, lng, float(corner.latitude), float(corner.longitude))
    north_km = haversine_km(lat, lng, float(north.latitude), float(north.longitude))
    assert radius < north_km < corner_km  # the closer village lies outside the first box

    village, distance = nearest_village(lat, lng, start_km=radius, max_km=50)
    assert village == north
    assert distance == pytest.approx(north_km)


def test_inactive_villages_and_villages_without_coordinates_are_skipped(api, geo):
    Village.objects.filter(pk=geo.areas["kolakaluru"].pk).update(is_active=False)
    Village.objects.filter(pk=geo.areas["nandivelugu"].pk).update(latitude=None, longitude=None)
    data = api().get("/api/v1/geo/nearest/?lat=16.2381&lng=80.5924").json()
    assert data["village"]["id"] not in {geo.areas["kolakaluru"].id, geo.areas["nandivelugu"].id}


def test_villages_under_an_inactive_mandal_are_skipped(api, geo):
    tenali = geo.areas["tenali"]
    tenali.is_active = False
    tenali.save()
    data = api().get("/api/v1/geo/nearest/?lat=16.2381&lng=80.5924").json()
    assert data["village"]["mandal"]["id"] != tenali.id


@pytest.mark.parametrize("query", ["", "?lat=16.2", "?lat=91&lng=80", "?lat=16&lng=181", "?lat=abc&lng=80"])
def test_bad_coordinates_are_rejected(api, geo, query):
    response = api().get(f"/api/v1/geo/nearest/{query}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
