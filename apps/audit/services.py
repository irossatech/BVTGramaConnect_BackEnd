"""Write audit entries. The only way rows get into AuditLog.

    record("post.published", actor=user, target=post, target_label=post.title,
           area=AreaRef("VILLAGE", 12), via="app", data={"comment": "…"})

Call it inside the same transaction as the change it describes.
"""

from __future__ import annotations

from .models import AuditLog, type_for_action


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
        data=data or {},
    )
