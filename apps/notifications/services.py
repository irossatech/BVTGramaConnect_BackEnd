"""Who hears about what, and in which words.

- Citizens: one push to the FCM topic of the post's target area when it is
  published. A district post reaches every village in the district, because
  each citizen's app subscribes to its village, mandal, district and state topics.
- Reviewers: inbox item + push when a post in their area waits for review.
  Village, Mandal and District Admins whose area contains the post's target.
  Super Admins are not pinged for every post; they see the queue in the
  console and get escalations that reach state level.
- Admins one level up: inbox item + push when a post is escalated.
- The author: inbox item + push when a reviewer publishes, sends back,
  discards or takes down their post.

Texts come from NotificationTemplate (editable in the console), rendered in
each recipient's preferred language. Topic pushes use the post's own language,
because the post itself exists only in that language.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django_q.tasks import async_task

from apps.accounts.models import RoleAssignment, User, UserStatus
from apps.accounts.roles import Role
from apps.geography.areas import AreaDescriber, ancestry
from apps.geography.models import AreaLevel

from .models import Notification, NotificationKind, NotificationTemplate
from .push import topic_for
from .templates_data import BY_KEY

logger = logging.getLogger("grama.notifications")

ADMIN_ROLE_AT = {
    AreaLevel.DISTRICT: Role.DISTRICT_ADMIN,
    AreaLevel.MANDAL: Role.MANDAL_ADMIN,
    AreaLevel.VILLAGE: Role.VILLAGE_ADMIN,
}


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render(key: str, language: str, **values) -> str:
    template = NotificationTemplate.objects.filter(key=key).first()
    if template is not None:
        text = template.text(language)
    else:
        default = BY_KEY[key]
        text = default["text_te"] if (language or "te").startswith("te") else default["text_en"]
    return text.format_map(_Blank({k: "" if v is None else v for k, v in values.items()}))


def admins_over(post_target, *, levels=None, include_super_admins=False):
    """Active admins whose area contains the target, optionally only at some levels."""
    chain = ancestry(post_target)
    query = Q(pk__in=[])
    for level, area_id in chain.items():
        role = ADMIN_ROLE_AT.get(level)
        if role and (levels is None or level in levels):
            query |= Q(role=role, area_level=level, area_id=area_id)
    if include_super_admins:
        query |= Q(role=Role.SUPER_ADMIN)
    user_ids = RoleAssignment.objects.filter(query).values("user_id")
    return User.objects.filter(id__in=user_ids, status=UserStatus.ACTIVE)


def reviewers_for(post):
    return admins_over(post.target).exclude(pk=post.author_id)


def _values(post, language: str, **extra) -> dict:
    area = AreaDescriber(language).describe(post.target)
    return {
        "title": post.title,
        "area": area["name"] if area else "",
        "category": post.category.display_name(language),
        **extra,
    }


def _push_data(post, kind: str) -> dict:
    return {
        "type": kind,
        "post_id": str(post.id),
        "area_level": post.target_level,
        "area_id": post.target_area_id,
        "urgent": "1" if post.is_urgent else "0",
    }


def notify_users(users, *, post, kind: str, template_key: str, **extra) -> list[Notification]:
    """Inbox item and a queued push for each user, in their own language."""
    created = []
    for user in users:
        language = user.preferred_language
        text = render(template_key, language, **_values(post, language, **extra))
        created.append(
            Notification.objects.create(
                user=user,
                kind=kind,
                title=text[:255],
                body=post.title[:500],
                post=post,
                area_level=post.target_level,
                area_id=post.target_area_id,
            )
        )
        async_task(
            "apps.notifications.tasks.send_push_to_user",
            str(user.pk),
            text[:255],
            post.title,
            _push_data(post, kind),
            group="push",
        )
    return created


def announce_to_citizens(post) -> None:
    """Queue one topic push for the post's target area, in the post's language.

    Urgent posts go out at high priority, everything else at normal (owner decision Q19).
    """
    title = render("post_published", post.language, **_values(post, post.language))
    async_task(
        "apps.notifications.tasks.send_push_to_topic",
        topic_for(post.target_level, post.target_area_id),
        title[:255],
        post.title,
        _push_data(post, NotificationKind.POST_PUBLISHED),
        "high" if post.is_urgent else "normal",
        group="push",
    )


def latest_comment(post) -> str:
    action = post.review_actions.order_by("-created_at").first()
    return action.comment if action else ""


AUTHOR_MESSAGES = {
    "PUBLISHED": (NotificationKind.POST_PUBLISHED, "post_approved"),
    "RECHECK": (NotificationKind.POST_RECHECK, "post_recheck"),
    "DISCARDED": (NotificationKind.POST_DISCARDED, "post_discarded"),
    "UNPUBLISHED": (NotificationKind.POST_UNPUBLISHED, "post_unpublished"),
}


def tell_author(post, actor=None) -> list[Notification]:
    """After a reviewer's decision: tell the author what happened to their post."""
    if actor is not None and actor.pk == post.author_id:
        return []
    message = AUTHOR_MESSAGES.get(post.status)
    if message is None:
        return []
    kind, template_key = message
    author = User.objects.filter(pk=post.author_id, status=UserStatus.ACTIVE)
    return notify_users(author, post=post, kind=kind, template_key=template_key, comment=latest_comment(post))


def alert_escalation(post, level: str, hours: int) -> list[Notification]:
    if level == AreaLevel.STATE:
        users = admins_over(post.target, levels=set(), include_super_admins=True)
    else:
        users = admins_over(post.target, levels={level})
    return notify_users(
        users.exclude(pk=post.author_id),
        post=post,
        kind=NotificationKind.ESCALATION,
        template_key="escalation",
        hours=hours,
    )
