"""seed_sample_data: the shape the UAT checklist relies on, and safe to run twice."""

from collections import Counter
from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import RoleAssignment
from apps.geography.models import District, Mandal, Village

pytestmark = pytest.mark.django_db


def test_sample_data_has_every_role_and_every_post_state():
    from apps.posts.models import Post

    call_command("seed_sample_data", stdout=StringIO())
    assert District.objects.count() == 2
    assert Mandal.objects.count() == 4
    assert Village.objects.count() == 12
    assert set(RoleAssignment.objects.values_list("role", flat=True)) == {
        "SUPER_ADMIN", "DISTRICT_ADMIN", "MANDAL_ADMIN", "VILLAGE_ADMIN", "PANCHAYAT_MEMBER",
    }
    states = Counter(Post.objects.values_list("status", flat=True))
    assert set(states) == {"DRAFT", "PENDING", "PUBLISHED", "RECHECK", "DISCARDED", "UNPUBLISHED"}

    call_command("seed_sample_data", stdout=StringIO())
    assert Village.objects.count() == 12
    assert Counter(Post.objects.values_list("status", flat=True)) == states


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
