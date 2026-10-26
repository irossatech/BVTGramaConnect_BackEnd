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

Who may do what comes from permissions over the post's target area
(accounts.authz): posts.create_post and submit_post to write and send on,
publish_post (which also auto-publishes the admin's own posts), recheck_post,
discard_post, takedown_post, reverse_decision and mark_urgent. Posts record
the key of the group they were written or decided under (posted_as_role,
decided_by_role); a reversal needs a group ranked strictly higher.
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

from apps.accounts import authz
from apps.accounts.authz import Grant
from apps.audit.services import record
from apps.geography.areas import AreaRef, area_exists, within_q
from apps.geography.models import AreaLevel
from grama.exceptions import ApiError, out_of_area, permission_denied

from . import signals
from .models import Category, Post, PostStatus, ReviewAction, SettingOverride

CREATE = "posts.create_post"
SUBMIT = "posts.submit_post"
REVIEW = "posts.review_post"
PUBLISH = "posts.publish_post"
URGENT = "posts.mark_urgent"
QUEUE = "posts.view_review_queue"
#: Review action -> the permission it needs over the post's target.
DECISION_PERMISSION = {
    "publish": "posts.publish_post",
    "recheck": "posts.recheck_post",
    "discard": "posts.discard_post",
    "unpublish": "posts.takedown_post",
    "reverse": "posts.reverse_decision",
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


def posting_assignment(user, target: AreaRef, perm: str = CREATE) -> Grant | None:
    """The assignment `user` posts under for `target`: the highest one granting `perm` there."""
    return authz.best_grant(user, perm, target)


def reviewer_assignment(user, post: Post, perm: str = REVIEW) -> Grant | None:
    """The highest assignment of `user` granting `perm` over the post's target."""
    return authz.best_grant(user, perm, post.target)


def review_scope_q(user, perm: str = REVIEW) -> Q:
    """Posts whose target lies inside an area where the user holds `perm`."""
    return authz.area_q(user, perm, **TARGET_Q)


def visible_posts(user) -> QuerySet:
    """Own posts, plus every non-draft post inside the areas where the user holds review_post."""
    scope = Q(author=user) | (review_scope_q(user) & ~Q(status=PostStatus.DRAFT))
    return Post.objects.filter(scope)


def review_queue(user) -> QuerySet:
    return (
        Post.objects.filter(status=PostStatus.PENDING)
        .filter(review_scope_q(user, QUEUE))
        .exclude(author=user)
        .order_by("submitted_at", "created_at")
    )


def _rank(user, key: str) -> int | None:
    role = authz.known_roles(user).get(key)
    return role.rank if role else None


def can_reverse(user, reviewer: Grant | None, post: Post) -> bool:
    """A decided post can be reversed by a group ranked strictly above the one that decided it."""
    if reviewer is None or post.status not in DECIDED:
        return False
    if not settings.REVIEW_FIRST_DECISION_WINS:
        return True
    decided_rank = _rank(user, post.decided_by_role)
    if decided_rank is None:
        return reviewer.platform_wide
    return reviewer.rank < decided_rank


def allowed_actions(user, post: Post) -> list[str]:
    actions: list[str] = []
    if post.author_id == user.id:
        if post.status in EDITABLE:
            actions += ["edit", "add_media", "submit"]
        if post.status == PostStatus.DRAFT:
            actions.append("delete")
    target = post.target
    if post.status == PostStatus.PENDING and post.author_id != user.id:
        actions += [name for name in ("publish", "recheck", "discard") if authz.can(user, DECISION_PERMISSION[name], target)]
    if post.status == PostStatus.PUBLISHED and authz.can(user, DECISION_PERMISSION["unpublish"], target):
        actions.append("unpublish")
    if can_reverse(user, reviewer_assignment(user, post, DECISION_PERMISSION["reverse"]), post):
        actions.append("reverse")
    if post.status != PostStatus.DRAFT and authz.can(user, URGENT, target):
        actions.append("set_urgent")
    return actions


# --- Helpers -------------------------------------------------------------------------------------


def _log(post: Post, action: str, from_status: str, to_status: str, actor, role: str, comment: str = "") -> None:
    """role: the key of the group the actor acted under."""
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


def _posting_or_raise(user, target: AreaRef, perm: str = CREATE) -> Grant:
    assignment = posting_assignment(user, target, perm)
    if assignment is None:
        if authz.can(user, perm):
            raise out_of_area(_("You can post only to your own area."))
        raise permission_denied(_("Your role cannot post."))
    return assignment


def _move_on_submit(post: Post, user, assignment: Grant, *, resubmit: bool) -> Post:
    """DRAFT or RECHECK onward: published at once when the author may publish, else reviewed."""
    now = timezone.now()
    from_status = post.status
    auto = PUBLISH in assignment.permissions and settings.ADMIN_POSTS_AUTO_PUBLISH
    if auto:
        changed = Post.objects.filter(pk=post.pk, status__in=EDITABLE).update(
            status=PostStatus.PUBLISHED,
            submitted_at=now,
            published_at=now,
            decided_at=now,
            decided_by=user,
            decided_by_role=assignment.key,
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
    _log(post, action, from_status, to_status, user, assignment.key)
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


def _check_urgent_allowed(assignment: Grant, wants_urgent: bool) -> None:
    """Only groups with posts.mark_urgent mark their own posts urgent (owner decision Q19)."""
    if wants_urgent and URGENT not in assignment.permissions:
        raise permission_denied(_("Only admins can mark a post as urgent."))


def create_post(user, data: PostInput, *, submit: bool = True) -> Post:
    _check_target(data.target)
    assignment = _posting_or_raise(user, data.target)
    _check_urgent_allowed(assignment, data.is_urgent)
    sender = _posting_or_raise(user, data.target, SUBMIT) if submit else None
    with transaction.atomic():
        post = Post.objects.create(
            author=user,
            posted_as_role=assignment.key,
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
        record("post.created", actor=user, actor_role=assignment.key, target=post, target_label=post.title, area=post.target)
        if submit:
            post = _move_on_submit(post, user, sender, resubmit=False)
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
    assignment = None
    if "target" in changes:
        target = changes["target"]
        _check_target(target)
        assignment = _posting_or_raise(user, target)
        values.update(
            target_level=target.level,
            target_area_id=target.id,
            posted_as_role=assignment.key,
            posted_as_level=assignment.area.level,
            posted_as_area_id=assignment.area.id,
        )
    if "event" in changes:
        values.update(_event_values(changes["event"]))
    if "is_urgent" in changes:
        if changes["is_urgent"]:
            _check_urgent_allowed(assignment or _posting_or_raise(user, post.target), True)
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
    assignment = _posting_or_raise(user, post.target, SUBMIT)
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
    perm = DECISION_PERMISSION.get(action, REVIEW)
    reviewer = reviewer_assignment(user, post, perm)
    if reviewer is None:
        if authz.can(user, perm):
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
        "decided_by_role": reviewer.key,
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
        _log(post, logged_action, from_status, new_status, user, reviewer.key, comment)
        _after_commit(signals.post_decided, post=post, action=action, actor=user)
        if new_status == PostStatus.PUBLISHED:
            _after_commit(signals.post_published, post=post)
    return post


def _reverse(user, reviewer: Grant, post: Post, comment: str, to_status: str | None) -> Post:
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
    if not can_reverse(user, reviewer, post):
        raise permission_denied(_("Only a higher level can reverse this decision."))

    now = timezone.now()
    values = {
        "status": to_status,
        "decided_at": now if to_status != PostStatus.PENDING else None,
        "decided_by": user if to_status != PostStatus.PENDING else None,
        "decided_by_role": reviewer.key if to_status != PostStatus.PENDING else "",
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
        _log(post, ReviewAction.Action.REVERSE, from_status, to_status, user, reviewer.key, comment)
        _after_commit(signals.post_decided, post=post, action="reverse", actor=user)
        if to_status == PostStatus.PUBLISHED:
            _after_commit(signals.post_published, post=post)
        if to_status == PostStatus.PENDING:
            _after_commit(signals.post_submitted, post=post)
    return post


# --- Escalation ---------------------------------------------------------------------------------------------


def set_urgent(user, post: Post, is_urgent: bool) -> Post:
    """Someone holding posts.mark_urgent over the post's target marks or clears "urgent" (owner decision Q19)."""
    reviewer = reviewer_assignment(user, post, URGENT)
    if reviewer is None:
        if authz.can(user, URGENT):
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
            actor_role=reviewer.key,
            target=post,
            target_label=post.title,
            area=post.target,
        )
    return post


# --- "Visible to" choices for compose (owner decision Q5) ------------------------------


def posting_targets(user) -> dict:
    """Levels and areas this official may pick as "Visible to", widest first (authz.area_targets)."""
    return authz.area_targets(user, CREATE)


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
