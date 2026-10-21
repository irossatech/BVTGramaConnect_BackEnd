"""Every change to a post's status happens here, nowhere else.

State machine (CLAUDE.md, contract section 10):
    DRAFT -> PENDING | PUBLISHED (admin auto-publish)
    PENDING -> PUBLISHED | RECHECK | DISCARDED
    RECHECK -> PENDING | PUBLISHED (admin auto-publish)
    PUBLISHED -> UNPUBLISHED
    any decided state -> REVERSE by a strictly higher level

Concurrency: each move is one conditional UPDATE, for example
    Post.objects.filter(id=..., status="PENDING").update(status="PUBLISHED", ...)
If no row changes, someone else got there first. This works the same on
SQLite, MySQL and PostgreSQL and needs no row locks.

Each move writes a ReviewAction and an AuditLog entry in the same transaction,
then sends a signal from posts.signals once the transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status as http

from apps.accounts.roles import POSTING_ROLES, REVIEW_ROLES, Role
from apps.accounts.scope import RoleArea, is_within, user_areas
from apps.audit.services import record
from apps.geography.areas import AreaDescriber, AreaRef, area_exists, within_q
from apps.geography.models import AreaLevel, District, Mandal, State
from grama.exceptions import ApiError, out_of_area, permission_denied

from . import signals
from .models import Category, Post, PostStatus, ReviewAction, SettingOverride

ROLE_RANK = {
    Role.SUPER_ADMIN: 0,
    Role.DISTRICT_ADMIN: 1,
    Role.MANDAL_ADMIN: 2,
    Role.VILLAGE_ADMIN: 3,
    Role.PANCHAYAT_MEMBER: 4,
}
NEXT_LEVEL_UP = {
    AreaLevel.VILLAGE: AreaLevel.MANDAL,
    AreaLevel.MANDAL: AreaLevel.DISTRICT,
    AreaLevel.DISTRICT: AreaLevel.STATE,
    AreaLevel.STATE: None,
}
EDITABLE = (PostStatus.DRAFT, PostStatus.RECHECK)
DECIDED = (PostStatus.PUBLISHED, PostStatus.RECHECK, PostStatus.DISCARDED, PostStatus.UNPUBLISHED)
REVERSE_TARGETS = (PostStatus.PUBLISHED, PostStatus.PENDING, PostStatus.RECHECK, PostStatus.DISCARDED)
TARGET_Q = {"level_field": "target_level", "id_field": "target_area_id"}
AUDIT_ACTIONS = {
    ReviewAction.Action.SUBMIT: "post.submitted",
    ReviewAction.Action.RESUBMIT: "post.resubmitted",
    ReviewAction.Action.PUBLISH: "post.published",
    ReviewAction.Action.AUTO_PUBLISH: "post.auto_published",
    ReviewAction.Action.RECHECK: "post.recheck",
    ReviewAction.Action.DISCARD: "post.discarded",
    ReviewAction.Action.UNPUBLISH: "post.unpublished",
    ReviewAction.Action.REVERSE: "post.reversed",
}
#: Levels from the widest down, for the "Visible to" picker.
LEVEL_ORDER = [AreaLevel.STATE, AreaLevel.DISTRICT, AreaLevel.MANDAL, AreaLevel.VILLAGE]


# --- Runtime settings (database value over the Django setting) -----------------------

RUNTIME_SETTINGS = {
    "REVIEW_ESCALATION_HOURS": int,
    "LANGUAGE_EN_ENABLED": bool,
}


def runtime(name: str):
    if name in RUNTIME_SETTINGS:
        override = SettingOverride.objects.filter(key=name).values_list("value", flat=True).first()
        if override is not None:
            return override
    if name == "LANGUAGE_EN_ENABLED":
        return True
    return getattr(settings, name)


# --- Errors -------------------------------------------------------------------------------


class AlreadyDecided(Exception):
    """Another reviewer changed the post first. The view answers 409 with the details."""

    def __init__(self, post: Post):
        super().__init__("already decided")
        self.post = post


def invalid_transition(post: Post, action: str) -> ApiError:
    return ApiError(
        "invalid_transition",
        _("A %(status)s post cannot be %(action)s.") % {"status": post.status.lower(), "action": action},
        status=http.HTTP_409_CONFLICT,
        current_status=post.status,
    )


def comment_required() -> ApiError:
    return ApiError(
        "validation_error",
        _("A comment is required for this action."),
        fields={"comment": [_("This field is required.")]},
    )


# --- Who may do what --------------------------------------------------------------------------


def posting_roles() -> set[str]:
    roles = set(POSTING_ROLES)
    if settings.SUPER_ADMIN_CAN_POST:
        roles.add(Role.SUPER_ADMIN)
    return roles


def posting_assignment(user, target: AreaRef) -> RoleArea | None:
    """The assignment `user` posts under for `target`: the highest matching role."""
    matches = []
    for item in user_areas(user, posting_roles()):
        if item.role == Role.PANCHAYAT_MEMBER:
            if target == item.area:
                matches.append(item)
        elif is_within(target, item):
            matches.append(item)
    return min(matches, key=lambda item: ROLE_RANK[item.role], default=None)


def reviewer_assignment(user, post: Post) -> RoleArea | None:
    """The highest review role of `user` whose area contains the post's target."""
    matches = [item for item in user_areas(user, REVIEW_ROLES) if is_within(post.target, item)]
    return min(matches, key=lambda item: ROLE_RANK[item.role], default=None)


