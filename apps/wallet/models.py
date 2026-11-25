"""Citizens' wallets of points and their referrals (BVTGC-106, docs/api-contract.md, section 21).

Points are collect-only until Bills & Recharges go live; they never become cash and cannot be
transferred (requirements D4). The entries are the truth: Wallet's two numbers are a cache kept in
step in the same transaction, and a daily job checks them. Entries are never edited or deleted, only
reversed by a new entry. Written only by apps.wallet.services.
Design: docs/features/citizen-wallet/technical-implementation.md, section 4.2.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Wallet(models.Model):
    """One per citizen account. PositiveIntegerField keeps both numbers at zero or above on every
    database, so a cache update below zero fails instead of saving."""

    account = models.OneToOneField(
        "citizens.CitizenAccount", on_delete=models.PROTECT, primary_key=True, related_name="wallet"
    )
    confirmed_points = models.PositiveIntegerField(default=0)
    held_points = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"wallet {str(self.account_id)[:8]}"


class Referral(models.Model):
    """A friend who signed up with a citizen's code (FR-17 to FR-25). PENDING while its points are
    held; the hourly job, or a Super Admin, confirms or rejects it with a conditional UPDATE on status."""

    class Status(models.TextChoices):
        PENDING = "PENDING"
        CONFIRMED = "CONFIRMED"
        REJECTED = "REJECTED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referrer = models.ForeignKey("citizens.CitizenAccount", on_delete=models.PROTECT, related_name="referrals_made")
    #: A citizen is referred at most once (FR-22).
    referee = models.OneToOneField("citizens.CitizenAccount", on_delete=models.PROTECT, related_name="referral")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    #: Why it was rejected: blocked, deleted, not_active, monthly_limit or rejected (contract section 21).
    reason_key = models.CharField(max_length=30, blank=True)
    #: The Super Admin's comment when they confirmed or rejected it.
    comment = models.CharField(max_length=500, blank=True)
    pending_at = models.DateTimeField(default=timezone.now)
    confirm_after = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["status", "confirm_after"]),
            models.Index(fields=["referrer", "status", "decided_at"]),
        ]
        constraints = [
            models.CheckConstraint(condition=~Q(referrer=F("referee")), name="wallet_referral_not_self"),
        ]

    def __str__(self):
        return f"referral {str(self.id)[:8]}"


class WalletEntry(models.Model):
    """One change of points, append-only. `idempotency_key` is unique per wallet, so a retried job or
    request can never pay twice (for example `ref:<referral id>:referrer`)."""

    class Kind(models.TextChoices):
        REFERRAL_REWARD = "REFERRAL_REWARD"
        WELCOME_BONUS = "WELCOME_BONUS"
        ADJUSTMENT = "ADJUSTMENT"

    class Status(models.TextChoices):
        HELD = "HELD"
        CONFIRMED = "CONFIRMED"
        CANCELLED = "CANCELLED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    points = models.IntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices)
    referral = models.ForeignKey(Referral, on_delete=models.PROTECT, null=True, blank=True, related_name="entries")
    #: Explains the entry in the history: same_phone, welcome_limit, no_device, not_active, ...
    reason_key = models.CharField(max_length=30, blank=True)
    #: An adjustment's or a rejection's text, as the Super Admin wrote it.
    reason = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    idempotency_key = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["wallet", "-created_at"])]
        constraints = [
            models.CheckConstraint(condition=~Q(points=0), name="wallet_entry_points_not_zero"),
            # Only a Super Admin's adjustment may take points away.
            models.CheckConstraint(
                condition=Q(points__gt=0) | Q(kind="ADJUSTMENT"), name="wallet_entry_negative_only_adjustment"
            ),
            models.UniqueConstraint(fields=["wallet", "idempotency_key"], name="wallet_entry_idempotency"),
        ]

    def __str__(self):
        return f"{self.kind.lower()} {self.points:+d}"
