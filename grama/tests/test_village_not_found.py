"""A missing village says so (BVTGC-70, audit CROSS-API-103).

The app forgets a citizen's saved village on a 404 from these endpoints. Any other 404 (an unknown
address during a deploy, a post that is gone) must not look the same, so the village answer carries
"reason": "village_not_found", and nothing else does. The code stays not_found for older apps.
"""

import uuid

import pytest

pytestmark = pytest.mark.django_db

VILLAGE_LISTS = ["/api/v1/feed/", "/api/v1/feed/upcoming/", "/api/v1/feed/urgent/", "/api/v1/contacts/", "/api/v1/ads/"]


@pytest.mark.parametrize("path", VILLAGE_LISTS)
def test_an_unknown_village_is_404_with_the_reason(api, world, path):
    response = api().get(path, {"village_id": 999999})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"  # unchanged, so older apps keep working
    assert error["reason"] == "village_not_found"


@pytest.mark.parametrize("path", VILLAGE_LISTS)
def test_a_switched_off_village_is_404_with_the_reason(api, world, path):
    village = world.areas["kolakaluru"]
    village.is_active = False
    village.save(update_fields=["is_active"])
    response = api().get(path, {"village_id": village.id})
    assert response.status_code == 404
    assert response.json()["error"]["reason"] == "village_not_found"


@pytest.mark.parametrize("path", VILLAGE_LISTS)
def test_a_live_village_answers_normally(api, world, path):
    assert api().get(path, {"village_id": world.areas["kolakaluru"].id}).status_code == 200


def test_other_not_found_answers_carry_no_reason(api, world):
    response = api().get(f"/api/v1/feed/posts/{uuid.uuid4()}/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found" and "reason" not in error
