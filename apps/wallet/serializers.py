"""The citizen's wallet history (docs/api-contract.md, section 21, WalletEntry; BVTGC-126)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import WalletEntry

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

    class Meta:
        model = WalletEntry
        fields = ["id", "points", "kind", "status", "reason_key", "reason", "created_at", "decided_at"]

    def get_reason(self, entry) -> str | None:
        if entry.reason:
            return entry.reason
        words = REASONS.get(entry.reason_key)
        return str(words) if words else None
