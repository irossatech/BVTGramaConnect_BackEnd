"""Lists with tab counts count their rows once per page view (audit finding BE-DB-009).

The page total now comes from the tab counts. Expected: `count` equals the number of rows the list really
holds, for every filter, and only one COUNT query runs per page view.
"""

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.citizens.models import CitizenDevice
from apps.posts.models import Category, PostStatus
from apps.service_requests.models import Service, ServiceRequest
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


def post(world, author, status, citizen=None):
    ref = world.ref("kolakaluru")
    who = {"citizen": citizen, "posted_as_role": "CITIZEN"} if citizen else {
        "author": world.users[author], "posted_as_role": "PANCHAYAT_MEMBER",
    }
    return make_bare_post(
        **who, posted_as_level=ref.level, posted_as_area_id=ref.id, category=Category.objects.first(),
        title="t", body="b", target_level=ref.level, target_area_id=ref.id, status=status,
        submitted_at=timezone.now(),
    )


@pytest.fixture
def posts(world):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n",
    )
    for status in (PostStatus.PENDING, PostStatus.PENDING, PostStatus.PUBLISHED, PostStatus.RECHECK,
                   PostStatus.DISCARDED, PostStatus.DRAFT, PostStatus.DRAFT):
        post(world, "pm_kolakaluru", status)
    post(world, "va_kolakaluru", PostStatus.DRAFT)  # someone else's draft: never in pm's or da's list
    for _ in range(3):
        post(world, None, PostStatus.PENDING, citizen=device)


def listed(client, url):
    response = client.get(url)
    assert response.status_code == 200, response.content
    data = response.json()
    return data, len(data["results"])


@pytest.mark.parametrize("name", ["pm_kolakaluru", "da_guntur", "super"])
@pytest.mark.parametrize("query", ["", "&status=PENDING", "&status=PENDING,DRAFT", "&status=DRAFT", "&mine=1",
                                   "&source=citizen", "&source=official", "&status=PUBLISHED&source=official"])
def test_posts_count_is_the_number_of_rows(api, world, posts, name, query):
    data, rows = listed(api(world.users[name]), f"/api/v1/posts/?page_size=100{query}")
    assert data["count"] == rows


@pytest.mark.parametrize(
    "query, tabs",
    [
        ("", {"official": 2, "citizen": 3}),
        ("&source=citizen", {"official": 2, "citizen": 3}),  # tabs ignore the source filter
        ("&source=official", {"official": 2, "citizen": 3}),
        ("&escalated=1", {"official": 0, "citizen": 0}),  # but follow the others: none is escalated
    ],
)
def test_queue_count_and_tabs(api, world, posts, query, tabs):
    data, rows = listed(api(world.users["da_guntur"]), f"/api/v1/review/queue/?page_size=100{query}")
    assert data["count"] == rows
    assert data["source_counts"] == tabs


@pytest.mark.parametrize("query", ["", "&status=NEW", "&status=COMPLETED"])
def test_service_request_count_and_tabs(api, world, query):
    service = Service.objects.first()
    for status in ("NEW", "NEW", "COMPLETED"):
        device = CitizenDevice.objects.create(
            token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kaza").id, nonce=uuid.uuid4().hex,
        )
        ServiceRequest.objects.create(service=service, name="x", phone="9000000000", address="a", status=status,
                                      village_id=world.ref("kaza").id, device=device)
    data, rows = listed(api(world.users["da_guntur"]), f"/api/v1/service-requests/?page_size=100{query}")
    assert data["count"] == rows
    assert data["status_counts"]["NEW"] == 2 and data["status_counts"]["COMPLETED"] == 1


@pytest.mark.parametrize("url", ["/api/v1/posts/", "/api/v1/review/queue/", "/api/v1/service-requests/"])
def test_one_count_per_page_view(api, world, url):
    # Officials' posts only: a citizen post's phone summary has counts of its own (BE-DB-005).
    for status in (PostStatus.PENDING, PostStatus.PUBLISHED, PostStatus.DRAFT):
        post(world, "pm_kolakaluru", status)
    with CaptureQueriesContext(connection) as ctx:
        assert api(world.users["da_guntur"]).get(url).status_code == 200
    counts = [q["sql"] for q in ctx.captured_queries if "COUNT(" in q["sql"].upper()]
    assert len(counts) == 1, counts
