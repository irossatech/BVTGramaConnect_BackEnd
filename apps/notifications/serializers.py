from rest_framework import serializers

from apps.geography.models import Village

from .models import Device, Notification, NotificationTemplate, Platform


class DeviceSerializer(serializers.ModelSerializer):
    language = serializers.ChoiceField(choices=[("te", "te"), ("en", "en")], default="te")
    platform = serializers.ChoiceField(choices=Platform.choices)

    class Meta:
        model = Device
        fields = ["id", "fcm_token", "platform", "language", "village_id", "app_version"]
        read_only_fields = ["id"]
        # fcm_token uniqueness is handled by the upsert, not by a 400.
        extra_kwargs = {"fcm_token": {"validators": []}, "village_id": {"required": False}}

    def validate_village_id(self, value):
        if value is not None and not Village.objects.active().filter(id=value).exists():
            raise serializers.ValidationError("No such active village.")
        return value


class DeviceUpdateSerializer(DeviceSerializer):
    class Meta(DeviceSerializer.Meta):
        extra_kwargs = {
            "fcm_token": {"validators": [], "required": False},
            "platform": {"required": False},
            "village_id": {"required": False},
        }


class NotificationSerializer(serializers.ModelSerializer):
    post_id = serializers.UUIDField(allow_null=True)
    area = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "post_id", "area", "created_at", "read_at"]

    def get_area(self, obj) -> dict | None:
        if not obj.area_level:
            return None
        return {"level": obj.area_level, "id": obj.area_id}


class NotificationTemplateSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = NotificationTemplate
        fields = [
            "key",
            "name",
            "name_te",
            "name_en",
            "channel",
            "recipient",
            "text_te",
            "text_en",
            "variables",
            "editable",
            "updated_at",
        ]

    def get_name(self, obj) -> str:
        from django.utils.translation import get_language

        return obj.name_te if (get_language() or "te").startswith("te") else obj.name_en


class NotificationTemplateUpdateSerializer(serializers.Serializer):
    text_te = serializers.CharField(max_length=300)
    text_en = serializers.CharField(max_length=300)

    def validate(self, attrs):
        template = self.context["template"]
        allowed = set(template.variables)
        import string

        for field in ("text_te", "text_en"):
            used = {name for _, name, _, _ in string.Formatter().parse(attrs[field]) if name}
            unknown = used - allowed
            if unknown:
                raise serializers.ValidationError(
                    {field: [f"Unknown variable(s): {', '.join(sorted(unknown))}. Use: {', '.join(template.variables)}."]}
                )
        return attrs
