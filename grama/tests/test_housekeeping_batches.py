"""Housekeeping deletes in short batches, never one long DELETE (audit finding BE-DB-019).

Expected: every expired row goes and every live one stays, whatever the batch size, and no single DELETE
statement removes more than a batch.
"""

from datetime import timedelta

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from grama import housekeeping
from grama.housekeeping import run_housekeeping

pytestmark = pytest.mark.django_db


def sessions(count, *, expired):
    for _ in range(count):
        store = SessionStore()
        store["x"] = 1
        store.create()
        if expired:
            Session.objects.filter(session_key=store.session_key).update(
                expire_date=timezone.now() - timedelta(days=1)
            )


def test_every_expired_row_goes_in_batches(world, monkeypatch):
    monkeypatch.setattr(housekeeping, "DELETE_BATCH", 3)
    sessions(7, expired=True)
    sessions(2, expired=False)
    user = world.users["va_kolakaluru"]
    past = timezone.now() - timedelta(days=1)
    for index in range(5):
        token = OutstandingToken.objects.create(user=user, jti=f"old-{index}", token="t", expires_at=past)
        BlacklistedToken.objects.create(token=token)
    OutstandingToken.objects.create(user=user, jti="live", token="t", expires_at=timezone.now() + timedelta(days=1))

    with CaptureQueriesContext(connection) as ctx:
        result = run_housekeeping()

    assert result["expired_sessions"] == 7 and result["expired_tokens"] == 5
    assert Session.objects.count() == 2
    assert list(OutstandingToken.objects.values_list("jti", flat=True)) == ["live"]
    assert not BlacklistedToken.objects.exists()  # gone with their tokens
    deletes = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("DELETE") and "django_session" in q["sql"]]
    assert len(deletes) == 3  # 3 + 3 + 1
