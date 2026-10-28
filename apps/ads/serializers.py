"""Shapes from docs/api-contract.md, section 17."""

from django.core.validators import URLValidator
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.serializers import describer_from
from apps.geography.models import AreaLevel

from .models import Ad


def absolute(request, field) -> str:
    return request.build_absolute_uri(field.url) if request is not None else field.url


class AdWriteSerializer(serializers.Serializer):
    """POST (multipart, with `image`) and PATCH (multipart with a new image, or JSON). The image itself is checked in services."""

    title = serializers.CharField(max_length=80)
    link = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    area_level = serializers.ChoiceField(choices=AreaLevel.choices)
    area_id = serializers.IntegerField(min_value=1)
    starts_on = serializers.DateField(required=False, allow_null=True, default=None)
    ends_on = serializers.DateField(required=False, allow_null=True, default=None)
    is_active = serializers.BooleanField(required=False, default=True)

    def to_internal_value(self, data):
        # An empty string means "no date", in multipart forms and JSON alike.
        if hasattr(data, "dict"):
            data = data.dict()
        data = {key: (None if key in ("starts_on", "ends_on") and value == "" else value) for key, value in data.items()}
        return super().to_internal_value(data)

    def validate_link(self, value):
        value = value.strip()
        if value:
            # Only http(s): no javascript:, intent:, file: or similar links in the app.
            URLValidator(schemes=["http", "https"], message="Give an http:// or https:// link.")(value)
        return value


class AdAreaSerializer(serializers.Serializer):
    level = serializers.ChoiceField(choices=AreaLevel.choices)
    id = serializers.IntegerField()
    name = serializers.CharField()


class AdminAdSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()

    class Meta:
        model = Ad
        fields = ["id", "title", "image", "link", "area", "starts_on", "ends_on", "is_active", "created_at", "updated_at"]

    def get_image(self, obj) -> str:
        return absolute(self.context.get("request"), obj.image)

    @extend_schema_field(AdAreaSerializer)
    def get_area(self, obj):
        described = describer_from(self.context).describe(obj.area)
        return {"level": obj.area_level, "id": obj.area_id, "name": described["name"] if described else ""}


class PublicAdSerializer(serializers.ModelSerializer):
    """Only what the banner needs: never who made it or anything internal."""

    image = serializers.SerializerMethodField()

    class Meta:
        model = Ad
        fields = ["id", "title", "image", "link"]

    def get_image(self, obj) -> str:
        return absolute(self.context.get("request"), obj.image)
