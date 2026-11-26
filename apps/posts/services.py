"""Every change to a post's status happens here, nowhere else.

State machine (CLAUDE.md, contract section 10):
    DRAFT -> PENDING | PUBLISHED (admin auto-publish)
    (new) -> PENDING for a citizen's phone (create_citizen_post; contract section 15)
    PENDING -> PUBLISHED | RECHECK | DISCARDED (never RECHECK for a citizen post)
    BLOCKED: citizen posts a block closed from 2026-11-20 to 2026-11-21 only; a block changes no post since
    RECHECK -> PENDING | PUBLISHED (admin auto-publish)
    PUBLISHED -> UNPUBLISHED
    any decided state -> REVERSE by a strictly higher level, or by a Super Admin (owner, 2026-11-21)

Concurrency: each move is one conditional UPDATE, for example
    Post.objects.filter(id=..., status="PENDING").update(status="PUBLISHED", ...)
If no row changes, someone else got there first. This works the same on
SQLite, MySQL and PostgreSQL and needs no row locks.

Each move writes a ReviewAction and an AuditLog entry in the same transaction,
then sends a signal from posts.signals once the transaction commits.

Who may do what comes from permissions (accounts.authz): posts.create_post and
submit_post to write and send on, publish_post (which also auto-publishes the
admin's own posts), recheck_post, discard_post, takedown_post, reverse_decision
and mark_urgent. A post is for one or more areas (PostTarget: "Everyone", or any
districts, mandals and villages). Anyone who may post may post to any area; a
reviewer acts when any of the post's areas lies inside their own, and the first
decision counts for the whole post (owner decision of 2026-10-28). Posts record
the key of the group they were written or decided under (posted_as_role,
decided_by_role); a reversal needs a group ranked strictly higher, except that a
Super Admin may change any decision (owner's decision of 2026-11-21).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status as http

from apps.accounts import authz
from apps.accounts.authz import Grant
from apps.accounts.roles import Role
from apps.audit.services import record
from apps.geography.areas import DEPTH, AreaRef, ancestries, within_q
from apps.geography.models import MODEL_BY_LEVEL, AreaLevel
from grama.exceptions import ApiError, out_of_area, permission_denied

from . import signals
from .models import CITIZEN_ROLE, Category, Post, PostStatus, PostTarget, ReviewAction, SettingOverride
from .sharing import publish_with_share_code
from .translation import detect_language

CREATE = "posts.create_post"
SUBMIT = "posts.submit_post"
REVIEW = "posts.review_post"
PUBLISH = "posts.publish_post"
URGENT = "posts.mark_urgent"
QUEUE = "posts.view_review_queue"
BLOCK_CITIZEN = "posts.block_citizen"
#: Review action -> the permission it needs over the post's target.
DECISION_PERMISSION = {
    "publish": "posts.publish_post",
    "recheck": "posts.recheck_post",
    "discard": "posts.discard_post",
    "unpublish": "posts.takedown_post",
    "reverse": "posts.reverse_decision",
    # Not a review button: only citizens.services.block moves a post here (ReviewSerializer has no "block").
    "block": BLOCK_CITIZEN,
}
NEXT_LEVEL_UP = {
    AreaLevel.VILLAGE: AreaLevel.MANDAL,
    AreaLevel.MANDAL: AreaLevel.DISTRICT,
    AreaLevel.DISTRICT: AreaLevel.STATE,
    AreaLevel.STATE: None,
}
EDITABLE = (PostStatus.DRAFT, PostStatus.RECHECK)
DECIDED = (PostStatus.PUBLISHED, PostStatus.RECHECK, PostStatus.DISCARDED, PostStatus.UNPUBLISHED, PostStatus.BLOCKED)
REVERSE_TARGETS = (PostStatus.PUBLISHED, PostStatus.PENDING, PostStatus.RECHECK, PostStatus.DISCARDED)
AUDIT_ACTIONS = {
    ReviewAction.Action.SUBMIT: "post.submitted",
    ReviewAction.Action.RESUBMIT: "post.resubmitted",
    ReviewAction.Action.PUBLISH: "post.published",
    ReviewAction.Action.AUTO_PUBLISH: "post.auto_published",
    ReviewAction.Action.RECHECK: "post.recheck",
    ReviewAction.Action.DISCARD: "post.discarded",
    ReviewAction.Action.UNPUBLISH: "post.unpublished",
    ReviewAction.Action.REVERSE: "post.reversed",
    ReviewAction.Action.BLOCK: "post.blocked",
}


# --- Runtime settings (database value over the Django setting) -----------------------

RUNTIME_SETTINGS = {
    "REVIEW_ESCALATION_HOURS": int,
    "LANGUAGE_EN_ENABLED": bool,
    "CITIZEN_POSTS_PER_DAY": int,
    #: Banners on a citizen's Home: 0 = no limit, N = at most N (BVTGC-92).
    "ADS_PUBLIC_LIMIT": int,
    # The citizen wallet (BVTGC-129, contract section 21 "Settings"): the switch, the pilot mandals and the
    # reward rules. Read where they are used, so a change in the console applies to the next sign-up or decision.
    "WALLET_ENABLED": bool,
    "WALLET_PILOT_MANDALS": list,
    "REFERRAL_REWARD_POINTS": int,
    "WELCOME_BONUS_POINTS": int,
    "REFERRAL_HOLD_DAYS": int,
    "REFERRAL_ACTIVE_DAYS": int,
    "REFERRALS_PER_MONTH": int,
    "WELCOME_BONUSES_PER_PHONE": int,
}


#: How long a process keeps a runtime setting before reading it again (audit finding BE-DB-027). A save
#: clears it at once in the saving process (receivers.forget_runtime_setting); other processes see a
#: change within this many seconds.
RUNTIME_CACHE_SECONDS = 30
_NOT_CACHED = object()


def runtime_cache_key(name: str) -> str:
    return f"runtime-setting:{name}"


def runtime(name: str):
    if name in RUNTIME_SETTINGS:
        override = cache.get(runtime_cache_key(name), _NOT_CACHED)
        if override is _NOT_CACHED:
            override = SettingOverride.objects.filter(key=name).values_list("value", flat=True).first()
            cache.set(runtime_cache_key(name), override, RUNTIME_CACHE_SECONDS)  # None: no override, also kept
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


#: One whole sentence per action, so each translates as a sentence (Telugu cannot be glued together
#: from English verbs). The keys are the actions callers pass to invalid_transition.
TRANSITION_MESSAGES = {
    "changed": gettext_lazy("A %(status)s post cannot be changed."),
    "edited": gettext_lazy("A %(status)s post cannot be edited."),
    "submitted": gettext_lazy("A %(status)s post cannot be submitted."),
    "deleted": gettext_lazy("A %(status)s post cannot be deleted."),
    "reversed": gettext_lazy("A %(status)s post cannot be reversed."),
    "marked urgent": gettext_lazy("A %(status)s post cannot be marked urgent."),
    "published": gettext_lazy("A %(status)s post cannot be published."),
    "sent for recheck": gettext_lazy("A %(status)s post cannot be sent for recheck."),
    "discarded": gettext_lazy("A %(status)s post cannot be discarded."),
    "taken down": gettext_lazy("A %(status)s post cannot be taken down."),
    "blocked": gettext_lazy("A %(status)s post cannot be blocked."),
}


def invalid_transition(post: Post, action: str) -> ApiError:
    message = TRANSITION_MESSAGES.get(action, TRANSITION_MESSAGES["changed"])
    return ApiError(
        "invalid_transition",
        message % {"status": str(PostStatus(post.status).label).lower()},
        status=http.HTTP_409_CONFLICT,
        current_status=post.status,
    )


def citizen_cannot_recheck(post: Post) -> ApiError:
    return ApiError(
        "invalid_transition",
        _("A citizen cannot edit a post, so it cannot be sent for recheck. Publish or discard it."),
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


def posting_assignment(user, targets: list[AreaRef] | AreaRef | None = None, perm: str = CREATE) -> Grant | None:
    """The assignment `user` posts under: the highest one granting `perm` over the first target, else
    the highest granting it anywhere. Anyone who may post may post to any area (owner decision of
    2026-10-28), so the target only picks which of several roles the post goes out under."""
    first = targets[0] if isinstance(targets, list) and targets else targets
    if isinstance(first, AreaRef):
        grant = authz.best_grant(user, perm, first)
        if grant is not None:
            return grant
    return authz.best_grant(user, perm)


def reviewer_assignment(user, post: Post, perm: str = REVIEW) -> Grant | None:
    """The highest assignment of `user` granting `perm` over any of the post's areas."""
    refs = post.target_refs
    authz.prime(user, refs)
    found = [grant for ref in refs if (grant := authz.best_grant(user, perm, ref)) is not None]
    return min(found, key=lambda grant: grant.rank, default=None)


