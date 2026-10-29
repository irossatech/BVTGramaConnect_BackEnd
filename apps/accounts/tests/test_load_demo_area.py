"""load_demo_area: demo officials, posts and numbers for one real mandal, safe to run again."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.contacts.phones import normalise_phone
from apps.geography.models import Village
from apps.posts.models import Post, PostStatus

pytestmark = pytest.mark.django_db


def run(*args) -> str:
    out = StringIO()
    call_command("load_demo_area", "--district", "Guntur", "--mandal", "Tenali", *args, stdout=out)
    return out.getvalue()


def test_loads_officials_posts_and_numbers_for_the_mandal_and_its_villages(world):
    output = run("--villages", "2")
    villages = list(Village.objects.filter(mandal__name_en="Tenali").order_by("name_en")[:2])
    names = {v.name_en for v in villages}
    assert all(name in output for name in names)

    demo = User.objects.filter(username__startswith="demo.")
    assert demo.count() == 1 + 2 * 2
    assert all(user.must_change_password for user in demo)
    assert "demo.tenali.mandal" in output

    posts = Post.objects.filter(author__in=demo)
    assert posts.count() == 3 + 4 * 2
    assert posts.filter(status=PostStatus.PENDING).count() == 2  # the Panchayat members' posts
    assert posts.filter(is_urgent=True).count() == 1
    assert posts.filter(event_start__isnull=False).count() == 2 + 2

    numbers = Contact.objects.filter(is_active=True, phone__startswith="08598000")
    assert numbers.count() == 2 + 2 * 2
    assert all(normalise_phone(c.phone)[1] == "LANDLINE" for c in numbers)

    # A citizen of the first village sees the demo posts and numbers.
    village_id = villages[0].id
    feed = APIClient().get(f"/api/v1/feed/?village_id={village_id}").json()["results"]
    assert len(feed) >= 3 + 3  # mandal posts and the village's published ones
    contacts = APIClient().get(f"/api/v1/contacts/?village_id={village_id}").json()
    assert sum(len(group["contacts"]) for group in contacts["for_you"]) == 2 + 2


def test_running_again_adds_nothing_and_keeps_passwords(world):
    run("--villages", "2")
    again = run("--villages", "2")
    assert "Posts: already present." in again and "Important numbers: already present." in again
    assert "(unchanged)" in again
    assert Post.objects.filter(author__username__startswith="demo.").count() == 11


def test_named_villages_and_unknown_names(world):
    run("--village", "Kolakaluru")
    assert User.objects.filter(username="demo.kolakaluru.admin").exists()
    with pytest.raises(CommandError, match="No active village"):
        run("--village", "Nowhere")
    with pytest.raises(CommandError, match="Its mandals"):
        call_command("load_demo_area", "--district", "Guntur", "--mandal", "Nowhere", stdout=StringIO())


def test_villages_without_coordinates_are_left_out(world):
    Village.objects.filter(mandal__name_en="Tenali").exclude(name_en="Nandivelugu").update(latitude=None, longitude=None)
    output = run("--villages", "4")
    assert User.objects.filter(username__endswith=".admin").count() == 1
    assert "Nandivelugu" in output