def review_scope_q(user) -> Q:
    """Posts whose target lies inside one of the user's review areas."""
    q = Q(pk__in=[])
    for item in user_areas(user, REVIEW_ROLES):
        if item.is_global:
            return Q()
        q |= within_q(item.area, **TARGET_Q)
    return q


def visible_posts(user) -> QuerySet:
    """Own posts, plus every non-draft post inside the user's review areas."""
    scope = Q(author=user) | (review_scope_q(user) & ~Q(status=PostStatus.DRAFT))
    return Post.objects.filter(scope)


def review_queue(user) -> QuerySet:
    return (
        Post.objects.filter(status=PostStatus.PENDING)
        .filter(review_scope_q(user))
        .exclude(author=user)
        .order_by("submitted_at", "created_at")
    )


def can_reverse(reviewer: RoleArea | None, post: Post) -> bool:
    if reviewer is None or post.status not in DECIDED:
        return False
    if not settings.REVIEW_FIRST_DECISION_WINS:
        return True
    decided_rank = ROLE_RANK.get(post.decided_by_role)
    if decided_rank is None:
        return reviewer.role == Role.SUPER_ADMIN
    return ROLE_RANK[reviewer.role] < decided_rank


def allowed_actions(user, post: Post) -> list[str]:
    actions: list[str] = []
    if post.author_id == user.id:
        if post.status in EDITABLE:
            actions += ["edit", "add_media", "submit"]
        if post.status == PostStatus.DRAFT:
            actions.append("delete")
    reviewer = reviewer_assignment(user, post)
    if reviewer is not None:
        if post.status == PostStatus.PENDING and post.author_id != user.id:
            actions += ["publish", "recheck", "discard"]
        if post.status == PostStatus.PUBLISHED:
            actions.append("unpublish")
        if can_reverse(reviewer, post):
            actions.append("reverse")
        if post.status != PostStatus.DRAFT:
            actions.append("set_urgent")
    return actions


# --- Helpers -------------------------------------------------------------------------------------


def _log(post: Post, action: str, from_status: str, to_status: str, actor, role: str, comment: str = "") -> None:
    ReviewAction.objects.create(
        post=post,
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=comment,
        actor=actor,
        actor_role=role,
    )
    record(
        AUDIT_ACTIONS[action],
        actor=actor,
        actor_role=role,
        target=post,
        target_label=post.title,
        area=post.target,
        via="system" if actor is None else "app",
        data={"from": from_status, "to": to_status, **({"comment": comment} if comment else {})},
    )