def targets_match(condition: Q) -> Exists:
    """Posts with at least one target (PostTarget.area_level, area_id) matching `condition`.

    An EXISTS, so a post with many matching targets is still one row and needs no distinct().
    """
    return Exists(PostTarget.objects.filter(post=OuterRef("pk")).filter(condition))


def targets_within(container: AreaRef) -> Exists:
    """Posts with a target that is `container` or lies inside it."""
    return targets_match(within_q(container))


def review_scope_q(user, perm: str = REVIEW) -> Exists:
    """Posts with a target inside an area where the user holds `perm`."""
    return targets_match(authz.area_q(user, perm))


def covering_scope_q(user) -> Exists | Q:
    """Posts with a target that is one of the user's areas or contains one (their village, mandal,
    district, the state: Everyone), over every active official role assignment, whatever its other
    permissions. A citizen's wallet role never counts (BVTGC-121).

    The same chain the citizen feed reads for a village (feed.views.targets_for_village), so an official
    sees at least what the citizens of their area see. One query per level for the whole chain.
    """
    found = authz.official_grants(user)
    if any(grant.platform_wide for grant in found):
        return Q()
    query = Q(pk__in=[])
    for chain in ancestries({grant.area for grant in found}).values():
        for level, area_id in chain.items():
            query |= Q(area_level=level, area_id=area_id)
    return targets_match(query)


