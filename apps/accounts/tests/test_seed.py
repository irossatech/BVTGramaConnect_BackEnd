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
