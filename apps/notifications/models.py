"""Devices for push, the officials' inbox, and editable notification texts."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Platform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    WEB = "web", "Web"


class Device(models.Model):
    """One app install that can receive pushes.

    Citizens' devices have no user; they subscribe to area topics on the phone.
    Officials' devices are linked to the user so alerts can go to them directly.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fcm_token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    language = models.CharField(max_length=2, choices=[("te", "తెలుగు"), ("en", "English")], default="te")
    village_id = models.PositiveIntegerField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="devices"
    )
    app_version = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user"]), models.Index(fields=["village_id"])]

    def __str__(self):
        return f"{self.platform} device {str(self.id)[:8]}"


class NotificationKind(models.TextChoices):
    POST_PUBLISHED = "POST_PUBLISHED", _("Your post was published")
    POST_RECHECK = "POST_RECHECK", _("Your post needs changes")
    POST_DISCARDED = "POST_DISCARDED", _("Your post was discarded")
    POST_UNPUBLISHED = "POST_UNPUBLISHED", _("Your post was taken down")
    REVIEW_PENDING = "REVIEW_PENDING", _("New post to review")
    ESCALATION = "ESCALATION", _("Escalated post")
    ACCOUNT = "ACCOUNT", _("Account")


class Notification(models.Model):
    """One item in an official's in-app notification list."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=NotificationKind.choices)
    title = models.CharField(max_length=255)
    body = models.CharField(max_length=500, blank=True)
    post = models.ForeignKey("posts.Post", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    area_level = models.CharField(max_length=10, blank=True)
    area_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return self.title


class Channel(models.TextChoices):
    PUSH = "push", _("Push")


class Recipient(models.TextChoices):
    AUTHOR = "author", _("Author of the post")
    CITIZENS = "citizens", _("Citizens of the area")
    REVIEWERS = "reviewers", _("Reviewers of the area")
    HIGHER_ADMIN = "higher_admin", _("Admins one level up")
    CITIZEN = "citizen", _("The citizen who sent it")


class NotificationTemplate(models.Model):
    """Text of one kind of notification, in Telugu and English, with {variables}.

    A row with editable=False cannot be changed from the console.
    """

    key = models.SlugField(max_length=40, unique=True)
    name_te = models.CharField(max_length=80)
    name_en = models.CharField(max_length=80)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.CharField(max_length=20, choices=Recipient.choices)
    text_te = models.CharField(max_length=300)
    text_en = models.CharField(max_length=300)
    variables = models.JSONField(default=list)
    editable = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "key"]
        permissions = [("manage_templates", "Change notification texts")]

    def __str__(self):
        return self.key

    def text(self, language: str) -> str:
        return self.text_te if (language or "te").startswith("te") else self.text_en
