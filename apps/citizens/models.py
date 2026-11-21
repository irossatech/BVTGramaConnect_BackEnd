"""The phones citizens post from. docs/api-contract.md, section 15.

Citizens never sign in (decision of 2026-10-27). Each app install gets its own
posting token after a Google Play Integrity check; the server keeps only the
token's SHA-256 hash, so a copy of the database cannot be used to post.
Posts point at the device that wrote them (posts.Post.citizen).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CitizenDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    platform = models.CharField(max_length=10)
    app_version = models.CharField(max_length=20, blank=True)
    #: The village this phone posts to; the app changes it with PATCH /citizen/me/.
    village_id = models.PositiveIntegerField()
    #: Keyed hash of the IP address at registration: counts registrations per IP without storing it.
    registered_ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    #: Keyed hash of Android's app-scoped device ID (Settings.Secure.ANDROID_ID). It stays the same
    #: when the app is reinstalled, so a block reaches the phone's next token too. Empty on iOS.
    android_id_hash = models.CharField(max_length=64, blank=True, db_index=True)
    #: Where to push "your post was published / discarded" (CP4); empty if the phone sent none.
    fcm_token = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=2, choices=[("te", "తెలుగు"), ("en", "English")], default="te")
    #: The one-time challenge used at registration; unique, so a nonce cannot be used twice.
    nonce = models.CharField(max_length=128, unique=True)
    #: Whether Google's verdict was checked (CITIZEN_INTEGRITY_REQUIRED) when the token was issued.
    integrity_checked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_seen = models.DateTimeField(auto_now=True)
    #: A block is temporary (lead's decision of 2026-10-27): it ends by itself at blocked_until.
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    block_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["registered_ip_hash", "created_at"])]

    def __str__(self):
        return f"citizen phone {str(self.id)[:8]}"

    @property
    def blocked(self) -> bool:
        """This row's own block is running. Use services.active_block() to include the same phone's other installs."""
        return self.blocked_at is not None and (self.blocked_until is None or self.blocked_until > timezone.now())


class CitizenBlock(models.Model):
    """One block of one phone, kept for the record (owner's request of 2026-11-21).

    CitizenDevice's blocked_* fields say whether the phone is blocked now; an unblock clears them. This row
    keeps every block with how it ended: `ended_at` empty while it runs; set with `ended_by` when a Super
    Admin unblocked it or blocked it again; set without `ended_by` when its time was up (the
    citizens.tasks job). Written only by citizens.services.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: services.phone_key(): the hashed Android ID, or the install's id when it has none. Groups a phone's installs.
    phone = models.CharField(max_length=64, db_index=True)
    #: The install whose post the block was made from.
    device = models.ForeignKey(CitizenDevice, on_delete=models.PROTECT, related_name="blocks")
    village_id = models.PositiveIntegerField()
    blocked_at = models.DateTimeField(db_index=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    blocked_by_role = models.CharField(max_length=20, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ended_by_role = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-blocked_at"]
        indexes = [models.Index(fields=["phone", "blocked_at"]), models.Index(fields=["ended_at", "blocked_until"])]

    def __str__(self):
        return f"block of citizen phone {self.phone[:8]}"
