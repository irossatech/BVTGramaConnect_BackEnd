"""Who hears about what, and in which words.

- Citizens: one push to the FCM topic of the post's target area when it is
  published. A district post reaches every village in the district, because
  each citizen's app subscribes to its village, mandal, district and state topics.
- Reviewers: inbox item + push when a post in their area waits for review:
  everyone holding posts.review_post through an assignment at the post's
  target area or an area above it. Platform-wide groups (Super Admin) are not
  pinged for every post; they see the queue in the console and get
  escalations that reach state level.
- Reviewers one level up: inbox item + push when a post is escalated.
- The author: inbox item + push when a reviewer publishes, sends back,
  discards or takes down their post. A citizen post's phone gets the same
  push, without an inbox item (tell_citizen).

Texts come from NotificationTemplate (editable in the console), rendered in
each recipient's preferred language. Topic pushes use the post's own language,
because the post itself exists only in that language.
"""

from __future__ import annotations

import logging

from django_q.tasks import async_task

from apps.accounts import authz
from apps.accounts.models import User
from apps.geography.areas import AreaDescriber, AreaRef, ancestry
from apps.geography.models import AreaLevel

from .models import Notification, NotificationKind, NotificationTemplate
from .push import topic_for
from .templates_data import BY_KEY

logger = logging.getLogger("grama.notifications")

REVIEW = "posts.review_post"


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
    """Active reviewers assigned at the target or an area above it, optionally only at some levels.

    Platform-wide reviewers (Super Admin) only with include_super_admins.
    """
    chain = ancestry(post_target)
    areas = [AreaRef(level, area_id) for level, area_id in chain.items() if levels is None or level in levels]
    users = authz.holders(REVIEW, areas, platform_wide=False) if areas else User.objects.none()
    if include_super_admins:
        users = users | authz.holders(REVIEW, platform_wide=True)
    return users.distinct()


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
    author = User.objects.filter(pk=post.author_id, is_active=True)
    return notify_users(author, post=post, kind=kind, template_key=template_key, comment=latest_comment(post))


def tell_citizen(post) -> bool:
    """After a decision on a citizen post: push the result to the phone that wrote it (contract section 15, CP4).

    Same texts as tell_author, in the phone's language. No inbox item: citizens have no account.
    Returns whether a push was queued (the phone sent an FCM token and the status is one to report).
    """
    if not post.citizen_id:
        return False
    message = AUTHOR_MESSAGES.get(post.status)
    device = post.citizen
    if message is None or not device.fcm_token:
        return False
    kind, template_key = message
    text = render(template_key, device.language, **_values(post, device.language, comment=latest_comment(post)))
    async_task(
        "apps.notifications.tasks.send_push_to_citizen",
        str(device.pk),
        text[:255],
        post.title,
        _push_data(post, kind),
        group="push",
    )
    return True


SERVICE_MANAGE = "service_requests.manage_service_requests"


def notify_service_request(request) -> list[Notification]:
    """A citizen's new service request: inbox item and push for the admins of its village, mandal and district.

    Like posts, the Super Admin is not pinged for every request; they see the list in the console.
    The text names the service and the citizen, never the phone number.
    """
    from apps.service_requests.models import label

    village = AreaRef(AreaLevel.VILLAGE, request.village_id)
    areas = [AreaRef(level, area_id) for level, area_id in ancestry(village).items()]
    users = authz.holders(SERVICE_MANAGE, areas, platform_wide=False).distinct() if areas else User.objects.none()
    created = []
    for user in users:
        language = user.preferred_language
        area = AreaDescriber(language).describe(village)
        text = render(
            "service_request_new", language,
            area=area["name"] if area else "", service=label(request.service, language), name=request.name,
        )
        created.append(
            Notification.objects.create(
                user=user,
                kind=NotificationKind.SERVICE_REQUEST,
                title=text[:255],
                body=(request.details or label(request.service, language))[:500],
                service_request=request,
                area_level=AreaLevel.VILLAGE,
                area_id=request.village_id,
            )
        )
        async_task(
            "apps.notifications.tasks.send_push_to_user",
            str(user.pk),
            text[:255],
            label(request.service, language),
            {
                "type": NotificationKind.SERVICE_REQUEST,
                "request_id": str(request.pk),
                "area_level": AreaLevel.VILLAGE,
                "area_id": request.village_id,
            },
            group="push",
        )
    return created


def tell_citizen_service_request(request) -> bool:
    """After a status change: push the new status (and the note) to the phone that sent the request."""
    from apps.service_requests.models import label

    device = request.device
    if not device.fcm_token:
        return False
    language = device.language
    text = render(
        "service_request_updated", language,
        service=label(request.service, language), status=label(request.status, language), note=request.note,
    )
    async_task(
        "apps.notifications.tasks.send_push_to_citizen",
        str(device.pk),
        text[:255],
        (request.note or label(request.status, language))[:500],
        {"type": "SERVICE_REQUEST_UPDATED", "request_id": str(request.pk), "status": request.status},
        group="push",
    )
    return True


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