def visible_posts(user) -> QuerySet:
    """What `GET /posts/` lists and what an official may open:

    - their own posts;
    - every non-draft post inside the areas where they hold review_post;
    - published citizen posts where they may take posts down (Panchayat members, owner 2026-11-17);
    - published posts for an area that is or contains one of their areas: their village, mandal,
      district or Everyone (backend plan of 2026-11-20). Read only: every action keeps its own check
      (reviewer_assignment, may_take_down, the author), so this adds no button.
    """
    takedown = review_scope_q(user, DECISION_PERMISSION["unpublish"]) & Q(
        citizen__isnull=False, status=PostStatus.PUBLISHED
    )
    covering = covering_scope_q(user) & Q(status=PostStatus.PUBLISHED)
    scope = Q(author=user) | (review_scope_q(user) & ~Q(status=PostStatus.DRAFT)) | takedown | covering
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
    """A decided post can be reversed by a group ranked strictly above the one that decided it, or by a Super Admin."""
    if reviewer is None or post.status not in DECIDED:
        return False
    # A published post is effectively a take-down by another route; apply the same rank rule.
    if post.status == PostStatus.PUBLISHED and not may_take_down(user, reviewer, post):
        return False
    if reviewer.platform_wide:
        # Owner's decision of 2026-11-21: a Super Admin may change any decision, another Super Admin's and
        # their own included, and bring back a post a block closed. A comment is still required.
        return True
    if not settings.REVIEW_FIRST_DECISION_WINS:
        return True
    decided_rank = _rank(user, post.decided_by_role)
    if decided_rank is None:
        return reviewer.platform_wide
    return reviewer.rank < decided_rank


