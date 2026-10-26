"""seed_sample_data: the shape the UAT checklist relies on, and safe to run twice."""

from collections import Counter
from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import RoleAssignment, User
from apps.geography.models import District, Mandal, Village

pytestmark = pytest.mark.django_db


def test_sample_data_has_every_role_and_every_post_state():
    from apps.posts.models import Post

    out = StringIO()
    call_command("seed_sample_data", stdout=out)
    assert District.objects.count() == 2
    assert Mandal.objects.count() == 4
    assert Village.objects.count() == 12
    assert set(RoleAssignment.objects.values_list("group__role_group__key", flat=True)) == {
        "SUPER_ADMIN", "DISTRICT_ADMIN", "MANDAL_ADMIN", "VILLAGE_ADMIN", "PANCHAYAT_MEMBER",
    }
    # Each official got a temporary password, printed once, and must change it first.
    officials = list(User.objects.all())
    assert len(officials) == 5 and all(user.must_change_password for user in officials)
    lines = {line.split()[0]: line.split()[1] for line in out.getvalue().splitlines() if line.startswith("  ") and "." in line.split()[0]}
    assert all(User.objects.get(username=name).check_password(password) for name, password in lines.items())
    assert len(lines) == 5
    states = Counter(Post.objects.values_list("status", flat=True))
    assert set(states) == {"DRAFT", "PENDING", "PUBLISHED", "RECHECK", "DISCARDED", "UNPUBLISHED"}

    again = StringIO()
    call_command("seed_sample_data", stdout=again)
    assert Village.objects.count() == 12
    assert Counter(Post.objects.values_list("status", flat=True)) == states
    assert again.getvalue().count("(unchanged)") == 5


def test_sample_data_has_events_an_urgent_post_and_a_media_check(settings, tmp_path, monkeypatch):
    from django.utils import timezone

    from apps.posts.models import Post

    settings.MEDIA_ROOT = tmp_path / "media"
    monkeypatch.setattr("shutil.which", lambda tool: None)
    out = StringIO()
    call_command("seed_sample_data", stdout=out)
    today = timezone.localdate()
    events = Post.objects.filter(status="PUBLISHED", event_start__isnull=False)
    assert events.count() == 4
    assert all(post.event_start >= today for post in events)
    assert set(events.values_list("event_kind", flat=True)) == {"meeting", "camp", "deadline", "closure"}
    urgent = Post.objects.get(is_urgent=True)
    assert urgent.status == "PUBLISHED" and urgent.target_level == "MANDAL"
    text = out.getvalue()
    assert f"Media: {settings.MEDIA_ROOT} is writable." in text
    assert "ffprobe and ffmpeg not found" in text

    again = StringIO()
    call_command("seed_sample_data", stdout=again)
    assert Post.objects.filter(event_start__isnull=False).count() == 4
    assert Post.objects.filter(is_urgent=True).count() == 1
    assert "Event and urgent posts: already present." in again.getvalue()


def test_sample_data_has_the_app_s_important_numbers(api):
    from apps.contacts.models import Contact

    call_command("seed_sample_data", stdout=StringIO())
    assert Contact.objects.count() == 15  # 6 helplines + 9 area numbers
    village = Village.objects.get(name_en="Kolakaluru")
    data = api().get("/api/v1/contacts/", {"village_id": village.id}, HTTP_ACCEPT_LANGUAGE="en").json()
    names = [c["name"] for group in data["for_you"] for c in group["contacts"]]
    # The app's demo set for Kolakaluru; P. Lakshmi (Nandivelugu) and Mangalagiri's Tahsildar are elsewhere.
    assert set(names) == {
        "K. Srinivasa Rao", "Kolakaluru Grama Sachivalayam", "Mee Seva Centre Kolakaluru", "Tahsildar Office, Tenali",
        "MPDO Office, Tenali", "Collectorate, Guntur", "RDO Office, Tenali Division",
    }
    assert [c["phone"] for g in data["helplines"] for c in g["contacts"]].count("108") == 1

    again = StringIO()
    call_command("seed_sample_data", stdout=again)
    assert Contact.objects.count() == 15
    assert "Important numbers: already present." in again.getvalue()
