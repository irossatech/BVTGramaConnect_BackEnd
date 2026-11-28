"""First decision wins even when two reviewers act at the same moment (BE_02, BE-TEST-101).

The other tests decide one after the other, so the second call already sees the new status.
Here both reviewers hold the post as it was when they opened it (PENDING), as two requests
arriving together do; only the conditional UPDATE in services.review stands between them.
"""

import pytest

from apps.audit.models import AuditLog
from apps.posts import services
from apps.posts.models import Post, PostStatus, ReviewAction

pytestmark = pytest.mark.django_db


def opened_by_both(make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    return Post.objects.get(pk=post.pk), Post.objects.get(pk=post.pk)  # each reviewer's own copy


def counts(post):
    return ReviewAction.objects.filter(post=post).count(), AuditLog.objects.filter(target_id=str(post.pk)).count()


@pytest.mark.parametrize("second_action, comment", [("discard", "duplicate"), ("recheck", "add the date"), ("publish", "")])
def test_the_slower_reviewer_loses_even_with_a_stale_copy(world, make_post, second_action, comment):
    first_copy, second_copy = opened_by_both(make_post)
    assert second_copy.status == PostStatus.PENDING

    services.review(world.users["va_kolakaluru"], first_copy, "publish")
    before = counts(first_copy)

    with pytest.raises(services.AlreadyDecided) as lost:
        services.review(world.users["ma_tenali"], second_copy, second_action, comment)

    # The answer names the winner, and nothing of the loser's decision was written.
    assert lost.value.post.decided_by == world.users["va_kolakaluru"]
    post = Post.objects.get(pk=first_copy.pk)
    assert post.status == PostStatus.PUBLISHED
    assert post.decided_by == world.users["va_kolakaluru"]
    assert counts(post) == before


def test_publishing_twice_from_stale_copies_keeps_one_share_code(world, make_post):
    first_copy, second_copy = opened_by_both(make_post)
    services.review(world.users["va_kolakaluru"], first_copy, "publish")
    code = Post.objects.get(pk=first_copy.pk).share_code
    with pytest.raises(services.AlreadyDecided):
        services.review(world.users["da_guntur"], second_copy, "publish")
    assert Post.objects.get(pk=first_copy.pk).share_code == code
