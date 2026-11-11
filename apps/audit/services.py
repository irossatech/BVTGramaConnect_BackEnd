"""Write audit entries. The only way rows get into AuditLog.

    record("post.published", actor=user, target=post, target_label=post.title,
           area=AreaRef("VILLAGE", 12), via="app", data={"comment": "…"})

Call it inside the same transaction as the change it describes.

Every text inside `data` is cut at AUDIT_TEXT_MAX_CHARS (2,000 by default), with "…" at the end.
The API already limits comments and notes to 500 characters, so this only catches text that
something lets through by mistake: the audit log is never cleaned, so it must not fill up.
"""

from __future__ import annotations

from django.conf import settings

from .models import AuditLog, type_for_action

#: Put at the end of a text that was cut.
CUT_MARK = "…"


def _cap(value, limit: int):
    """`value` with every text in it, at any depth, at most `limit` characters long."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - len(CUT_MARK)] + CUT_MARK
    if isinstance(value, dict):
        return {key: _cap(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cap(item, limit) for item in value]
    return value


def record(
    action: str,
    *,
    actor=None,
    target=None,
    target_label: str = "",
    area=None,
    via: str = "app",
    data: dict | None = None,
    actor_role: str = "",
) -> AuditLog:
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None
    if actor is not None and not actor_role:
        # The keys of the actor's groups, for display, such as VILLAGE_ADMIN.
        from apps.accounts import authz

        keys = sorted({grant.key for grant in authz.grants(actor, pending_change_ok=True)})
        actor_role = keys[0] if len(keys) == 1 else ",".join(keys)[:20]
    target_type = target_id = ""
    if target is not None:
        target_type = target._meta.label_lower
        target_id = str(target.pk)
    return AuditLog.objects.create(
        actor=actor,
        actor_name=(getattr(actor, "name", "") or "")[:150] if actor else "",
        actor_role=actor_role[:20],
        action=action,
        type=type_for_action(action),
        target_type=target_type,
        target_id=target_id,
        target_label=(target_label or "")[:255],
        area_level=area.level if area is not None else "",
        area_id=area.id if area is not None else None,
        via=via,
        data=_cap(data or {}, settings.AUDIT_TEXT_MAX_CHARS),
    )
