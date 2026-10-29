"""Shapes from docs/api-contract.md, section 18: the same as the services' (section 16)."""

from rest_framework import serializers

from .models import COLORS, ICONS, Bill


class BillSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = ["key", "name_en", "name_te", "icon", "logo", "color", "is_active"]

    def get_logo(self, obj) -> str | None:
        """Absolute URL of the uploaded logo, or null: then the app draws `icon`."""
        if not obj.logo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.logo.url) if request is not None else obj.logo.url


class AdminBillSerializer(BillSerializer):
    class Meta(BillSerializer.Meta):
        fields = [*BillSerializer.Meta.fields, "order", "created_at", "updated_at"]


class BillWriteSerializer(serializers.Serializer):
    """POST and PATCH, as JSON or multipart (with `logo`, a file). The logo itself is checked in services."""

    name_en = serializers.CharField(max_length=60)
    name_te = serializers.CharField(max_length=60, required=False, allow_blank=True)
    icon = serializers.ChoiceField(choices=ICONS, required=False)
    color = serializers.ChoiceField(choices=COLORS)
    is_active = serializers.BooleanField(required=False)

    def to_internal_value(self, data):
        if hasattr(data, "dict"):  # multipart: plain values; the logo file is read by the view
            data = {key: value for key, value in data.dict().items() if key != "logo"}
        return super().to_internal_value(data)

    def validate_name_en(self, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("Write at least 2 letters.")
        return value

    def validate_name_te(self, value):
        return " ".join(value.split())


class BillOrderSerializer(serializers.Serializer):
    keys = serializers.ListField(child=serializers.CharField(max_length=40), allow_empty=False)
