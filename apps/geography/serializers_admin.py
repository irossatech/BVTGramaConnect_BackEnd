"""Super Admin area writes: request shapes (validated here) and response shapes (for Swagger).

The rules beyond shape (parent exists, unique names, warnings) live in services.py.
"""

from rest_framework import serializers

NAME = {"max_length": 120}


class _AreaFields(serializers.Serializer):
    name_en = serializers.CharField(**NAME)
    name_te = serializers.CharField(required=False, allow_blank=True, **NAME)
    lgd_code = serializers.RegexField(r"^\d{1,20}$", required=False, allow_blank=True, allow_null=True)

    def validate_name_en(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Enter a name.")
        return value

    def validate_lgd_code(self, value):
        return value or None


class DistrictCreateSerializer(_AreaFields):
    state_id = serializers.IntegerField(min_value=1)


class MandalCreateSerializer(_AreaFields):
    district_id = serializers.IntegerField(min_value=1)


class _VillagePlace(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    pin_code = serializers.RegexField(r"^\d{6}$", required=False, allow_blank=True)


class VillageCreateSerializer(_AreaFields, _VillagePlace):
    mandal_id = serializers.IntegerField(min_value=1)


class AreaUpdateSerializer(_AreaFields):
    """PATCH a district or mandal: every field optional."""

    is_active = serializers.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("partial", True)
        super().__init__(*args, **kwargs)


class VillageUpdateSerializer(AreaUpdateSerializer, _VillagePlace):
    pass


# --- Responses (Swagger only) ------------------------------------------------------------


class WarningSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class AreaRowSerializer(serializers.Serializer):
    """views_admin.area_row(): every area; the levels add their parent's id and, for villages, the place."""

    id = serializers.IntegerField()
    level = serializers.ChoiceField(["STATE", "DISTRICT", "MANDAL", "VILLAGE"])
    name_en = serializers.CharField()
    name_te = serializers.CharField()
    lgd_code = serializers.CharField(allow_null=True)
    is_active = serializers.BooleanField()
    state_id = serializers.IntegerField(required=False)
    district_id = serializers.IntegerField(required=False)
    mandal_id = serializers.IntegerField(required=False)
    latitude = serializers.CharField(required=False, allow_null=True, help_text="6 decimals")
    longitude = serializers.CharField(required=False, allow_null=True, help_text="6 decimals")
    pin_code = serializers.CharField(required=False)


class AreaWriteSerializer(AreaRowSerializer):
    """What every write answers: the row plus data-quality warnings (never blocking)."""

    warnings = WarningSerializer(many=True)


class TreeMandal(AreaRowSerializer):
    village_count = serializers.IntegerField()
    villages = AreaRowSerializer(many=True, required=False, help_text="Only with ?villages=1")


class TreeDistrict(AreaRowSerializer):
    mandal_count = serializers.IntegerField()
    village_count = serializers.IntegerField()
    mandals = TreeMandal(many=True)


class TreeState(AreaRowSerializer):
    districts = TreeDistrict(many=True)


class ImportCounts(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    unchanged = serializers.IntegerField()


class MissingCoordinates(serializers.Serializer):
    state = serializers.CharField()
    district = serializers.CharField()
    mandal = serializers.CharField()
    village = serializers.CharField()
    village_lgd_code = serializers.CharField(allow_null=True)


class GeographyImportReport(serializers.Serializer):
    ok = serializers.BooleanField()
    dry_run = serializers.BooleanField()
    rows = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField())
    warnings = serializers.ListField(child=serializers.CharField())
    counts = serializers.DictField(child=ImportCounts(), help_text="state, district, mandal, village")
    missing_coordinates = MissingCoordinates(many=True)
    missing_coordinates_count = serializers.IntegerField()
    missing_telugu = serializers.IntegerField()


CSV_UPLOAD = {
    "multipart/form-data": {
        "type": "object",
        "properties": {"file": {"type": "string", "format": "binary"}},
        "required": ["file"],
    }
}
