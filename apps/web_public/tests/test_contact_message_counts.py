"""The console's New / Handled / All pills show numbers (2026-11-20).

GET /admin/contact-messages/ adds "status_counts": {"new", "handled", "all"}, worked out with every filter
except `status`, as GET /posts/ does: the search narrows the numbers, the chosen pill does not.
"""

import pytest

from apps.web_public.models import ContactMessage

from .test_showcase_and_console import INBOX, message

pytestmark = pytest.mark.django_db


@pytest.fixture
def five():
    """Three new messages and two handled ones; two of them mention బోరు (bore well)."""
    texts = ["బోరు పని చేయట్లేదు", "రోడ్డు గుంతలు", "Street light", "బోరు నీరు రావట్లేదు", "Ration card"]
    items = [message(text=text) for text in texts]
    ContactMessage.objects.filter(pk__in=[items[3].pk, items[4].pk]).update(handled=True)
    return items


def counts(client, **params):
    response = client.get(INBOX, params)
    assert response.status_code == 200
    return response.json()["status_counts"], response.json()["count"]


@pytest.mark.parametrize("status, listed", [("", 5), ("new", 3), ("handled", 2)])
def test_the_counts_do_not_change_with_the_chosen_pill(api, world, five, status, listed):
    numbers, count = counts(api(world.users["super"]), status=status)
    assert numbers == {"new": 3, "handled": 2, "all": 5}
    assert count == listed


@pytest.mark.parametrize("status", ["", "new", "handled"])
def test_the_search_narrows_the_counts(api, world, five, status):
    numbers, _count = counts(api(world.users["super"]), status=status, q="బోరు")
    assert numbers == {"new": 1, "handled": 1, "all": 2}


def test_marking_handled_moves_one_from_new_to_handled(api, world, five):
    client = api(world.users["super"])
    assert client.patch(f"{INBOX}{five[0].pk}/", {"handled": True}, format="json").status_code == 200
    assert counts(client)[0] == {"new": 2, "handled": 3, "all": 5}


def test_no_messages_gives_zeros(api, world):
    assert counts(api(world.users["super"]))[0] == {"new": 0, "handled": 0, "all": 0}


def test_officials_without_the_permission_get_no_counts(api, world):
    response = api(world.users["da_guntur"]).get(INBOX)
    assert response.status_code == 403
    assert "status_counts" not in str(response.json())