def may_take_down(user, reviewer: Grant | None, post: Post) -> bool:
    """Owner rule of 2026-11-17. Super Admin: any post. District/Mandal/Village Admin: citizen posts,
    Panchayat members' posts and their own. Panchayat member: citizen posts and their own."""
    if reviewer is None:
        return False
    if reviewer.platform_wide or post.citizen_id is not None or post.author_id == user.id:
        return True
    return reviewer.key != Role.PANCHAYAT_MEMBER and post.posted_as_role == Role.PANCHAYAT_MEMBER


def takedown_grant(user, post: Post) -> Grant | None:
    """The grant to use for a take-down decision.

    For own posts posted outside the user's area, fall back to the best grant the user holds
    anywhere, so a Panchayat member can still take down their own post even if it targets another
    district (owner decision of 2026-11-17).
    """
    perm = DECISION_PERMISSION["unpublish"]
    grant = reviewer_assignment(user, post, perm)
    return grant if grant is not None or post.author_id != user.id else authz.best_grant(user, perm)


def allowed_actions(user, post: Post, *, citizen_blocked: bool | None = None) -> list[str]:
    """citizen_blocked: whether the phone behind a citizen post is blocked, when the caller already knows
    (the post serializer's phone summary); None asks the database."""
    actions: list[str] = []
    if post.author_id is not None and post.author_id == user.id:
        if post.status in EDITABLE:
            actions += ["edit", "add_media", "submit"]
        if post.status == PostStatus.DRAFT:
            actions.append("delete")
    def on_post(perm: str) -> bool:
        return reviewer_assignment(user, post, perm) is not None

    if post.status == PostStatus.PENDING and post.author_id != user.id:
        # A citizen cannot edit a post, so it is never sent back for recheck (contract section 15).
        decisions = ("publish", "discard") if post.citizen_id else ("publish", "recheck", "discard")
        actions += [name for name in decisions if on_post(DECISION_PERMISSION[name])]
    if post.status == PostStatus.PUBLISHED and may_take_down(user, takedown_grant(user, post), post):
        actions.append("unpublish")
    if can_reverse(user, reviewer_assignment(user, post, DECISION_PERMISSION["reverse"]), post):
        actions.append("reverse")
    if post.status != PostStatus.DRAFT and on_post(URGENT):
        actions.append("set_urgent")
    if post.citizen_id and on_post(BLOCK_CITIZEN):
        if citizen_blocked is None:
            from apps.citizens.services import active_block  # citizens builds on this module

            citizen_blocked = active_block(post.citizen) is not None
        actions.append("unblock_citizen" if citizen_blocked else "block_citizen")
        actions.append("discard_citizen_posts")
    return actions


# --- Helpers -------------------------------------------------------------------------------------


def _log(
    post: Post, action: str, from_status: str, to_status: str, actor, role: str, comment: str = "", via: str = ""
) -> None:
    """role: the key of the group the actor acted under (CITIZEN_ROLE for a citizen's phone)."""
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
        via=via or ("system" if actor is None else "app"),
        data={
            "from": from_status,
            "to": to_status,
            **({"comment": comment} if comment else {}),
            **({"citizen": str(post.citizen_id)} if post.citizen_id else {}),
        },
    )


def _after_commit(signal, **kwargs) -> None:
    transaction.on_commit(lambda: signal.send(sender=Post, **kwargs))


def _reload(post: Post) -> Post:
    return Post.objects.select_related("author", "citizen", "category", "decided_by").get(pk=post.pk)


def _targets_error(message) -> ApiError:
    return ApiError("validation_error", message, fields={"targets": [message]})


