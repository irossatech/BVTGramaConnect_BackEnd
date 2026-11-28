from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.serializers import AreaInputSerializer, AreaOutputSerializer, describer_from

from . import services
from .models import Contact, ContactSection, ContactType


# --- Public (citizens) -------------------------------------------------------------------------


class PublicAreaSerializer(serializers.Serializer):
    level = serializers.CharField()
    id = serializers.IntegerField()
    name = serializers.CharField()


class PublicContactSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()
    phone = serializers.CharField()
    phone_kind = serializers.CharField()
    email = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()

    def get_name(self, obj) -> str:
        return obj.display_name()

    def get_email(self, obj) -> str | None:
        return obj.email or None

    @extend_schema_field(PublicAreaSerializer)
    def get_area(self, obj):
        area = describer_from(self.context).describe(obj.area)
        return {key: area[key] for key in ("level", "id", "name")} if area else None


class GroupSerializer(serializers.Serializer):
    type = serializers.CharField()
    label = serializers.CharField()
    contacts = PublicContactSerializer(many=True)


class CitizenContactsSerializer(serializers.Serializer):
    village = AreaOutputSerializer()
    for_you = GroupSerializer(many=True)
    helplines = GroupSerializer(many=True)
    updated_at = serializers.DateTimeField(allow_null=True)


class PublicContactTypeSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = ContactType
        fields = ["key", "section", "name", "is_other", "sort_order"]

    def get_name(self, obj) -> str:
        return obj.display_name()


# --- Officials ---------------------------------------------------------------------------------


class ContactWriteSerializer(serializers.Serializer):
    """Shapes only; the rules (phone grammar, area, duplicates …) live in services.py."""

    type = serializers.CharField(max_length=40)
    custom_type = serializers.CharField(max_length=40, required=False, allow_blank=True)
    name = serializers.CharField(max_length=150, trim_whitespace=True)
    name_te = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    area = AreaInputSerializer()
    sort_order = serializers.IntegerField(required=False, min_value=0, max_value=1000)
    consent_recorded = serializers.BooleanField(required=False)
    verified = serializers.BooleanField(required=False, help_text="PATCH only: true stamps verified_at.")


class TypeBriefSerializer(serializers.Serializer):
    key = serializers.CharField()
    section = serializers.CharField()
    name = serializers.CharField()


class PersonBriefSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class AdminContactSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    needs_recheck = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id", "type", "custom_type", "name", "name_te", "phone", "phone_kind", "email", "area",
            "sort_order", "consent_recorded", "verified_at", "needs_recheck", "created_by",
            "created_at", "updated_at", "allowed_actions",
        ]

    @extend_schema_field(TypeBriefSerializer)
    def get_type(self, obj):
        return {"key": obj.type.key, "section": obj.type.section, "name": obj.type.display_name()}

    @extend_schema_field(AreaOutputSerializer)
    def get_area(self, obj):
        return describer_from(self.context).describe(obj.area)

    @extend_schema_field(PersonBriefSerializer(allow_null=True))
    def get_created_by(self, obj):
        if obj.created_by_id is None:
            return None
        return {"id": str(obj.created_by_id), "name": obj.created_by.name}

    def get_needs_recheck(self, obj) -> bool:
        limit = timezone.now() - timedelta(days=settings.CONTACTS_VERIFY_DAYS)
        return obj.verified_at is None or obj.verified_at < limit

    def get_allowed_actions(self, obj) -> list[str]:
        request = self.context.get("request")
        if request is None or not services.may_manage(request.user, obj.area):
            return []
        return ["edit", "remove", "verify"]


class ImportErrorSerializer(serializers.Serializer):
    row = serializers.IntegerField()
    field = serializers.CharField()
    message = serializers.CharField()


class ImportReportSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField()
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    skipped = serializers.IntegerField()
    errors = ImportErrorSerializer(many=True)


class AdminContactTypeSerializer(serializers.ModelSerializer):
    key = serializers.SlugField(max_length=40)
    section = serializers.ChoiceField(choices=ContactSection.choices)
    contact_count = serializers.SerializerMethodField()

    class Meta:
        model = ContactType
        fields = ["id", "key", "section", "name_en", "name_te", "is_other", "sort_order", "is_active", "contact_count"]
        read_only_fields = ["id", "contact_count"]

    def get_contact_count(self, obj) -> int:
        return getattr(obj, "contact_count", 0)
