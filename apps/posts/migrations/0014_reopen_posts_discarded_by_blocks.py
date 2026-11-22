"""Posts a block discarded before 2026-11-20 go back to review (owner's request of 2026-11-21, option A).

Until PR #121 (2026-11-20) blocking a phone discarded its waiting posts. A block no longer closes posts, and
posts/0013 reopened the ones a block moved to BLOCKED; this step does the same for the older ones, which are
still DISCARDED. Posts discarded by "Discard all" or by a reviewer stay DISCARDED.

Which posts: the old block wrote its `citizen.blocked` audit entry first and then discarded the phone's
pending posts in the same transaction, as the same Super Admin with the same comment. So a post qualifies
when its latest ReviewAction is a DISCARD by that Super Admin, with that comment, on a post of that phone
(any install), made within WINDOW after the audit entry, and the post is still DISCARDED. "Discard all"
discarded first and audited after, and a blocked phone could not post in between, so it never matches.

Each post goes to PENDING with a fresh submitted_at (its escalation clock starts again), a ReviewAction
REVERSE by the system and a `post.reversed` audit entry (actor System). In batches.

Reverse step: posts still PENDING whose latest action is this reopening go back to DISCARDED with the
decision of their original discard, and those ReviewAction rows are removed. The audit entries stay: the
audit log is never changed or cleaned.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import migrations
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

BATCH = 500
WINDOW = timedelta(seconds=60)
COMMENT = "Reopened: closed by a block before 2026-11-20; a block no longer closes posts (2026-11-21)."
DEVICE_TYPE = "citizens.citizendevice"


def _phone_devices(CitizenDevice, device_id):
    device = CitizenDevice.objects.filter(pk=device_id).only("pk", "android_id_hash").first()
    if device is None:
        return []
    if device.android_id_hash:
        return list(CitizenDevice.objects.filter(android_id_hash=device.android_id_hash).values_list("pk", flat=True))
    return [device.pk]


def _discards_by_blocks(apps):
    """{post_id: the DISCARD ReviewAction a block wrote}, for posts whose latest action it still is."""
    AuditLog = apps.get_model("audit", "AuditLog")
    CitizenDevice = apps.get_model("citizens", "CitizenDevice")
    ReviewAction = apps.get_model("posts", "ReviewAction")
    found = {}
    blocks = AuditLog.objects.filter(action="citizen.blocked", target_type=DEVICE_TYPE).order_by("created_at")
    for entry in blocks.iterator():
        try:
            devices = _phone_devices(CitizenDevice, entry.target_id)
        except (ValidationError, ValueError):  # a target id that is not a device id
            continue
        if not devices:
            continue
        actions = ReviewAction.objects.filter(
            action="DISCARD", to_status="DISCARDED", post__citizen_id__in=devices,
            created_at__gte=entry.created_at, created_at__lte=entry.created_at + WINDOW,
        )
        if entry.actor_id is not None:
            actions = actions.filter(actor_id=entry.actor_id)
        comment = ((entry.data or {}).get("comment") or "").strip()
        if comment:
            actions = actions.filter(Q(comment=comment) | Q(comment=comment[:500]))
        for action in actions:
            found.setdefault(action.post_id, action)
    if not found:
        return {}
    Post = apps.get_model("posts", "Post")
    latest = ReviewAction.objects.filter(post=OuterRef("pk")).order_by("-created_at", "-pk").values("pk")[:1]
    still = Post.objects.filter(pk__in=list(found), status="DISCARDED").annotate(last=Subquery(latest))
    return {post_id: found[post_id] for post_id, last in still.values_list("pk", "last") if last == found[post_id].pk}


def reopen(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    ReviewAction = apps.get_model("posts", "ReviewAction")
    AuditLog = apps.get_model("audit", "AuditLog")
    now = timezone.now()
    ids = sorted(_discards_by_blocks(apps), key=str)
    for start in range(0, len(ids), BATCH):
        posts = list(Post.objects.filter(pk__in=ids[start:start + BATCH], status="DISCARDED"))
        for post in posts:
            post.status = "PENDING"
            post.submitted_at = now
            post.decided_at = None
            post.decided_by_id = None
            post.decided_by_role = ""
            post.escalated_at = None
            post.escalation_level = ""
            post.updated_at = now
        Post.objects.bulk_update(
            posts,
            ["status", "submitted_at", "decided_at", "decided_by", "decided_by_role", "escalated_at",
             "escalation_level", "updated_at"],
        )
        ReviewAction.objects.bulk_create(
            [
                ReviewAction(post_id=post.pk, action="REVERSE", from_status="DISCARDED", to_status="PENDING",
                             comment=COMMENT, actor=None, actor_role="")
                for post in posts
            ]
        )
        AuditLog.objects.bulk_create(
            [
                AuditLog(
                    created_at=now, actor=None, actor_name="", actor_role="", action="post.reversed", type="posts",
                    target_type="posts.post", target_id=str(post.pk), target_label=post.title[:255],
                    area_level=post.target_level, area_id=post.target_area_id, via="system",
                    data={"from": "DISCARDED", "to": "PENDING", "comment": COMMENT},
                )
                for post in posts
            ]
        )


def discard_again(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    ReviewAction = apps.get_model("posts", "ReviewAction")
    reopened = ReviewAction.objects.filter(
        action="REVERSE", from_status="DISCARDED", to_status="PENDING", comment=COMMENT
    )
    post_ids = list(reopened.values_list("post_id", flat=True).distinct())
    for start in range(0, len(post_ids), BATCH):
        chunk = post_ids[start:start + BATCH]
        posts = list(Post.objects.filter(pk__in=chunk, status="PENDING"))
        for post in posts:
            original = (
                ReviewAction.objects.filter(post_id=post.pk, action="DISCARD").order_by("-created_at", "-pk").first()
            )
            post.status = "DISCARDED"
            if original is not None:
                post.decided_at = original.created_at
                post.decided_by_id = original.actor_id
                post.decided_by_role = original.actor_role
        Post.objects.bulk_update(posts, ["status", "decided_at", "decided_by", "decided_by_role"])
    reopened.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("posts", "0013_reopen_blocked_posts"),
        ("audit", "0003_mask_personal_data"),
        ("citizens", "0002_block_until_android_push"),
    ]

    operations = [
        migrations.RunPython(reopen, discard_again),
    ]
