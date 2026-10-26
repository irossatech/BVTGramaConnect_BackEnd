"""Public geography endpoints: cascading lists and nearest village.

No login. Only active areas whose parents are active. Unpaged, because each
level under one parent is small. GET responses are cacheable for
GEO_PUBLIC_MAX_AGE (10 minutes) and vary by Accept-Language. The district,
mandal and village URLs also take the Super Admin's writes (views_admin.py),
which are never cached.
"""

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from grama.exceptions import ApiError

from .models import District, Mandal, State, Village
from .nearest import nearest_village
from .serializers import (
    DistrictSerializer,
    MandalSerializer,
    NearestQuerySerializer,
    NearestVillageSerializer,
    StateSerializer,
    VillageDetailSerializer,
    VillageListSerializer,
)

VILLAGE_SEARCH_LIMIT = 50
# GET only: the same URLs take POST and PATCH (views_admin.py), whose answers must not be cached.
public_cache = method_decorator(cache_control(public=True, max_age=settings.GEO_PUBLIC_MAX_AGE), name="get")

# Query parameters of the public lists, on GET only (views_admin's classes add POST to these URLs).
STATE_PARAMS = [OpenApiParameter("state", OpenApiTypes.INT, description="State id")]
DISTRICT_PARAMS = [OpenApiParameter("district", OpenApiTypes.INT, required=True)]
VILLAGE_PARAMS = [
    OpenApiParameter("mandal", OpenApiTypes.INT, description="Mandal id. Required unless q is given."),
    OpenApiParameter("q", OpenApiTypes.STR, description="Search English and Telugu names, 2+ characters."),
]


def _int_param(request, name: str, *, required: bool) -> int | None:
    raw = request.query_params.get(name, "").strip()
    if not raw:
        if required:
            raise ApiError("validation_error", f"The '{name}' query parameter is required.", fields={name: ["Required."]})
        return None
    if not raw.isdigit():
        raise ApiError("validation_error", f"'{name}' must be a number.", fields={name: ["Must be a number."]})
    return int(raw)


class PublicGeoView:
    permission_classes = [AllowAny]
    authentication_classes: list = []
    pagination_class = None


@public_cache
class StateListView(PublicGeoView, generics.ListAPIView):
    serializer_class = StateSerializer

    def get_queryset(self):
        return State.objects.active().ordered_for_language()


@public_cache
@extend_schema_view(get=extend_schema(parameters=STATE_PARAMS))
class DistrictListView(PublicGeoView, generics.ListAPIView):
    serializer_class = DistrictSerializer

    def get_queryset(self):
        queryset = District.objects.active()
        state = _int_param(self.request, "state", required=False)
        if state is not None:
            queryset = queryset.filter(state_id=state)
        return queryset.ordered_for_language()


@public_cache
@extend_schema_view(get=extend_schema(parameters=DISTRICT_PARAMS))
class MandalListView(PublicGeoView, generics.ListAPIView):
    serializer_class = MandalSerializer

    def get_queryset(self):
        district = _int_param(self.request, "district", required=True)
        return Mandal.objects.active().filter(district_id=district).ordered_for_language()


@public_cache
@extend_schema_view(get=extend_schema(parameters=VILLAGE_PARAMS))
class VillageListView(PublicGeoView, generics.ListAPIView):
    serializer_class = VillageListSerializer

    def get_queryset(self):
        mandal = _int_param(self.request, "mandal", required=False)
        query = self.request.query_params.get("q", "").strip()
        if mandal is None and len(query) < 2:
            raise ApiError(
                "validation_error",
                "Give a mandal, or a search of at least 2 characters.",
                fields={"mandal": ["Required unless q has 2 or more characters."]},
            )
        queryset = Village.objects.active()
        if mandal is not None:
            queryset = queryset.filter(mandal_id=mandal)
        if query:
            queryset = queryset.filter(name_en__icontains=query) | queryset.filter(name_te__icontains=query)
        queryset = queryset.ordered_for_language()
        return queryset if mandal is not None else queryset[:VILLAGE_SEARCH_LIMIT]


@public_cache
class VillageDetailView(PublicGeoView, generics.RetrieveAPIView):
    serializer_class = VillageDetailSerializer

    def get_queryset(self):
        return Village.objects.active().select_related("mandal__district__state")


class NearestVillageView(PublicGeoView, APIView):
    @extend_schema(
        summary="Nearest village to a GPS position",
        parameters=[NearestQuerySerializer],
        responses=NearestVillageSerializer,
    )
    def get(self, request):
        query = NearestQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        result = nearest_village(query.validated_data["lat"], query.validated_data["lng"])
        if result is None:
            raise ApiError(
                "not_found",
                f"No village found within {settings.NEAREST_VILLAGE_MAX_KM} km. Pick your village from the list.",
                status=404,
            )
        village, distance = result
        return Response({"village": VillageDetailSerializer(village).data, "distance_km": round(distance, 2)})
