"""Posts, their photos, their review history, categories and runtime settings.

Post.status changes only through posts.services. The target area is stored as
(target_level, target_area_id), like role assignments, so containment checks
use geography.areas.within_q(level_field="target_level", id_field="target_area_id").
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.accounts.roles import Role
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel


class Category(models.Model):
    key = models.SlugField(max_length=20, unique=True)
    name_te = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60)
    short_name_te = models.CharField(max_length=40, blank=True)
    short_name_en = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = _("categories")

    def __str__(self):
        return self.name_en

    def display_name(self, language=None, short=False):
        telugu = (language or get_language() or "te").startswith("te")
        if short:
            value = self.short_name_te if telugu else self.short_name_en
            if value:
                return value
        return self.name_te if telugu else self.name_en


class PostStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    PENDING = "PENDING", _("Pending")
    PUBLISHED = "PUBLISHED", _("Published")
    RECHECK = "RECHECK", _("Recheck")
    DISCARDED = "DISCARDED", _("Discarded")
    UNPUBLISHED = "UNPUBLISHED", _("Taken down")


class Post(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="posts")
    posted_as_role = models.CharField(max_length=20, choices=Role.choices)
    posted_as_level = models.CharField(max_length=10, choices=AreaLevel.choices)
    posted_as_area_id = models.PositiveIntegerField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="posts")
    language = models.CharField(max_length=2, choices=[("te", "తెలుగు"), ("en", "English")], default="te")
    title = models.CharField(max_length=150)
    body = models.TextField(max_length=5000)
    target_level = models.CharField(max_length=10, choices=AreaLevel.choices)
    target_area_id = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=PostStatus.choices, default=PostStatus.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_by_role = models.CharField(max_length=20, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.CharField(max_length=10, choices=AreaLevel.choices, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "target_level", "target_area_id", "published_at"]),
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["author", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def target(self) -> AreaRef:
        return AreaRef(self.target_level, self.target_area_id)

    @property
    def posted_as(self) -> AreaRef:
        return AreaRef(self.posted_as_level, self.posted_as_area_id)


def media_upload_path(instance, filename):
    return f"posts/{instance.post.created_at:%Y/%m}/{filename}"


class PostMedia(models.Model):
    """One photo, stored as WebP at most 1280 px on the long side, plus a 320 px thumbnail."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="media")
    image = models.FileField(upload_to=media_upload_path, max_length=200)
    thumbnail = models.FileField(upload_to=media_upload_path, max_length=200)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    bytes = models.PositiveIntegerField()
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]

    def delete(self, *args, **kwargs):
        image, thumbnail = self.image, self.thumbnail
        result = super().delete(*args, **kwargs)
        for field in (image, thumbnail):
            if field:
                field.storage.delete(field.name)
        return result


class ReviewAction(models.Model):
    class Action(models.TextChoices):
        SUBMIT = "SUBMIT"
        RESUBMIT = "RESUBMIT"
        PUBLISH = "PUBLISH"
        AUTO_PUBLISH = "AUTO_PUBLISH"
        RECHECK = "RECHECK"
        DISCARD = "DISCARD"
        UNPUBLISH = "UNPUBLISH"
        ESCALATE = "ESCALATE"
        REVERSE = "REVERSE"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="review_actions")
    action = models.CharField(max_length=14, choices=Action.choices)
    from_status = models.CharField(max_length=12, choices=PostStatus.choices, blank=True)
    to_status = models.CharField(max_length=12, choices=PostStatus.choices)
    comment = models.TextField(max_length=500, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    actor_role = models.CharField(max_length=20, blank=True, help_text=_("Empty means the system acted."))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["post", "created_at"])]


class SettingOverride(models.Model):
    """A runtime value that overrides the Django setting of the same name."""

    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key} = {self.value!r}"