def clean_targets(refs, *, max_targets: int | None = None, allow_state: bool = True) -> list[AreaRef]:
    """The areas a post is for: checked, without duplicates or areas inside another chosen one.

    "Everyone" is the STATE. Every area must exist and be active, else 400 fields.targets. Children
    are never merged into their parent: what was sent is saved. One query per level for the active
    check and one per level for the parents, whatever the number of areas.
    """
    limit = max_targets or settings.POST_MAX_TARGETS
    refs = list(dict.fromkeys(refs or ()))  # duplicates out, order kept
    if not refs:
        raise _targets_error(_("Choose who will see the post."))
    if len(refs) > limit:
        raise _targets_error(_("Choose at most %(n)d areas, or a wider area.") % {"n": limit})
    if not allow_state and any(ref.level == AreaLevel.STATE for ref in refs):
        raise _targets_error(_("Choose districts, mandals or villages; posting to everyone is not allowed."))
    by_level: dict[str, set[int]] = {}
    for ref in refs:
        by_level.setdefault(ref.level, set()).add(ref.id)
    active = {
        AreaRef(level, area_id)
        for level, ids in by_level.items()
        for area_id in MODEL_BY_LEVEL[level].objects.active().filter(id__in=ids).values_list("id", flat=True)
    }
    missing = [ref for ref in refs if ref not in active]
    if missing:
        names = ", ".join(f"{ref.level} {ref.id}" for ref in missing[:5])
        raise _targets_error(_("No such active area: %(areas)s.") % {"areas": names})
    chosen = set(refs)
    parents = ancestries(refs)
    return [
        ref for ref in refs
        if not any(AreaRef(level, area_id) in chosen for level, area_id in parents[ref].items() if level != ref.level)
    ]


def _set_targets(post: Post, refs: list[AreaRef]) -> None:
    """Replace the post's areas with one bulk insert; the first is also kept on the post itself."""
    PostTarget.objects.filter(post=post).delete()
    PostTarget.objects.bulk_create(PostTarget(post=post, area_level=ref.level, area_id=ref.id) for ref in refs)


def _posting_or_raise(user, targets: list[AreaRef] | AreaRef | None = None, perm: str = CREATE) -> Grant:
    assignment = posting_assignment(user, targets, perm)
    if assignment is None:
        raise permission_denied(_("Your role cannot post."))
    return assignment


def _update(queryset, new_status: str, values: dict) -> int:
    """The conditional UPDATE of a status move; one that publishes also gives the post its share code."""
    if new_status == PostStatus.PUBLISHED:
        return publish_with_share_code(queryset, **values)
    return queryset.update(**values)


def publishes_at_once(user, targets: list[AreaRef]) -> bool:
    """Does an official's post for these areas go live without review? (BVTGC-26, Admin's decision of
    2026-11-25, replacing the 2026-10-28 rule.)

    Only when ADMIN_POSTS_AUTO_PUBLISH is on and the author may publish in **every** one of the areas:
    an admin's post inside their own area goes live at once; one area outside it, or Everyone (STATE),
    and the whole post goes to review by the admins of those areas (Everyone: the Super Admin). The
    Super Admin publishes anywhere, so nothing changes for them. Panchayat members never publish.
    """
    if not settings.ADMIN_POSTS_AUTO_PUBLISH or not targets:
        return False
    authz.prime(user, targets)
    return all(authz.can(user, PUBLISH, ref) for ref in targets)


def check_targets(user, refs) -> dict:
    """POST /posts/targets/check/: what sending a post for these areas would do, before it is sent
    (BVTGC-26, for the app's "this post will go to review" note, BVTGC-104). The same checks as a new
    post: the areas are cleaned (400 as on create) and the user must be allowed to post (403)."""
    targets = clean_targets(refs)
    _posting_or_raise(user, targets)
    return {"publishes_at_once": publishes_at_once(user, targets)}


