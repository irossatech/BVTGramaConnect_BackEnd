from rest_framework import serializers

from .areas import PATH_SEPARATOR
from .models import District, Mandal, State, Village


class LocalizedNameMixin(serializers.Serializer):
    """`name` in the language picked from Accept-Language (Telugu by default)."""

    name = serializers.SerializerMethodField()

    def get_name(self, obj) -> str:
        return obj.display_name()


class StateSerializer(LocalizedNameMixin, serializers.ModelSerializer):
    class Meta:
        model = State
        fields = ["id", "name"]


class DistrictSerializer(LocalizedNameMixin, serializers.ModelSerializer):
    class Meta:
        model = District
        fields = ["id", "name", "state_id"]


class MandalSerializer(LocalizedNameMixin, serializers.ModelSerializer):
    class Meta:
        model = Mandal
        fields = ["id", "name", "district_id"]


class VillageListSerializer(LocalizedNameMixin, serializers.ModelSerializer):
    class Meta:
        model = Village
        fields = ["id", "name", "mandal_id", "pin_code"]


class AreaBriefSerializer(LocalizedNameMixin, serializers.Serializer):
    id = serializers.IntegerField()


class VillageDetailSerializer(LocalizedNameMixin, serializers.ModelSerializer):
    mandal = AreaBriefSerializer()
    district = AreaBriefSerializer(source="mandal.district")
    state = AreaBriefSerializer(source="mandal.district.state")
    path = serializers.SerializerMethodField()

    class Meta:
        model = Village
        fields = [
            "id",
            "name",
            "pin_code",
            "latitude",
            "longitude",
            "mandal",
            "district",
            "state",
            "path",
        ]

    def get_path(self, obj) -> str:
        return PATH_SEPARATOR.join(
            area.display_name() for area in (obj, obj.mandal, obj.mandal.district)
        )


class NearestQuerySerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)


class NearestVillageSerializer(serializers.Serializer):
    village = VillageDetailSerializer()
    distance_km = serializers.FloatField()
