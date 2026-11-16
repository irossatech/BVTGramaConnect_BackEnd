"""API answers are private (no-store) unless the view marks them public (web audit WEB-HDR-002)."""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from rest_framework.test import APIClient

from grama.cache_headers import PrivateApiAnswersMiddleware

pytestmark = pytest.mark.django_db


def private(response) -> bool:
    return response["Cache-Control"] == "no-store, private"


@pytest.mark.parametrize(
    "url",
    ["/api/v1/me/", "/api/v1/users/", "/api/v1/admin/contact-messages/", "/api/v1/service-requests/", "/api/v1/audit/"],
)
def test_answers_with_personal_data_are_not_stored(api, world, url):
    response = api(world.users["super"]).get(url)
    assert response.status_code == 200, (url, response.status_code)
    assert private(response)


def test_the_console_session_and_csv_exports_are_not_stored(console, world):
    client = console(world.users["super"])
    assert private(client.get("/api/v1/me/"))
    export = client.get("/api/v1/audit/export/?format=csv")
    assert export.status_code == 200 and private(export)


def test_errors_and_anonymous_answers_are_not_stored(world):
    anonymous = APIClient()
    refused = anonymous.get("/api/v1/me/")
    assert refused.status_code == 401 and private(refused)
    assert private(anonymous.get("/api/v1/health/"))
    missing = anonymous.get("/api/v1/no-such-thing/")
    assert missing.status_code == 404 and private(missing)


def test_public_answers_keep_their_own_caching(world):
    feed = APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}")
    assert feed.status_code == 200
    assert "public" in feed["Cache-Control"] and "max-age=60" in feed["Cache-Control"]
    assert "no-store" not in feed["Cache-Control"]


def test_only_api_answers_are_changed():
    middleware = PrivateApiAnswersMiddleware(lambda request: HttpResponse("page"))
    page = middleware(RequestFactory().get("/console/"))
    assert not page.has_header("Cache-Control")
    api_answer = middleware(RequestFactory().get("/api/v1/anything/"))
    assert private(api_answer)
    own = PrivateApiAnswersMiddleware(lambda request: _with_header("public, max-age=300"))
    assert own(RequestFactory().get("/api/v1/feed/"))["Cache-Control"] == "public, max-age=300"


def _with_header(value: str) -> HttpResponse:
    response = HttpResponse("x")
    response["Cache-Control"] = value
    return response
