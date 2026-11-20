"""GET /posts/ answers "source_counts": {"official", "citizen"} (2026-11-20).

For the Super Admin app's All / Officials / Citizens switch, which counted only the posts it had loaded.
The numbers use every filter except `source`, the status included, so they match the list under the
chosen status tab; `status_counts` keeps every filter except `status`, `source` included.
Expected numbers are worked out here from the posts the test makes.
"""

import uuid

import pytest
from django.utils import timezone

from apps.citizens.models import CitizenDevice
from apps.posts.models import Category, PostStatus
from conftest import make_bare_post

pytestmark = pytest.mark.django_db

LIST = "/api/v1/posts/"


def citizen_post(world, village, status):
    ref = world.ref(village)
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=ref.id, nonce=uuid.uuid4().hex,
    )
    return make_bare_post(
        citizen=device, posted_as_role="CITIZEN", posted_as_level=ref.level, posted_as_area_id=ref.id,
        category=Category.objects.get(key="announce"), title="పౌరుడి వార్త", body="b", target_level=ref.level,
        target_area_id=ref.id, status=status, submitted_at=timezone.now(),
    )


@pytest.fixture
def mixed(world, make_post):
    """Officials: 2 published, 1 pending (Guntur). Citizens: 1 pending in Kolakaluru, 1 pending and
    1 published in Nidamanuru (Krishna)."""
    make_post("va_kolakaluru", "kolakaluru")  # auto-published
    make_post("da_guntur", "guntur")  # auto-published
    make_post("pm_kolakaluru", "kolakaluru")  # pending
    citizen_post(world, "kolakaluru", PostStatus.PENDING)
    citizen_post(world, "nidamanuru", PostStatus.PENDING)
    citizen_post(world, "nidamanuru", PostStatus.PUBLISHED)


def answer(api, world, who="super", **params):
    response = api(world.users[who]).get(LIST, {"page_size": 100, **params})
    assert response.status_code == 200, response.json()
    return response.json()


def test_the_super_admin_gets_both_totals(api, world, mixed):
    data = answer(api, world)
    assert data["source_counts"] == {"official": 3, "citizen": 3}
    assert data["count"] == 6 and data["status_counts"]["ALL"] == 6


@pytest.mark.parametrize("status, official, citizen", [
    ("PENDING", 1, 2), ("PUBLISHED", 2, 1), ("DISCARDED", 0, 0), ("DISCARDED,UNPUBLISHED", 0, 0),
])
def test_they_follow_the_status_tab(api, world, mixed, status, official, citizen):
    assert answer(api, world, status=status)["source_counts"] == {"official": official, "citizen": citizen}


@pytest.mark.parametrize("source, listed, pending", [("official", 3, 1), ("citizen", 3, 2)])
def test_the_chosen_source_does_not_change_them(api, world, mixed, source, listed, pending):
    data = answer(api, world, source=source)
    assert data["source_counts"] == {"official": 3, "citizen": 3}
    # The list and status_counts are narrowed to that source, as before.
    assert data["count"] == listed and data["status_counts"]["PENDING"] == pending


def test_the_other_filters_narrow_them(api, world, mixed):
    guntur = world.areas["guntur"].id
    assert answer(api, world, district=guntur)["source_counts"] == {"official": 3, "citizen": 1}
    assert answer(api, world, category="news")["source_counts"] == {"official": 0, "citizen": 0}
    assert answer(api, world, escalated="true")["source_counts"] == {"official": 0, "citizen": 0}


@pytest.mark.parametrize("who, official, citizen", [
    ("da_guntur", 3, 1),        # Guntur's three official posts and the Kolakaluru citizen post
    ("va_kaza", 1, 0),          # Kaza is in Guntur: the District Admin's published post to Guntur only
    ("va_nidamanuru", 0, 2),    # Nidamanuru (Krishna): only its two citizen posts
    ("da_krishna", 0, 2),
])
def test_an_admin_counts_only_what_they_can_see(api, world, mixed, who, official, citizen):
    data = answer(api, world, who)
    ids = data["results"]
    assert data["source_counts"] == {
        "official": sum(1 for row in ids if row["author"]["role"] != "CITIZEN"),
        "citizen": sum(1 for row in ids if row["author"]["role"] == "CITIZEN"),
    }
    assert data["source_counts"] == {"official": official, "citizen": citizen}


def test_an_unknown_source_is_refused(api, world, mixed):
    response = api(world.users["super"]).get(LIST, {"source": "robots"})
    assert response.status_code == 400