def _move_on_submit(post: Post, user, assignment: Grant, *, resubmit: bool) -> Post:
    """DRAFT or RECHECK onward: published at once when the author may publish in every one of the
    post's areas (publishes_at_once), else reviewed."""
    now = timezone.now()
    from_status = post.status
    auto = PUBLISH in assignment.permissions and publishes_at_once(user, post.target_refs)
    if auto:
        changed = publish_with_share_code(
            Post.objects.filter(pk=post.pk, status__in=EDITABLE),
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
    #: One area (older clients); `targets` wins when both are given.
    target: AreaRef | None = None
    #: {"kind", "start_date", "end_date"} with date objects, or None (owner decision Q7).
    event: dict | None = None
    is_urgent: bool = False
    #: Every area the post is for: STATE alone is "Everyone" (owner decision of 2026-10-28).
    targets: list[AreaRef] | None = None

    def areas(self) -> list[AreaRef]:
        return list(self.targets) if self.targets else ([self.target] if self.target else [])


def _event_values(event: dict | None) -> dict:
    if not event:
        return {"event_kind": "", "event_start": None, "event_end": None}
    return {
        "event_kind": event["kind"],
        "event_start": event["start_date"],
        "event_end": event.get("end_date"),
    }


def _language_of(title: str, body: str, given: str) -> str:
    """What the text is written in, whatever the app sent: the translation's direction depends on it."""
    return detect_language(f"{title}\n{body}", given)


def _targets_data(targets: list[AreaRef]) -> dict:
    """The areas for the audit entry: how many, and the first few (a post may have 200)."""
    return {"targets": [[ref.level, ref.id] for ref in targets[:20]], "target_count": len(targets)}


def _check_urgent_allowed(assignment: Grant, wants_urgent: bool) -> None:
    """Only groups with posts.mark_urgent mark their own posts urgent (owner decision Q19)."""
    if wants_urgent and URGENT not in assignment.permissions:
        raise permission_denied(_("Only admins can mark a post as urgent."))


def earlier_post(client_ref, **owner) -> Post | None:
    """The post this author (`author=`) or phone (`citizen=`) already sent with this client_ref, or None.

    A repeat after a lost answer (MOB-ERR-004): the caller returns it instead of creating a second
    post, before any limit, block or media check. The post comes back with `repeated = True`.
    """
    if client_ref is None:
        return None
    post = Post.objects.filter(client_ref=client_ref, **owner).first()
    if post is not None:
        post.repeated = True
    return post


def create_post(user, data: PostInput, *, submit: bool = True, client_ref=None) -> Post:
    """An official's post for any areas. Admins' posts publish at once whatever the areas: that
    depends on the poster's role, not on where the post goes (owner decision of 2026-10-28).

    `client_ref`: the app's id for this create. The same id again from the same author returns the
    first post (`repeated`), with nothing created, audited or pushed again.
    """
    earlier = earlier_post(client_ref, author=user)
    if earlier is not None:
        return earlier
    _posting_or_raise(user)  # a role that cannot post hears that before any area check
    targets = clean_targets(data.areas())
    assignment = _posting_or_raise(user, targets)
    _check_urgent_allowed(assignment, data.is_urgent)
    sender = _posting_or_raise(user, targets, SUBMIT) if submit else None
    try:
        return _create_official_post(user, data, targets, assignment, sender, client_ref)
    except IntegrityError:
        # The same repeat arrived twice at the same moment: the first one's post is the answer.
        earlier = earlier_post(client_ref, author=user)
        if earlier is None:
            raise
        return earlier


def _create_official_post(user, data: PostInput, targets, assignment: Grant, sender: Grant | None, client_ref) -> Post:
    submit = sender is not None
    with transaction.atomic():
        post = Post.objects.create(
            author=user,
            client_ref=client_ref,
            posted_as_role=assignment.key,
            posted_as_level=assignment.area.level,
            posted_as_area_id=assignment.area.id,
            category=data.category,
            language=_language_of(data.title, data.body, data.language),
            title=data.title,
            body=data.body,
            target_level=targets[0].level,
            target_area_id=targets[0].id,
            status=PostStatus.DRAFT,
            is_urgent=data.is_urgent,
            **_event_values(data.event),
        )
        _set_targets(post, targets)
        record("post.created", actor=user, actor_role=assignment.key, target=post, target_label=post.title,
               area=post.target, data=_targets_data(targets))
        if submit:
            post = _move_on_submit(post, user, sender, resubmit=False)
    return _reload(post)


def create_citizen_post(device, data: PostInput, attach=None, client_ref=None, before=None) -> Post:
    """A citizen's post: straight to PENDING for every reviewer of its village (contract section 15).

    `device` is the citizens.CitizenDevice that wrote it; the caller has checked its token,
    block and daily limit. `attach(post)` stores the photos inside the same transaction, so
    reviewers are alerted (post_submitted, after commit) only once the photos exist.
    The post is for the phone's village only, and is posted from it (owner's decision of
    2026-11-19: citizens no longer choose areas, so they cannot post into other villages). Any areas
    in `data` are ignored. `client_ref` is saved on the post; the caller (citizens.services) looks
    for a repeat first. `before()` runs inside the transaction before the post is written (the caller
    locks the phone there, ahead of the insert's foreign-key lock, so posts sent at once do not deadlock).
    """
    home = AreaRef(AreaLevel.VILLAGE, device.village_id)
    targets = clean_targets([home])
    now = timezone.now()
    with transaction.atomic():
        if before is not None:
            before()
        post = Post.objects.create(
            citizen=device,
            client_ref=client_ref,
            posted_as_role=CITIZEN_ROLE,
            posted_as_level=home.level,
            posted_as_area_id=home.id,
            category=data.category,
            language=_language_of(data.title, data.body, data.language),
            title=data.title,
            body=data.body,
            target_level=targets[0].level,
            target_area_id=targets[0].id,
            status=PostStatus.PENDING,
            submitted_at=now,
        )
        _set_targets(post, targets)
        record(
            "post.created", target=post, target_label=post.title, area=post.target, via="citizen",
            data={"citizen": str(device.pk), **_targets_data(targets)},
        )
        if attach is not None:
            attach(post)
        _log(post, ReviewAction.Action.SUBMIT, PostStatus.DRAFT, PostStatus.PENDING, None, CITIZEN_ROLE, via="citizen")
        _after_commit(signals.post_submitted, post=post)
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
    if {"title", "body", "language"} & set(values):
        values["language"] = _language_of(
            values.get("title", post.title), values.get("body", post.body), values.get("language", post.language)
        )
    assignment = None
    targets = None
    if changes.get("targets") or changes.get("target"):
        targets = clean_targets(changes.get("targets") or [changes["target"]])
        assignment = _posting_or_raise(user, targets)
        values.update(
            target_level=targets[0].level,
            target_area_id=targets[0].id,
            posted_as_role=assignment.key,
            posted_as_level=assignment.area.level,
            posted_as_area_id=assignment.area.id,
        )
    if "event" in changes:
        values.update(_event_values(changes["event"]))
    if "is_urgent" in changes:
        if changes["is_urgent"]:
            _check_urgent_allowed(assignment or _posting_or_raise(user, post.target_refs), True)
        values["is_urgent"] = changes["is_urgent"]
    if values:
        values["updated_at"] = timezone.now()
        with transaction.atomic():
            if not Post.objects.filter(pk=post.pk, status__in=EDITABLE).update(**values):
                raise invalid_transition(_reload(post), "edited")
            post = _reload(post)
            if targets is not None:
                _set_targets(post, targets)
            record("post.updated", actor=user, target=post, target_label=post.title, area=post.target,
                   data=_targets_data(targets) if targets is not None else {})
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
        for field in (item.image, item.medium, item.thumbnail, item.video):  # the video too (BE-DB-015)
            if field:
                field.storage.delete(field.name)


def submit_post(user, post: Post) -> Post:
    _own_editable(user, post, "submitted")
    assignment = _posting_or_raise(user, post.target_refs, SUBMIT)
    with transaction.atomic():
        return _move_on_submit(post, user, assignment, resubmit=post.status == PostStatus.RECHECK)


# --- Review -----------------------------------------------------------------------------------------------

DECISIONS = {
    # action: (from status, to status, ReviewAction, comment required)
    "publish": (PostStatus.PENDING, PostStatus.PUBLISHED, ReviewAction.Action.PUBLISH, False),
    "recheck": (PostStatus.PENDING, PostStatus.RECHECK, ReviewAction.Action.RECHECK, True),
    "discard": (PostStatus.PENDING, PostStatus.DISCARDED, ReviewAction.Action.DISCARD, True),
    "unpublish": (PostStatus.PUBLISHED, PostStatus.UNPUBLISHED, ReviewAction.Action.UNPUBLISH, True),
    "block": (PostStatus.PENDING, PostStatus.BLOCKED, ReviewAction.Action.BLOCK, True),
}
VERBS = {
    "publish": "published", "recheck": "sent for recheck", "discard": "discarded", "unpublish": "taken down",
    "block": "blocked",
}


def review(user, post: Post, action: str, comment: str = "", to_status: str | None = None) -> Post:
    comment = (comment or "").strip()
    perm = DECISION_PERMISSION.get(action, REVIEW)
    reviewer = takedown_grant(user, post) if action == "unpublish" else reviewer_assignment(user, post, perm)
    if reviewer is None:
        if authz.can(user, perm):
            raise out_of_area(_("This post is outside your area."))
        raise permission_denied(_("Your role cannot review posts."))

    if action == "reverse":
        return _reverse(user, reviewer, post, comment, to_status)
    if action not in DECISIONS:
        raise ApiError("validation_error", _("Unknown action."), fields={"action": [_("Unknown action.")]})

    from_status, new_status, logged_action, needs_comment = DECISIONS[action]
    if new_status == PostStatus.RECHECK and post.citizen_id:
        raise citizen_cannot_recheck(post)
    if new_status == PostStatus.BLOCKED and not post.citizen_id:
        raise ApiError("validation_error", _("This is not a citizen post."), fields={"post": [_("Not a citizen post.")]})
    if needs_comment and not comment:
        raise comment_required()
    if from_status == PostStatus.PENDING and post.author_id == user.id:
        raise permission_denied(_("You cannot review your own post."))
    if action == "unpublish" and not may_take_down(user, reviewer, post):
        raise permission_denied(_("You cannot take down this post."))

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
        changed = _update(Post.objects.filter(pk=post.pk, status=from_status), new_status, values)
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
    if to_status == PostStatus.RECHECK and post.citizen_id:
        raise citizen_cannot_recheck(post)
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
        changed = _update(
            Post.objects.filter(pk=post.pk, status=post.status, decided_at=post.decided_at), to_status, values
        )
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


def lowest_level(refs: list[AreaRef]) -> str:
    """The deepest level among a post's areas: VILLAGE when any is a village."""
    return max((ref.level for ref in refs), key=lambda level: DEPTH[level])


def _can_climb() -> Q:
    """Posts with a level left above them: escalated below the state, or not escalated yet with an area
    below the state (older posts without target rows: their first area). Posts for Everyone and posts at
    the state already stay out of the batch, so they can never fill it and stop every other post (BE-DB-201)."""
    below_state = [AreaLevel.VILLAGE, AreaLevel.MANDAL, AreaLevel.DISTRICT]
    targets = PostTarget.objects.filter(post=OuterRef("pk"))
    not_escalated = Q(escalation_level="") & (
        Exists(targets.filter(area_level__in=below_state))
        | (~Exists(targets) & Q(target_level__in=below_state))
    )
    return Q(escalation_level__in=below_state) | not_escalated


def escalate_overdue(now=None) -> int:
    """Flag PENDING posts nobody decided within REVIEW_ESCALATION_HOURS.

    Each run moves an overdue post one level up: a village post first alerts
    the mandal's admins, REVIEW_ESCALATION_HOURS later the district's, then
    the state's. Lower reviewers keep the post in their queue. A post for
    several areas climbs from its lowest one; the alert reaches the admins
    above each of its areas (notifications.services.alert_escalation).

    At most ESCALATION_BATCH_SIZE posts per run, oldest submission first; the rest are taken by the next
    run. The alerts go out as jobs of their own (notifications.receivers), so a run stays short.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(hours=int(runtime("REVIEW_ESCALATION_HOURS")))
    candidates = (
        Post.objects.filter(status=PostStatus.PENDING)
        .filter(Q(escalated_at__isnull=True, submitted_at__lte=cutoff) | Q(escalated_at__lte=cutoff))
        .filter(_can_climb())
        .order_by("submitted_at", "pk")
        .prefetch_related("targets")[: settings.ESCALATION_BATCH_SIZE]
    )
    count = 0
    for post in candidates:
        current = post.escalation_level or lowest_level(post.target_refs)
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
