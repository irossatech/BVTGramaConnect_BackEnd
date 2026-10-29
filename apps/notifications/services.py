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
from apps.geography.areas import DEPTH, AreaDescriber, AreaRef, ancestries, ancestry
from apps.geography.models import AreaLevel

from .models import Notification, NotificationKind, NotificationTemplate
from .push import topic_for
from .templates_data import BY_KEY

logger = logging.getLogger("grama.notifications")

REVIEW = "posts.review_post"
NEXT_LEVEL_UP = {AreaLevel.VILLAGE: AreaLevel.MANDAL, AreaLevel.MANDAL: AreaLevel.DISTRICT,
                 AreaLevel.DISTRICT: AreaLevel.STATE, AreaLevel.STATE: None}


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


def admins_over_targets(at: dict[AreaRef, set[str] | None], *, include_super_admins=False):
    """admins_over() for many areas at once: {area: levels or None for all} -> one user query.

    Ancestry is loaded with one query per level, so a post for 200 areas costs the same as one.
    """
    chains = ancestries(at)
    areas = {
        AreaRef(level, area_id)
        for ref, levels in at.items()
        for level, area_id in chains[ref].items()
        if levels is None or level in levels
    }
    users = authz.holders(REVIEW, areas, platform_wide=False) if areas else User.objects.none()
    if include_super_admins:
        users = users | authz.holders(REVIEW, platform_wide=True)
    return users.distinct()


def reviewers_for(post):
    """The admins over any of the post's areas. The Super Admin sees the queue instead of a ping, except
    for a post to Everyone, which only platform-wide reviewers can review."""
    refs = post.target_refs
    everyone = any(ref.level == AreaLevel.STATE for ref in refs)
    return admins_over_targets(dict.fromkeys(refs), include_super_admins=everyone).exclude(pk=post.author_id)


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
    """Queue one job that pushes to the topic of each of the post's areas, in the post's language.

    A phone hears it once: its app follows its village's, mandal's, district's and state's topics,
    and a post never has two areas where one contains the other. Urgent posts go out at high
    priority, everything else at normal (owner decision Q19).
    """
    title = render("post_published", post.language, **_values(post, post.language))
    async_task(
        "apps.notifications.tasks.send_push_to_topics",
        [topic_for(ref.level, ref.id) for ref in post.target_refs],
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


def _service_request_note(user, request, template_key: str) -> Notification:
    """Inbox item and queued push about a service request, in the user's language.

    The text names the service, the area and the citizen's name; never the phone, email or address.
    """
    language = user.preferred_language
    village = AreaRef(AreaLevel.VILLAGE, request.village_id)
    area = AreaDescriber(language).describe(village)
    service = request.service.display_name(language)
    text = render(template_key, language, area=area["name"] if area else "", service=service, name=request.name)
    note = Notification.objects.create(
        user=user,
        kind=NotificationKind.SERVICE_REQUEST,
        title=text[:255],
        body=service[:500],
        service_request=request,
        area_level=AreaLevel.VILLAGE,
        area_id=request.village_id,
    )
    async_task(
        "apps.notifications.tasks.send_push_to_user",
        str(user.pk),
        text[:255],
        service,
        {
            "type": NotificationKind.SERVICE_REQUEST,
            "request_id": str(request.pk),
            "area_level": AreaLevel.VILLAGE,
            "area_id": request.village_id,
        },
        group="push",
    )
    return note


def notify_service_request(request) -> list[Notification]:
    """A citizen's new request: the admins of its village, mandal and district each get an inbox item and a push.

    The admin it was assigned to gets "assigned to you". Like posts, the Super Admin is not pinged
    for every request; they see the list in the console.
    """
    village = AreaRef(AreaLevel.VILLAGE, request.village_id)
    areas = [AreaRef(level, area_id) for level, area_id in ancestry(village).items()]
    users = authz.holders(SERVICE_MANAGE, areas, platform_wide=False).distinct() if areas else User.objects.none()
    return [
        _service_request_note(
            user, request, "service_request_assigned_you" if user.pk == request.assigned_to_id else "service_request_new"
        )
        for user in users
    ]


def tell_assignee(request) -> Notification | None:
    """After an official re-assigns a request: tell the new assignee."""
    assignee = User.objects.filter(pk=request.assigned_to_id, is_active=True).first()
    if assignee is None:
        return None
    return _service_request_note(assignee, request, "service_request_assigned_you")


def tell_citizen_service_request(request) -> bool:
    """After a status change: push the new status (and the note) to the phone that sent the request."""
    from apps.service_requests.models import status_label

    device = request.device
    if not device.fcm_token:
        return False
    language = device.language
    status = status_label(request.status, language)
    text = render(
        "service_request_updated", language,
        service=request.service.display_name(language), status=status, note=request.note,
    )
    async_task(
        "apps.notifications.tasks.send_push_to_citizen",
        str(device.pk),
        text[:255],
        (request.note or status)[:500],
        {"type": "SERVICE_REQUEST_UPDATED", "request_id": str(request.pk), "status": request.status},
        group="push",
    )
    return True


def escalation_levels(refs: list[AreaRef], level: str) -> dict[AreaRef, str]:
    """Which level to alert for each of a post's areas when the post climbs to `level`.

    The post climbs from its lowest area. An area whose ladder has `level` above it is alerted
    there. One already above `level` (a district while the post's villages reach the mandals) is
    alerted once, at its own next level up, on the first escalation only.
    """
    lowest = max((ref.level for ref in refs), key=lambda lv: DEPTH[lv])
    first = level == NEXT_LEVEL_UP.get(lowest)
    alerts = {}
    for ref in refs:
        if DEPTH[ref.level] > DEPTH[level]:
            alerts[ref] = level
        elif first and NEXT_LEVEL_UP.get(ref.level):
            alerts[ref] = NEXT_LEVEL_UP[ref.level]
    return alerts


def alert_escalation(post, level: str, hours: int) -> list[Notification]:
    alerts = escalation_levels(post.target_refs, level)
    below_state = {ref: {lv} for ref, lv in alerts.items() if lv != AreaLevel.STATE}
    users = admins_over_targets(below_state, include_super_admins=AreaLevel.STATE in alerts.values())
    return notify_users(
        users.exclude(pk=post.author_id),
        post=post,
        kind=NotificationKind.ESCALATION,
        template_key="escalation",
        hours=hours,
    )
