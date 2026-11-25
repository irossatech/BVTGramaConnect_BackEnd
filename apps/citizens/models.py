"""The phones citizens post from (docs/api-contract.md, section 15), and citizens' optional wallet
accounts (section 21, BVTGC-106).

Posting needs no sign-in (decision of 2026-10-27). Each app install gets its own
posting token after a Google Play Integrity check; the server keeps only the
token's SHA-256 hash, so a copy of the database cannot be used to post.
Posts point at the device that wrote them (posts.Post.citizen).

A citizen may also open a wallet account (CitizenAccount), used only for the wallet
of points and referrals. The account and the posting token are separate: a phone may
have both, and neither needs the other. The account's full mobile number is never
stored, only its keyed hash and last 4 digits; its email is kept in full, because
codes are sent to it. Design: docs/features/citizen-wallet/technical-implementation.md, section 4.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

LANGUAGES = [("te", "తెలుగు"), ("en", "English")]


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


class CitizenAccount(models.Model):
    """A citizen's wallet account (BVTGC-106): the Django user behind it, and what only citizens have.

    Login is by `email_key` or `mobile_hash` plus the user's password. Both are unique while set and
    null after deletion, so a deleted account frees its email and number. Deleting an account keeps
    the row with no personal details (owner's decision of 2026-11-25, Q8); the `deleted_*` fingerprints
    stop repeat welcome bonuses for a time (requirements Q9). Written only by the account services.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, primary_key=True, related_name="citizen_account"
    )
    #: The village chosen in the app; also the area of the account's Citizen role assignment.
    village_id = models.PositiveIntegerField()
    age_confirmed_at = models.DateTimeField()
    consent_version = models.CharField(max_length=20)
    consent_at = models.DateTimeField()
    #: Where codes and notices go, in lower case. Not User.email: officials' emails are contact only
    #: and not unique, and an official's own citizen account may use the same one. Empty after deletion.
    email = models.EmailField(max_length=254, blank=True)
    #: The email's canonical form, one of the two login names (Gmail dots and "+" parts removed).
    email_key = models.CharField(max_length=254, unique=True, null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    #: Keyed hash of the 10-digit number (citizens.services._keyed_hash, PHONE_HASH_KEY), the other
    #: login name. Null after deletion, or after the Super Admin removed it from this account (D16).
    mobile_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    mobile_last4 = models.CharField(max_length=4, blank=True)
    #: Set when the later stage confirms the number with a WhatsApp or SMS code (D17); a change clears it.
    mobile_verified_at = models.DateTimeField(null=True, blank=True)
    last_active_at = models.DateTimeField(null=True, blank=True)
    referral_code = models.CharField(max_length=8, unique=True)
    #: Keyed hash of the Android ID of the phone used at sign-up: the welcome bonus limit per phone (D10).
    signup_android_id_hash = models.CharField(max_length=64, blank=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_mobile_hash = models.CharField(max_length=64, blank=True, db_index=True)
    deleted_email_key = models.CharField(max_length=254, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"citizen account {str(self.user_id)[:8]}"


class PendingSignup(models.Model):
    """A sign-up waiting for its email code (FR-10). An unfinished form, not a record: housekeeping
    deletes it after `expires_at` (24 hours), and finishing the sign-up deletes it at once.
    The password is kept only as its hash (make_password)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    village_id = models.PositiveIntegerField()
    language = models.CharField(max_length=2, choices=LANGUAGES, default="te")
    mobile_hash = models.CharField(max_length=64, db_index=True)
    mobile_last4 = models.CharField(max_length=4)
    email = models.EmailField(max_length=254)
    email_key = models.CharField(max_length=254, db_index=True)
    password = models.CharField(max_length=128)
    age_confirmed_at = models.DateTimeField()
    consent_version = models.CharField(max_length=20)
    #: As typed at sign-up; checked again when the account is created.
    referral_code = models.CharField(max_length=8, blank=True)
    android_id_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self):
        return f"pending sign-up {str(self.id)[:8]}"


class VerificationCode(models.Model):
    """One code sent to a citizen (FR-12). Never holds the code itself: `code_hash` is an HMAC of the
    code with the row id. A sign-up code belongs to a PendingSignup; every other code to an account."""

    class Purpose(models.TextChoices):
        SIGNUP = "SIGNUP"
        RESET_PASSWORD = "RESET_PASSWORD"
        CHANGE_EMAIL = "CHANGE_EMAIL"
        DELETE = "DELETE"

    class Channel(models.TextChoices):
        EMAIL = "EMAIL"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)
    pending_signup = models.ForeignKey(
        PendingSignup, on_delete=models.CASCADE, null=True, blank=True, related_name="codes"
    )
    account = models.ForeignKey(CitizenAccount, on_delete=models.CASCADE, null=True, blank=True, related_name="codes")
    #: The canonical address the code went to, for the limit per address.
    email_key = models.CharField(max_length=254)
    #: CHANGE_EMAIL only: the address being proven. Cleared once the code is used or expired.
    new_email = models.EmailField(max_length=254, blank=True)
    new_email_key = models.CharField(max_length=254, blank=True)
    android_id_hash = models.CharField(max_length=64, blank=True)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["email_key", "created_at"]),
            models.Index(fields=["android_id_hash", "created_at"]),
            models.Index(fields=["account", "purpose", "created_at"]),
            models.Index(fields=["pending_signup", "created_at"]),
        ]
        constraints = [
            # Exactly one owner: a pending sign-up, or an account.
            models.CheckConstraint(
                condition=(
                    Q(pending_signup__isnull=False, account__isnull=True)
                    | Q(pending_signup__isnull=True, account__isnull=False)
                ),
                name="citizens_code_one_owner",
            ),
        ]

    def __str__(self):
        return f"{self.purpose.lower()} code {str(self.id)[:8]}"


class CitizenActiveDay(models.Model):
    """One row per India date on which the app opened with the account: the referral active rule.
    A counter, not a record: housekeeping deletes rows older than 60 days."""

    account = models.ForeignKey(CitizenAccount, on_delete=models.CASCADE, related_name="active_days")
    day = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "day"], name="citizens_active_day_once")]

    def __str__(self):
        return f"active day {self.day}"
