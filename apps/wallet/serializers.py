"""The citizen's wallet history (docs/api-contract.md, section 21, WalletEntry; BVTGC-126)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import WalletEntry
from .services import MAX_ADJUSTMENT

#: Words for each reason, in the request's language. An adjustment or a Super Admin's rejection shows the
#: text they typed instead.
REASONS = {
    "same_phone": _("No reward: both accounts signed up on the same phone."),
    "welcome_limit": _("No welcome bonus: this phone or number already had one."),
    "no_device": _("No welcome bonus: the phone could not be recognised."),
    "not_active": _("Not confirmed: the new citizen did not use the app on enough days."),
    "monthly_limit": _("Not confirmed: the monthly limit of referrals was reached."),
    "blocked": _("Not confirmed: an account was blocked."),
    "deleted": _("Not confirmed: an account was deleted."),
    "rejected": _("Not confirmed by the Super Admin."),
}


class WalletEntrySerializer(serializers.ModelSerializer):
    reason = serializers.SerializerMethodField()
    release_at = serializers.SerializerMethodField()

    class Meta:
        model = WalletEntry
        fields = ["id", "points", "kind", "status", "reason_key", "reason", "created_at", "decided_at", "release_at"]

    def get_reason(self, entry) -> str | None:
        if entry.reason:
            return entry.reason
        words = REASONS.get(entry.reason_key)
        return str(words) if words else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_release_at(self, entry):
        """When a held entry is decided: its referral's end of hold (the hourly job decides within the hour after).
        Null when the entry is not on hold, or has no referral. Each row its own date (BVTGC-157): the summary's
        next_release_at is only the earliest of them. Callers select_related("referral")."""
        if entry.status != WalletEntry.Status.HELD or entry.referral_id is None:
            return None
        return serializers.DateTimeField().to_representation(entry.referral.confirm_after)


# --- The Super Admin (BVTGC-128) ---------------------------------------------------------------------------

def person(user) -> dict | None:
    return {"id": user.pk, "name": user.name} if user is not None else None


def party(account) -> dict:
    """One side of a referral: never the full number or email."""
    return {
        "id": account.pk, "name": account.user.name, "mobile_last4": account.mobile_last4,
        "referral_code": account.referral_code,
    }


class AdminWalletEntrySerializer(WalletEntrySerializer):
    """An entry in the Super Admin's view of one wallet: who made an adjustment, and its referral."""

    created_by = serializers.SerializerMethodField()
    referral_id = serializers.UUIDField(allow_null=True, read_only=True)

    class Meta(WalletEntrySerializer.Meta):
        fields = [*WalletEntrySerializer.Meta.fields, "created_by", "referral_id"]

    def get_created_by(self, entry) -> dict | None:
        return person(entry.created_by)


class ReferralRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    referrer = serializers.DictField()
    referee = serializers.DictField()
    status = serializers.CharField()
    reason_key = serializers.CharField(allow_null=True)
    comment = serializers.CharField()
    pending_at = serializers.DateTimeField()
    confirm_after = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    decided_by = serializers.DictField(allow_null=True)
    allowed_actions = serializers.ListField(child=serializers.CharField())

    def to_representation(self, referral) -> dict:
        pending = referral.status == referral.Status.PENDING
        return {
            "id": referral.id,
            "referrer": party(referral.referrer),
            "referee": party(referral.referee),
            "status": referral.status,
            "reason_key": referral.reason_key or None,
            "comment": referral.comment,
            "pending_at": referral.pending_at,
            "confirm_after": referral.confirm_after,
            "decided_at": referral.decided_at,
            "decided_by": person(referral.decided_by),
            "allowed_actions": ["confirm", "reject"] if pending else [],
        }


class CommentSerializer(serializers.Serializer):
    """A rejection's comment is shown in both citizens' histories."""

    comment = serializers.CharField(max_length=500)


class AdjustSerializer(serializers.Serializer):
    points = serializers.IntegerField(min_value=-MAX_ADJUSTMENT, max_value=MAX_ADJUSTMENT)
    reason = serializers.CharField(max_length=500, help_text="Shown in the citizen's wallet history")

    def validate_points(self, value):
        if value == 0:
            raise serializers.ValidationError(_("Give a number of points other than 0."))
        return value


class AdminEntryRowSerializer(AdminWalletEntrySerializer):
    """A row of the Super Admin's list of every entry (BVTGC-151): the entry and whose wallet it is."""

    citizen = serializers.SerializerMethodField()

    class Meta(AdminWalletEntrySerializer.Meta):
        fields = [*AdminWalletEntrySerializer.Meta.fields, "citizen"]

    def get_citizen(self, entry) -> dict:
        account = entry.wallet.account
        return {"id": account.pk, "name": account.user.name, "mobile_last4": account.mobile_last4}
