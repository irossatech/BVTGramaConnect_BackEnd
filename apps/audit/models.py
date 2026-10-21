"""Append-only audit log. Rows are written by audit.services.record() and never changed."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AuditType(models.TextChoices):
    POSTS = "posts", _("Posts")
    USERS = "users", _("Users")
    AREAS = "areas", _("Areas")
    LOGINS = "logins", _("Logins")
    SETTINGS = "settings", _("Settings")
    SYSTEM = "system", _("System")


#: Action prefix -> type shown in the console's audit tabs.
TYPE_BY_PREFIX = {
    "post": AuditType.POSTS,
    "user": AuditType.USERS,
    "area": AuditType.AREAS,
    "auth": AuditType.LOGINS,
    "setting": AuditType.SETTINGS,
    "category": AuditType.SETTINGS,
    "template": AuditType.SETTINGS,
    "report": AuditType.SETTINGS,
    "email": AuditType.SYSTEM,
    "push": AuditType.SYSTEM,
}


def type_for_action(action: str) -> str:
    return TYPE_BY_PREFIX.get(action.split(".", 1)[0], AuditType.SYSTEM)


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    actor_name = models.CharField(max_length=150, blank=True, help_text=_("Name at the time; 'System' when empty."))
    actor_role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    type = models.CharField(max_length=10, choices=AuditType.choices, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_label = models.CharField(max_length=255, blank=True)
    area_level = models.CharField(max_length=10, blank=True)
    area_id = models.PositiveIntegerField(null=True, blank=True)
    via = models.CharField(max_length=10, default="app")
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")
        indexes = [
            models.Index(fields=["type", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["area_level", "area_id"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_name or 'System'} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Audit log entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit log entries cannot be deleted.")