def _after_commit(signal, **kwargs) -> None:
    transaction.on_commit(lambda: signal.send(sender=Post, **kwargs))


def _reload(post: Post) -> Post:
    return Post.objects.select_related("author", "category", "decided_by").get(pk=post.pk)


def _check_target(target: AreaRef) -> None:
    if not area_exists(target, active_only=True):
        raise ApiError(
            "validation_error",
            _("No such active area."),
            fields={"target": [_("No such active area.")]},
        )


def _posting_or_raise(user, target: AreaRef) -> RoleArea:
    assignment = posting_assignment(user, target)
    if assignment is None:
        if user_areas(user, posting_roles()):
            raise out_of_area(_("You can post only to your own area."))
        raise permission_denied(_("Your role cannot post."))
    return assignment


def _move_on_submit(post: Post, user, assignment: RoleArea, *, resubmit: bool) -> Post:
    """DRAFT or RECHECK onward: auto-publish for admins, review for everyone else."""
    now = timezone.now()
    from_status = post.status
    auto = assignment.role != Role.PANCHAYAT_MEMBER and settings.ADMIN_POSTS_AUTO_PUBLISH
    if auto:
        changed = Post.objects.filter(pk=post.pk, status__in=EDITABLE).update(
            status=PostStatus.PUBLISHED,
            submitted_at=now,
            published_at=now,
            decided_at=now,
            decided_by=user,
            decided_by_role=assignment.role,
            updated_at=now,
        )
        action, to_status, signal = ReviewAction.Action.AUTO_PUBLISH, PostStatus.PUBLISHED, signals.post_published
    else:
        changed = Post.objects.filter(pk=post.pk, status__in=EDITABLE).update(
            status=PostStatus.PENDING,
            submitted_at=now,
            decided_at=None,
            decided_by=None,
            decided_by_role="",
            escalated_at=None,
            escalation_level="",
            updated_at=now,
        )
        action = ReviewAction.Action.RESUBMIT if resubmit else ReviewAction.Action.SUBMIT
        to_status, signal = PostStatus.PENDING, signals.post_submitted
    if not changed:
        raise invalid_transition(_reload(post), "submitted")
    post = _reload(post)
    _log(post, action, from_status, to_status, user, assignment.role)
    _after_commit(signal, post=post)
    return post


# --- Authoring ---------------------------------------------------------------------------------------


@dataclass
class PostInput:
    category: Category
    language: str
    title: str
    body: str
    target: AreaRef
    #: {"kind", "start_date", "end_date"} with date objects, or None (owner decision Q7).
    event: dict | None = None
    is_urgent: bool = False


def _event_values(event: dict | None) -> dict:
    if not event:
        return {"event_kind": "", "event_start": None, "event_end": None}
    return {
        "event_kind": event["kind"],
        "event_start": event["start_date"],
        "event_end": event.get("end_date"),
    }


def _check_urgent_allowed(role: str, wants_urgent: bool) -> None:
    """Only admins mark their own posts urgent (owner decision Q19)."""
    if wants_urgent and role == Role.PANCHAYAT_MEMBER:
        raise permission_denied(_("Only admins can mark a post as urgent."))


def create_post(user, data: PostInput, *, submit: bool = True) -> Post:
    _check_target(data.target)
    assignment = _posting_or_raise(user, data.target)
    _check_urgent_allowed(assignment.role, data.is_urgent)
    with transaction.atomic():
        post = Post.objects.create(
            author=user,
            posted_as_role=assignment.role,
            posted_as_level=assignment.area.level,
            posted_as_area_id=assignment.area.id,
            category=data.category,
            language=data.language,
            title=data.title,
            body=data.body,
            target_level=data.target.level,
            target_area_id=data.target.id,
            status=PostStatus.DRAFT,
            is_urgent=data.is_urgent,
            **_event_values(data.event),
        )
        record("post.created", actor=user, actor_role=assignment.role, target=post, target_label=post.title, area=post.target)
        if submit:
            post = _move_on_submit(post, user, assignment, resubmit=False)
    return _reload(post)


