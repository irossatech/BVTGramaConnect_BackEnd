"""Bad input answers 400 or 404 in the contract's error shape, never 500 (audit finding BE-ERR-101).

- An id that is not a UUID (or not a number, for numeric ids) in a URL: 404 not_found.
- A filter value that cannot be one: 400 validation_error.
- "²" passes str.isdigit() but int() cannot read it; Telugu digits are real numbers and still work.
- CSV imports: a field over the csv module's limit, or a NaN coordinate, is an error in the report.
- A JSON list where an object is expected: 400.
"""

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.posts.models import Category
from conftest import client_for

pytestmark = pytest.mark.django_db

SUPERSCRIPT_TWO = "²"
TELUGU_ONE = "౧"


@pytest.fixture
def sa(world):
    client = client_for(world.users["super"])
    client.raise_request_exception = False  # a crash would come back as a 500, which the tests catch
    return client


@pytest.fixture
def anon():
    client = client_for()
    client.raise_request_exception = False
    return client


def error_code(response):
    return json.loads(response.content)["error"]["code"]


def a_post(sa, world):
    response = sa.post(
        "/api/v1/posts/",
        {"category": Category.objects.first().key, "title": "t", "body": "b",
         "target": {"level": "VILLAGE", "id": world.ref("kolakaluru").id}},
        format="json",
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("bad", ["abc", "-" * 36, "12345678-1234-1234-1234-12345678901z"])
def test_a_post_id_that_is_not_a_uuid_is_404(sa, world, bad):
    response = sa.get(f"/api/v1/posts/{bad}/")
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_a_media_id_that_is_not_a_uuid_is_404(sa, world):
    post_id = a_post(sa, world)
    response = sa.delete(f"/api/v1/posts/{post_id}/media/abc/")
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_a_contact_id_that_is_not_a_uuid_is_404(sa):
    response = sa.get("/api/v1/admin/contacts/" + "-" * 36 + "/")
    assert response.status_code == 404 and error_code(response) == "not_found"


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_a_contact_type_id_that_is_not_a_number_is_404(sa, method):
    response = getattr(sa, method)("/api/v1/admin/contact-types/abc/", {"name_en": "x"}, format="json")
    assert response.status_code == 404 and error_code(response) == "not_found"


@pytest.mark.parametrize("url, field", [("/api/v1/posts/?author=abc", "author"), ("/api/v1/audit/?actor=abc", "actor")])
def test_a_user_filter_that_is_not_a_uuid_is_400(sa, url, field):
    response = sa.get(url)
    assert response.status_code == 400 and error_code(response) == "validation_error"
    assert field in json.loads(response.content)["error"]["fields"]


def test_a_real_user_filter_still_works(sa, world):
    response = sa.get(f"/api/v1/posts/?author={world.users['super'].id}")
    assert response.status_code == 200


PUBLIC_NUMBER_URLS = [
    "/api/v1/feed/?village_id={n}",
    "/api/v1/feed/upcoming/?village_id={n}",
    "/api/v1/feed/urgent/?village_id={n}",
    "/api/v1/contacts/?village_id={n}",
    "/api/v1/ads/?village_id={n}",
    "/api/v1/geo/mandals/?district={n}",
    "/api/v1/geo/villages/?mandal={n}",
]
OFFICIAL_NUMBER_URLS = [
    "/api/v1/posts/?level=VILLAGE&area_id={n}",
    "/api/v1/posts/?district={n}",
    "/api/v1/review/queue/?level=VILLAGE&area_id={n}",
    "/api/v1/stats/?level=VILLAGE&area_id={n}",
    "/api/v1/users/?level=VILLAGE&area_id={n}",
    "/api/v1/admin/contacts/?level=VILLAGE&area_id={n}",
    "/api/v1/admin/reports/summary/?days={n}",
    "/api/v1/geo/admin/villages/?mandal={n}",
]


@pytest.mark.parametrize("url", PUBLIC_NUMBER_URLS)
def test_public_number_parameters_refuse_superscripts(anon, world, url):
    response = anon.get(url.format(n=SUPERSCRIPT_TWO))
    assert response.status_code == 400 and error_code(response) == "validation_error"


@pytest.mark.parametrize("url", OFFICIAL_NUMBER_URLS)
def test_official_number_parameters_refuse_superscripts(sa, world, url):
    response = sa.get(url.format(n=SUPERSCRIPT_TWO))
    assert response.status_code == 400 and error_code(response) == "validation_error"


def test_telugu_digits_are_still_numbers(anon, world):
    """౧ is a decimal digit that int() reads as 1, so it is not refused (only "²"-like characters are)."""
    assert anon.get(f"/api/v1/geo/mandals/?district={TELUGU_ONE}").status_code == 200


def upload(name, text):
    return {"file": SimpleUploadedFile(name, text.encode("utf-8"))}


@pytest.mark.parametrize("coordinate", ["NaN", "Infinity", "-inf"])
def test_a_geography_import_with_a_non_finite_coordinate_reports_the_line(sa, world, coordinate):
    text = f"state,district,mandal,village,latitude,longitude\nAndhra Pradesh,Guntur,Tenali,Kolakaluru,{coordinate},80.5\n"
    response = sa.post("/api/v1/geo/villages/import/?dry_run=1", upload("g.csv", text), format="multipart")
    assert response.status_code == 400
    errors = json.loads(response.content)["error"]["report"]["errors"]
    assert any("Line 2" in error and "not a number" in error for error in errors)


def test_a_geography_import_with_an_over_long_field_is_400(sa, world):
    text = "state,district,mandal,village\nAndhra Pradesh,Guntur,Tenali," + "x" * 131073 + "\n"
    response = sa.post("/api/v1/geo/villages/import/?dry_run=1", upload("g.csv", text), format="multipart")
    assert response.status_code == 400
    assert "not valid CSV" in response.content.decode("utf-8")


def test_a_contacts_import_with_an_over_long_field_is_400(sa, world):
    text = "type,name,phone\nother," + "x" * 131073 + ",9000000000\n"
    response = sa.post("/api/v1/admin/contacts/import/?dry_run=1", upload("c.csv", text), format="multipart")
    assert response.status_code == 400
    assert "not valid CSV" in response.content.decode("utf-8")


def test_an_ad_from_a_json_list_is_400(sa):
    response = sa.post("/api/v1/admin/ads/", [1, 2], format="json")
    assert response.status_code == 400 and error_code(response) == "validation_error"
