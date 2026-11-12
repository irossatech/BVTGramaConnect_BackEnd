"""Every request gets a number, in its answer and in its log lines (audit BE_02, BE-OBS-101)."""

import logging
from unittest import mock

import pytest
from django.test import Client

from grama import request_id
from grama.request_id import RequestIdFilter

pytestmark = pytest.mark.django_db


def test_every_answer_carries_a_new_number(client):
    first = client.get("/api/v1/health/")
    second = client.get("/api/v1/health/")
    assert len(first["X-Request-ID"]) == 32
    assert first["X-Request-ID"] != second["X-Request-ID"]


def test_errors_carry_it_too(client):
    assert client.get("/api/v1/no-such-address/")["X-Request-ID"]
    assert client.get("/api/v1/admin/settings/")["X-Request-ID"]  # 401/403


def test_a_proxy_number_is_kept(client):
    response = client.get("/api/v1/health/", HTTP_X_REQUEST_ID="nginx-7f3a9c1e2b")
    assert response["X-Request-ID"] == "nginx-7f3a9c1e2b"


@pytest.mark.parametrize("sent", ["short", "has space here", "a" * 65, "x\nforged log line", "<script>"])
def test_anything_else_is_replaced(client, sent):
    """A client cannot write its own text into the server log."""
    response = client.get("/api/v1/health/", HTTP_X_REQUEST_ID=sent)
    assert response["X-Request-ID"] != sent
    assert len(response["X-Request-ID"]) == 32


def test_log_lines_carry_the_requests_number(client):
    seen = []

    def capture(*args, **kwargs):
        record = logging.LogRecord("grama.test", logging.WARNING, __file__, 1, "inside the view", None, None)
        RequestIdFilter().filter(record)
        seen.append(record.request_id)
        return mock.DEFAULT

    with mock.patch("grama.views.connection.ensure_connection", side_effect=capture):
        response = client.get("/api/v1/health/")
    # The health check may touch the database more than once (it also checks the worker); every
    # touch carries this request's number.
    assert seen and set(seen) == {response["X-Request-ID"]}
    # Outside a request (background jobs, commands) the number is "-".
    assert request_id.current.get() == "-"


def test_a_crash_gives_the_number_to_quote(settings):
    settings.DEBUG = False  # the JSON 500 handler, as on the servers
    crashing = Client(raise_request_exception=False)
    with mock.patch("grama.views.connection.ensure_connection", side_effect=RuntimeError("boom")):
        response = crashing.get("/api/v1/health/")
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == response["X-Request-ID"]