def _own_editable(user, post: Post, action: str) -> None:
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can change this post."))
    if post.status not in EDITABLE:
        raise invalid_transition(post, action)


def update_post(user, post: Post, changes: dict) -> Post:
    _own_editable(user, post, "edited")
    values = {}
    for name in ("title", "body", "language", "category"):
        if name in changes:
            values[name] = changes[name]
    role = post.posted_as_role
    if "target" in changes:
        target = changes["target"]
        _check_target(target)
        assignment = _posting_or_raise(user, target)
        role = assignment.role
        values.update(
            target_level=target.level,
            target_area_id=target.id,
            posted_as_role=assignment.role,
            posted_as_level=assignment.area.level,
            posted_as_area_id=assignment.area.id,
        )
    if "event" in changes:
        values.update(_event_values(changes["event"]))
    if "is_urgent" in changes:
        _check_urgent_allowed(role, changes["is_urgent"])
        values["is_urgent"] = changes["is_urgent"]
    if values:
        values["updated_at"] = timezone.now()
        with transaction.atomic():
            if not Post.objects.filter(pk=post.pk, status__in=EDITABLE).update(**values):
                raise invalid_transition(_reload(post), "edited")
            post = _reload(post)
            record("post.updated", actor=user, target=post, target_label=post.title, area=post.target)
    return _reload(post)


def delete_post(user, post: Post) -> None:
    if post.author_id != user.id:
        raise permission_denied(_("Only the author can delete this post."))
    with transaction.atomic():
        media = list(post.media.all())
        deleted, _rows = Post.objects.filter(pk=post.pk, status=PostStatus.DRAFT).delete()
        if not deleted:
            raise invalid_transition(_reload(post), "deleted")
        record("post.deleted", actor=user, target_label=post.title, area=post.target, data={"post_id": str(post.pk)})
    for item in media:
        for field in (item.image, item.thumbnail):
            if field:
                field.storage.delete(field.name)


def submit_post(user, post: Post) -> Post:
    _own_editable(user, post, "submitted")
    assignment = _posting_or_raise(user, post.target)
    with transaction.atomic():
        return _move_on_submit(post, user, assignment, resubmit=post.status == PostStatus.RECHECK)


# --- Review -----------------------------------------------------------------------------------------------

DECISIONS = {
    # action: (from status, to status, ReviewAction, comment required)
    "publish": (PostStatus.PENDING, PostStatus.PUBLISHED, ReviewAction.Action.PUBLISH, False),
    "recheck": (PostStatus.PENDING, PostStatus.RECHECK, ReviewAction.Action.RECHECK, True),
    "discard": (PostStatus.PENDING, PostStatus.DISCARDED, ReviewAction.Action.DISCARD, True),
    "unpublish": (PostStatus.PUBLISHED, PostStatus.UNPUBLISHED, ReviewAction.Action.UNPUBLISH, True),
}
VERBS = {"publish": "published", "recheck": "sent for recheck", "discard": "discarded", "unpublish": "taken down"}


