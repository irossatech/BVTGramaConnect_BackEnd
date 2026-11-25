"""Requests and answers of citizens' wallet accounts (docs/api-contract.md, section 21; BVTGC-123 on)."""

from __future__ import annotations

from rest_framework import serializers

from apps.audit.masking import mask_email
from apps.posts.sharing import invite_url

from .models import CitizenAccount
from .serializers import _describer, _village


class SignupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    mobile = serializers.CharField(max_length=20)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    village_id = serializers.IntegerField(min_value=1)
    age_confirmed = serializers.BooleanField()
    consent_version = serializers.CharField(max_length=20)
    language = serializers.ChoiceField(choices=["te", "en"], default="te")
    referral_code = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    android_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")


class CitizenLoginSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254, help_text="The email, or the 10-digit mobile number")
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class CitizenForgotSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254, help_text="The email, or the 10-digit mobile number")


class CitizenResetSerializer(CitizenForgotSerializer):
    code = serializers.CharField(max_length=10)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class AccountUpdateSerializer(serializers.Serializer):
    village_id = serializers.IntegerField(min_value=1, required=False)
    language = serializers.ChoiceField(choices=["te", "en"], required=False)


class CitizenPasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class EmailChangeSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254, help_text="The new email")
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class CodeOnlySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=10)


class MobileChangeSerializer(serializers.Serializer):
    mobile = serializers.CharField(max_length=20, help_text="The new 10-digit number")
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class PasswordOnlySerializer(serializers.Serializer):
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class DeleteWithCodeSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254, help_text="The email, or the 10-digit mobile number")
    code = serializers.CharField(max_length=10)


class TokenPairOnlySerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class SignupTokenSerializer(serializers.Serializer):
    signup_token = serializers.CharField(max_length=500)


class SignupVerifySerializer(SignupTokenSerializer):
    code = serializers.CharField(max_length=10)


class CodeSentSerializer(serializers.Serializer):
    expires_in = serializers.IntegerField()
    resend_after = serializers.IntegerField()
    email_hint = serializers.CharField(required=False)


class SignupStartedSerializer(CodeSentSerializer):
    signup_token = serializers.CharField()


class AccountSerializer(serializers.Serializer):
    """The citizen's own account (contract section 21, "Account"). Never the full email or number."""

    name = serializers.CharField()
    language = serializers.CharField()
    village = serializers.DictField()
    email_hint = serializers.CharField()
    email_verified = serializers.BooleanField()
    mobile_last4 = serializers.CharField()
    mobile_missing = serializers.BooleanField()
    mobile_verified = serializers.BooleanField()
    referral_code = serializers.CharField()
    invite_url = serializers.CharField(allow_null=True)
    wallet = serializers.DictField()
    referral = serializers.DictField(allow_null=True)
    created_at = serializers.DateTimeField()

    def to_representation(self, account: CitizenAccount) -> dict:
        from apps.wallet.models import Referral
        from apps.wallet.services import summary

        wallet = getattr(account, "wallet", None)
        balance = summary(wallet) if wallet is not None else {"confirmed_points": 0, "held_points": 0, "next_release_at": None}
        own = Referral.objects.filter(referee=account).values("status", "confirm_after").first()
        base = invite_url()
        return {
            "name": account.user.name,
            "language": account.user.preferred_language,
            "village": _village(_describer(self.context), account.village_id),
            "email_hint": mask_email(account.email) if account.email else "",
            "email_verified": account.email_verified_at is not None,
            "mobile_last4": account.mobile_last4,
            "mobile_missing": account.mobile_hash is None,
            "mobile_verified": account.mobile_verified_at is not None,
            "referral_code": account.referral_code,
            "invite_url": f"{base}?ref={account.referral_code}" if base else None,
            "wallet": balance,
            "referral": own,
            "created_at": account.created_at,
        }


class SignedInSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    account = AccountSerializer()
