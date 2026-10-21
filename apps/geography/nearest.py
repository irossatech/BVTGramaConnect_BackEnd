"""Nearest village to a GPS position, without spatial database features.

1. Filter villages to a bounding box around the point with plain ORM range
   lookups on latitude and longitude (indexed together).
2. Measure each candidate with the haversine formula in Python.
3. If the closest candidate is farther than the box's radius, a closer
   village could sit just outside the box, so double the radius and look
   again, up to NEAREST_VILLAGE_MAX_KM.
"""

from __future__ import annotations

import math
from decimal import Decimal

from django.conf import settings

from .models import Village

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LAT = math.pi * EARTH_RADIUS_KM / 180


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(min_lat, max_lat, min_lng, max_lng) of a box that contains the circle."""
    dlat = radius_km / KM_PER_DEGREE_LAT
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    dlng = radius_km / (KM_PER_DEGREE_LAT * cos_lat)

    def dec(value: float) -> Decimal:
        return Decimal(str(round(value, 6)))

    return (
        dec(max(lat - dlat, -90.0)),
        dec(min(lat + dlat, 90.0)),
        dec(max(lng - dlng, -180.0)),
        dec(min(lng + dlng, 180.0)),
    )


def nearest_village(
    lat: float,
    lng: float,
    *,
    start_km: float | None = None,
    max_km: float | None = None,
) -> tuple[Village, float] | None:
    """The closest active village with coordinates, and its distance in km.

    None when no village lies within max_km.
    """
    radius = float(start_km or settings.NEAREST_VILLAGE_START_KM)
    limit = float(max_km or settings.NEAREST_VILLAGE_MAX_KM)
    radius = min(radius, limit)
    candidates = Village.objects.active().with_coordinates()

    while True:
        min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius)
        rows = candidates.filter(
            latitude__gte=min_lat,
            latitude__lte=max_lat,
            longitude__gte=min_lng,
            longitude__lte=max_lng,
        ).values_list("id", "latitude", "longitude")

        best_id, best_km = None, math.inf
        for village_id, v_lat, v_lng in rows:
            distance = haversine_km(lat, lng, float(v_lat), float(v_lng))
            if distance < best_km or (distance == best_km and village_id < best_id):
                best_id, best_km = village_id, distance

        if best_id is not None and best_km <= radius:
            village = Village.objects.select_related("mandal__district__state").get(id=best_id)
            return village, best_km
        if radius >= limit:
            return None
        radius = min(radius * 2, limit)