def review(user, post: Post, action: str, comment: str = "", to_status: str | None = None) -> Post:
    comment = (comment or "").strip()
    reviewer = reviewer_assignment(user, post)
    if reviewer is None:
        if user_areas(user, REVIEW_ROLES):
            raise out_of_area(_("This post is outside your area."))
        raise permission_denied(_("Your role cannot review posts."))

    if action == "reverse":
        return _reverse(user, reviewer, post, comment, to_status)
    if action not in DECISIONS:
        raise ApiError("validation_error", _("Unknown action."), fields={"action": [_("Unknown action.")]})

    from_status, new_status, logged_action, needs_comment = DECISIONS[action]
    if needs_comment and not comment:
        raise comment_required()
    if from_status == PostStatus.PENDING and post.author_id == user.id:
        raise permission_denied(_("You cannot review your own post."))

    now = timezone.now()
    values = {
        "status": new_status,
        "decided_at": now,
        "decided_by": user,
        "decided_by_role": reviewer.role,
        "updated_at": now,
    }
    if new_status == PostStatus.PUBLISHED:
        values["published_at"] = now

    with transaction.atomic():
        changed = Post.objects.filter(pk=post.pk, status=from_status).update(**values)
        if not changed:
            current = _reload(post)
            if from_status == PostStatus.PENDING and current.status in DECIDED and current.decided_at:
                raise AlreadyDecided(current)
            if from_status == PostStatus.PUBLISHED and current.status == PostStatus.UNPUBLISHED:
                raise AlreadyDecided(current)
            raise invalid_transition(current, VERBS[action])
        post = _reload(post)
        _log(post, logged_action, from_status, new_status, user, reviewer.role, comment)
        _after_commit(signals.post_decided, post=post, action=action, actor=user)
        if new_status == PostStatus.PUBLISHED:
            _after_commit(signals.post_published, post=post)
    return post


def _reverse(user, reviewer: RoleArea, post: Post, comment: str, to_status: str | None) -> Post:
    if not comment:
        raise comment_required()
    if to_status not in REVERSE_TARGETS or to_status == post.status:
        raise ApiError(
            "validation_error",
            _("Pick a different status to move the post to."),
            fields={"to_status": [_("One of %(s)s, not the current one.") % {"s": ", ".join(REVERSE_TARGETS)}]},
        )
    if post.status not in DECIDED:
        raise invalid_transition(post, "reversed")
    if not can_reverse(reviewer, post):
        raise permission_denied(_("Only a higher level can reverse this decision."))

    now = timezone.now()
    values = {
        "status": to_status,
        "decided_at": now if to_status != PostStatus.PENDING else None,
        "decided_by": user if to_status != PostStatus.PENDING else None,
        "decided_by_role": reviewer.role if to_status != PostStatus.PENDING else "",
        "updated_at": now,
    }
    if to_status == PostStatus.PUBLISHED and not post.published_at:
        values["published_at"] = now
    if to_status == PostStatus.PENDING:
        values.update(submitted_at=now, escalated_at=None, escalation_level="")

    with transaction.atomic():
        changed = Post.objects.filter(pk=post.pk, status=post.status, decided_at=post.decided_at).update(**values)
        if not changed:
            raise AlreadyDecided(_reload(post))
        from_status = post.status
        post = _reload(post)
        _log(post, ReviewAction.Action.REVERSE, from_status, to_status, user, reviewer.role, comment)
        _after_commit(signals.post_decided, post=post, action="reverse", actor=user)
        if to_status == PostStatus.PUBLISHED:
            _after_commit(signals.post_published, post=post)
        if to_status == PostStatus.PENDING:
            _after_commit(signals.post_submitted, post=post)
    return post


# --- Escalation ---------------------------------------------------------------------------------------------


def set_urgent(user, post: Post, is_urgent: bool) -> Post:
    """A reviewer whose area contains the post marks or clears "urgent" (owner decision Q19)."""
    reviewer = reviewer_assignment(user, post)
    if reviewer is None:
        if user_areas(user, REVIEW_ROLES):
            raise out_of_area(_("This post is outside your area."))
        raise permission_denied(_("Only admins can mark a post as urgent."))
    if post.status == PostStatus.DRAFT:
        raise invalid_transition(post, "marked urgent")
    if post.is_urgent == is_urgent:
        return _reload(post)
    with transaction.atomic():
        Post.objects.filter(pk=post.pk).update(is_urgent=is_urgent, updated_at=timezone.now())
        post = _reload(post)
        record(
            "post.urgent_set" if is_urgent else "post.urgent_cleared",
            actor=user,
            actor_role=reviewer.role,
            target=post,
            target_label=post.title,
            area=post.target,
        )
    return post


# --- "Visible to" choices for compose (owner decision Q5) ------------------------------


