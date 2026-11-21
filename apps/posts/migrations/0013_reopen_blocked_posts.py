"""A block no longer closes posts (owner's request of 2026-11-21, option A): reopen the BLOCKED ones.

Every BLOCKED post goes back to PENDING, the status it had before the block, with a fresh submitted_at
so its escalation clock starts again. Each gets a ReviewAction REVERSE by the system and a
`post.reversed` audit entry (actor System), so its history explains the change. In batches.

Reverse step: posts still PENDING whose latest action is this reopening go back to BLOCKED, and those
ReviewAction rows are removed. The audit entries stay: the audit log is never changed or cleaned.
"""

from django.db import migrations
from django.utils import timezone

BATCH = 500
COMMENT = "Reopened: a block no longer closes posts (2026-11-21)."


def reopen(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    ReviewAction = apps.get_model("posts", "ReviewAction")
    AuditLog = apps.get_model("audit", "AuditLog")
    now = timezone.now()
    ids = list(Post.objects.filter(status="BLOCKED").order_by("pk").values_list("pk", flat=True))
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        posts = list(Post.objects.filter(pk__in=chunk, status="BLOCKED"))
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
                ReviewAction(post_id=post.pk, action="REVERSE", from_status="BLOCKED", to_status="PENDING",
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
                    data={"from": "BLOCKED", "to": "PENDING", "comment": COMMENT},
                )
                for post in posts
            ]
        )


def close_again(apps, schema_editor):
    Post = apps.get_model("posts", "Post")
    ReviewAction = apps.get_model("posts", "ReviewAction")
    reopened = ReviewAction.objects.filter(action="REVERSE", from_status="BLOCKED", to_status="PENDING", comment=COMMENT)
    post_ids = list(reopened.values_list("post_id", flat=True))
    for start in range(0, len(post_ids), BATCH):
        chunk = post_ids[start:start + BATCH]
        Post.objects.filter(pk__in=chunk, status="PENDING").update(status="BLOCKED")
    reopened.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("posts", "0012_post_status_blocked"),
        ("audit", "0003_mask_personal_data"),
    ]

    operations = [
        migrations.RunPython(reopen, close_again),
    ]
