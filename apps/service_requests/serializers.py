"""Shapes from docs/api-contract.md, section 16."""

import re

from django.utils.translation import get_language
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts import authz

from . import services
from .models import RequestStatus, Service, ServiceRequest

MOBILE = re.compile(r"^[6-9][0-9]{9}$")


class CitizenServiceRequestWriteSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=Service.choices)
    name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    details = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_name(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Write at least 2 letters.")
        return value

    def validate_phone(self, value):
        value = "".join(value.split())
        if not MOBILE.match(value):
            raise serializers.ValidationError("Give a 10-digit Indian mobile number starting with 6, 7, 8 or 9.")
        return value

    def validate_email(self, value):
        return value.strip().lower()

    def validate_details(self, value):
        return value.strip()


class AreaNameSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


def _area(obj) -> dict:
    return {"id": obj.id, "name": obj.display_name(get_language() or "te")}


class CitizenServiceRequestSerializer(serializers.ModelSerializer):
    """What the citizen sees: their own fields, the area, the status and the latest note."""

    village = serializers.SerializerMethodField()
    mandal = serializers.SerializerMethodField()
    district = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = [
            "id", "service", "name", "phone", "email", "details", "village", "mandal", "district",
            "status", "note", "created_at", "updated_at",
        ]

    @extend_schema_field(AreaNameSerializer)
    def get_village(self, obj):
        return _area(obj.village)

    @extend_schema_field(AreaNameSerializer)
    def get_mandal(self, obj):
        return _area(obj.village.mandal)

    @extend_schema_field(AreaNameSerializer)
    def get_district(self, obj):
        return _area(obj.village.mandal.district)


class PersonSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()


class HistorySerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    by = PersonSerializer(allow_null=True)
    note = serializers.CharField()
    status = serializers.ChoiceField(choices=RequestStatus.choices, required=False)
    assigned_to = serializers.DictField(required=False, allow_null=True)


class ServiceRequestSerializer(CitizenServiceRequestSerializer):
    """The officials' view: the citizen's fields plus the assignee, the allowed actions and the history."""

    assigned_to = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()

    class Meta(CitizenServiceRequestSerializer.Meta):
        fields = [*CitizenServiceRequestSerializer.Meta.fields, "assigned_to", "allowed_actions", "history"]

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_assigned_to(self, obj):
        user = obj.assigned_to
        if user is None:
            return None
        grant = authz.best_grant(user, services.VIEW, services.area_of(obj))
        return {"id": str(user.id), "name": user.name, "role": grant.key if grant else ""}

    def get_allowed_actions(self, obj) -> list[str]:
        request = self.context.get("request")
        return services.allowed_actions(request.user, obj) if request is not None else []

    @extend_schema_field(HistorySerializer(many=True))
    def get_history(self, obj):
        rows = []
        for event in obj.history.all():
            row = {
                "at": serializers.DateTimeField().to_representation(event.at),
                "by": {"id": str(event.by.id), "name": event.by.name, "role": event.by_role} if event.by else None,
                "note": event.note,
            }
            if event.status:
                row["status"] = event.status
            if event.assigned_changed:
                row["assigned_to"] = (
                    {"id": str(event.assigned_to.id), "name": event.assigned_to.name} if event.assigned_to else None
                )
            rows.append(row)
        return rows


class ServiceRequestUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=RequestStatus.choices, required=False)
    assigned_to = serializers.UUIDField(required=False, allow_null=True)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)