def _children(area: AreaRef) -> list[AreaRef]:
    if area.level == AreaLevel.STATE:
        return [AreaRef(AreaLevel.DISTRICT, i) for i in District.objects.active().filter(state_id=area.id).values_list("id", flat=True)]
    if area.level == AreaLevel.DISTRICT:
        return [AreaRef(AreaLevel.MANDAL, i) for i in Mandal.objects.active().filter(district_id=area.id).values_list("id", flat=True)]
    if area.level == AreaLevel.MANDAL:
        from apps.geography.models import Village

        return [AreaRef(AreaLevel.VILLAGE, i) for i in Village.objects.active().filter(mandal_id=area.id).values_list("id", flat=True)]
    return []


def posting_targets(user) -> dict:
    """Levels and areas this official may pick as "Visible to", widest first.

    Own area at own level, and the areas one level below it. Two or more levels
    below would be too long a list, so those say pick_via="geo": the app walks
    down from the level above with the geography lists.
    """
    choices: dict[str, list[AreaRef]] = {}
    via_geo: set[str] = set()
    for item in user_areas(user, posting_roles()):
        if item.role == Role.SUPER_ADMIN:
            states = [AreaRef(AreaLevel.STATE, i) for i in State.objects.active().values_list("id", flat=True)]
            choices.setdefault(AreaLevel.STATE, []).extend(states)
            for state in states:
                choices.setdefault(AreaLevel.DISTRICT, []).extend(_children(state))
            via_geo.update({AreaLevel.MANDAL, AreaLevel.VILLAGE})
            continue
        if not area_exists(item.area, active_only=True):
            continue
        choices.setdefault(item.area.level, []).append(item.area)
        if item.role == Role.PANCHAYAT_MEMBER:
            continue
        below = _children(item.area)
        if below:
            choices.setdefault(below[0].level, []).extend(below)
        depth = LEVEL_ORDER.index(item.area.level)
        via_geo.update(LEVEL_ORDER[depth + 2:])
    describer = AreaDescriber()
    describer.prime([ref for refs in choices.values() for ref in refs])
    levels = []
    for level in LEVEL_ORDER:
        refs = sorted(set(choices.get(level, [])), key=lambda ref: ref.id)
        if refs:
            entry = {"level": level, "areas": [a for a in (describer.describe(r) for r in refs) if a]}
            if level in via_geo:
                entry["pick_via"] = "geo"
            levels.append(entry)
        elif level in via_geo:
            levels.append({"level": level, "areas": [], "pick_via": "geo"})
    return {"levels": levels}


def escalate_overdue(now=None) -> int:
    """Flag PENDING posts nobody decided within REVIEW_ESCALATION_HOURS.

    Each run moves an overdue post one level up: a village post first alerts
    the mandal's admins, REVIEW_ESCALATION_HOURS later the district's, then
    the state's. Lower reviewers keep the post in their queue.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(hours=int(runtime("REVIEW_ESCALATION_HOURS")))
    candidates = Post.objects.filter(status=PostStatus.PENDING).filter(
        Q(escalated_at__isnull=True, submitted_at__lte=cutoff) | Q(escalated_at__lte=cutoff)
    )
    count = 0
    for post in candidates:
        current = post.escalation_level or post.target_level
        next_level = NEXT_LEVEL_UP.get(current)
        if next_level is None:
            continue
        with transaction.atomic():
            changed = Post.objects.filter(
                pk=post.pk, status=PostStatus.PENDING, escalated_at=post.escalated_at
            ).update(escalated_at=now, escalation_level=next_level)
            if not changed:
                continue
            post = _reload(post)
            ReviewAction.objects.create(
                post=post,
                action=ReviewAction.Action.ESCALATE,
                from_status=PostStatus.PENDING,
                to_status=PostStatus.PENDING,
                comment=next_level,
            )
            record(
                "post.escalated",
                target=post,
                target_label=post.title,
                area=post.target,
                via="system",
                data={"to_level": next_level, "hours": int(runtime("REVIEW_ESCALATION_HOURS"))},
            )
            _after_commit(signals.post_escalated, post=post, level=next_level)
        count += 1
    return count
