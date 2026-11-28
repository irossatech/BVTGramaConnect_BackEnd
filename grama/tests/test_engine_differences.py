"""Answers that used to depend on which database runs underneath (audit finding BE-DB-021).

- The dashboard's escalation hours read the console's saved value under the key the console saves.
  The old lower-case lookup found it only on MySQL, whose usual collation ignores case.
- Telugu duplicate names are compared character by character in Python, so MySQL's collation
  (which can treat some Telugu vowel signs as equal) gives the same answer as SQLite and PostgreSQL.
"""

import pytest

from apps.geography.models import Village

pytestmark = pytest.mark.django_db

SETTINGS = "/api/v1/admin/settings/"
DASHBOARD = "/api/v1/admin/dashboard/"
REVIEW_TIME = "/api/v1/admin/reports/review-time/"


def test_the_dashboard_shows_the_escalation_hours_saved_in_the_console(api, world):
    admin = api(world.users["super"])
    assert admin.get(DASHBOARD).json()["escalation_hours"] == 24
    assert admin.patch(SETTINGS, {"review_escalation_hours": 48}, format="json").status_code == 200
    assert admin.get(DASHBOARD).json()["escalation_hours"] == 48
    assert {row["escalation_hours"] for row in admin.get(REVIEW_TIME).json()} == {48}


def test_the_dashboard_falls_back_to_the_setting(api, world, settings):
    settings.REVIEW_ESCALATION_HOURS = 36
    assert api(world.users["super"]).get(DASHBOARD).json()["escalation_hours"] == 36


def new_village(api, world, name_en, name_te):
    return api(world.users["super"]).post("/api/v1/geo/villages/", {
        "mandal_id": world.areas["tenali"].id, "name_en": name_en, "name_te": name_te,
        "latitude": "16.200000", "longitude": "80.600000",
    }, format="json")


def test_the_same_telugu_name_is_refused(api, world):
    response = new_village(api, world, "Kolakaluru Two", "కొలకలూరు")
    assert response.status_code == 409 and "name_te" in response.json()["error"]["fields"]


def test_a_telugu_name_differing_only_in_a_vowel_sign_is_allowed(api, world):
    # కొలకలూరు with the last vowel sign ూ (U+0C42) replaced by ు (U+0C41): another name.
    other = "కొలకలురు"
    assert other != "కొలకలూరు"
    response = new_village(api, world, "Kolakaluru Two", other)
    assert response.status_code == 201, response.json()
    assert Village.objects.filter(name_te=other).exists()


def test_the_telugu_check_reads_names_from_the_same_mandal_only(api, world):
    assert new_village(api, world, "Kaza Two", "కాజా").status_code == 201  # Kaza is in Mangalagiri, not Tenali
