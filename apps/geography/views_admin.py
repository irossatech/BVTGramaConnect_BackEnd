"""Master data for Super Admins: districts, mandals, villages, the console tree and the CSV import.

docs/api-contract.md, section 4. The list URLs serve the public read (GET, no
login) and the Super Admin write (POST/PATCH) in one class each. Views are
thin: they validate the request shape and call services.py, the only writer.
Areas are never deleted; deactivating one hides it and everything below it.
"""

from __future__ import annotations

from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.audit.services import record
from grama.exceptions import ApiError
from grama.schema import ErrorSerializer

from . import services
from .importer import GeographyImporter
from .models import District, Mandal, State, Village
from .serializers_admin import (
    CSV_UPLOAD,
    AreaRowSerializer,
    AreaUpdateSerializer,
    AreaWriteSerializer,
    DistrictCreateSerializer,
    GeographyImportReport,
    MandalCreateSerializer,
    TreeState,
    VillageCreateSerializer,
    VillageUpdateSerializer,
)
from .views import DISTRICT_PARAMS, STATE_PARAMS, VILLAGE_PARAMS, DistrictListView, MandalListView, VillageDetailView, VillageListView

MAX_MISSING_LISTED = 200

#: Owner decision Q13: only the Super Admin maintains districts, mandals and villages.
MANAGE_MASTER_DATA = HasPerm("geography.manage_master_data", PLATFORM, "Only Super Admins can do this.")
WRITE_ERRORS = {400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer, 409: ErrorSerializer}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def area_row(obj) -> dict:
    row = {
        "id": obj.id,
        "level": obj.level,
        "name_en": obj.name_en,
        "name_te": obj.name_te,
        "lgd_code": obj.lgd_code,
        "is_active": obj.is_active,
    }
    if isinstance(obj, Village):
        row.update(
            latitude=None if obj.latitude is None else f"{obj.latitude:.6f}",
            longitude=None if obj.longitude is None else f"{obj.longitude:.6f}",
            pin_code=obj.pin_code,
            mandal_id=obj.mandal_id,
        )
    elif isinstance(obj, Mandal):
        row["district_id"] = obj.district_id
    elif isinstance(obj, District):
        row["state_id"] = obj.state_id
    return row


# --- Writes -----------------------------------------------------------------------------


class PublicReadAdminWrite:
    """GET stays public with no authentication at all, so a stale token on a phone never
    breaks it; POST and PATCH need a login and geography.manage_master_data.

    The class keeps the default authenticators, so the schema shows the login on
    the writes; a read drops them before DRF reads them.
    """

    authentication_classes = api_settings.DEFAULT_AUTHENTICATION_CLASSES

    def initialize_request(self, request, *args, **kwargs):
        # get_authenticators() runs inside initialize_request, before self.request exists.
        if request.method in SAFE_METHODS:
            self.authentication_classes = []
        return super().initialize_request(request, *args, **kwargs)

    def get_permissions(self):
        method = getattr(getattr(self, "request", None), "method", None)
        if method in SAFE_METHODS:
            return [AllowAny()]
        return [IsAuthenticated(), MANAGE_MASTER_DATA]  # writes, and the schema


def _respond(outcome, code=status.HTTP_200_OK) -> Response:
    return Response({**area_row(outcome.area), "warnings": outcome.warnings}, status=code)


def _create(level, serializer_class):
    def post(self, request):
        data = serializer_class(data=request.data)
        data.is_valid(raise_exception=True)
        outcome = services.create_area(request.user, level, data.validated_data, via=client_via(request))
        return _respond(outcome, status.HTTP_201_CREATED)

    return post


def _update(level, serializer_class):
    def patch(self, request, pk):
        data = serializer_class(data=request.data)
        data.is_valid(raise_exception=True)
        return _respond(services.update_area(request.user, level, pk, data.validated_data, via=client_via(request)))

    return patch


@extend_schema_view(
    get=extend_schema(summary="Active districts (public)", auth=[], parameters=STATE_PARAMS),
    post=extend_schema(summary="Add a district (Super Admin)", request=DistrictCreateSerializer,
                       responses={201: AreaWriteSerializer, **WRITE_ERRORS}),
)
class DistrictCollectionView(PublicReadAdminWrite, DistrictListView):
    post = _create("DISTRICT", DistrictCreateSerializer)


@extend_schema_view(
    get=extend_schema(summary="Active mandals of a district (public)", auth=[], parameters=DISTRICT_PARAMS),
    post=extend_schema(summary="Add a mandal (Super Admin)", request=MandalCreateSerializer,
                       responses={201: AreaWriteSerializer, **WRITE_ERRORS}),
)
class MandalCollectionView(PublicReadAdminWrite, MandalListView):
    post = _create("MANDAL", MandalCreateSerializer)


@extend_schema_view(
    get=extend_schema(summary="Active villages of a mandal, or a name search (public)", auth=[], parameters=VILLAGE_PARAMS),
    post=extend_schema(summary="Add a village (Super Admin)", request=VillageCreateSerializer,
                       responses={201: AreaWriteSerializer, **WRITE_ERRORS}),
)
class VillageCollectionView(PublicReadAdminWrite, VillageListView):
    post = _create("VILLAGE", VillageCreateSerializer)


@extend_schema_view(
    get=extend_schema(summary="One active village with its mandal, district and path (public)", auth=[]),
    patch=extend_schema(summary="Edit, switch off or on a village (Super Admin)", request=VillageUpdateSerializer,
                        responses={200: AreaWriteSerializer, 404: ErrorSerializer, **WRITE_ERRORS}),
)
class VillageItemView(PublicReadAdminWrite, VillageDetailView):
    patch = _update("VILLAGE", VillageUpdateSerializer)


class _AdminOnlyItem(APIView):
    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]


@extend_schema_view(
    patch=extend_schema(summary="Edit, switch off or on a district (Super Admin)", request=AreaUpdateSerializer,
                        responses={200: AreaWriteSerializer, 404: ErrorSerializer, **WRITE_ERRORS}),
)
class DistrictItemView(_AdminOnlyItem):
    patch = _update("DISTRICT", AreaUpdateSerializer)


@extend_schema_view(
    patch=extend_schema(summary="Edit, switch off or on a mandal (Super Admin)", request=AreaUpdateSerializer,
                        responses={200: AreaWriteSerializer, 404: ErrorSerializer, **WRITE_ERRORS}),
)
class MandalItemView(_AdminOnlyItem):
    patch = _update("MANDAL", AreaUpdateSerializer)


# --- Reads for the console --------------------------------------------------------------


class GeoTreeView(APIView):
    """States, districts and mandals with counts, including inactive ones.

    Villages come per mandal from GeoAdminVillagesView, so the tree stays small
    with 17,000 villages.
    """

    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]

    @extend_schema(
        summary="Master data tree for the console",
        parameters=[OpenApiParameter("villages", OpenApiTypes.BOOL, description="1: include each mandal's villages")],
        responses=TreeState(many=True),
    )
    def get(self, request):
        states = list(State.objects.order_by("name_en"))
        districts = list(District.objects.order_by("name_en"))
        mandals = list(Mandal.objects.order_by("name_en"))
        village_counts: dict[int, int] = {}
        for mandal_id in Village.objects.values_list("mandal_id", flat=True):
            village_counts[mandal_id] = village_counts.get(mandal_id, 0) + 1
        villages_by_mandal: dict[int, list] = {}
        if request.query_params.get("villages") in ("1", "true"):
            for village in Village.objects.order_by("name_en"):
                villages_by_mandal.setdefault(village.mandal_id, []).append(area_row(village))
        mandals_by_district: dict[int, list] = {}
        for mandal in mandals:
            row = area_row(mandal)
            row["village_count"] = village_counts.get(mandal.id, 0)
            if villages_by_mandal or request.query_params.get("villages") in ("1", "true"):
                row["villages"] = villages_by_mandal.get(mandal.id, [])
            mandals_by_district.setdefault(mandal.district_id, []).append(row)
        districts_by_state: dict[int, list] = {}
        for district in districts:
            row = area_row(district)
            row["mandals"] = mandals_by_district.get(district.id, [])
            row["mandal_count"] = len(row["mandals"])
            row["village_count"] = sum(m["village_count"] for m in row["mandals"])
            districts_by_state.setdefault(district.state_id, []).append(row)
        result = []
        for state in states:
            row = area_row(state)
            row["districts"] = districts_by_state.get(state.id, [])
            result.append(row)
        return Response(result)


class GeoAdminVillagesView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]

    @extend_schema(
        summary="Every village of one mandal, including inactive ones",
        parameters=[OpenApiParameter("mandal", OpenApiTypes.INT, required=True)],
        responses=AreaRowSerializer(many=True),
    )
    def get(self, request):
        mandal = request.query_params.get("mandal", "")
        if not mandal.isdigit():
            raise ApiError("validation_error", _("Give a mandal."), fields={"mandal": [_("Required.")]})
        villages = Village.objects.filter(mandal_id=int(mandal)).order_by("name_en")
        return Response([area_row(village) for village in villages])


class VillageImportView(APIView):
    """CSV import, same rules as `manage.py import_geography`. ?dry_run=1 saves nothing."""

    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Import villages from CSV",
        parameters=[OpenApiParameter("dry_run", OpenApiTypes.BOOL, description="1: check every row and save nothing")],
        request=CSV_UPLOAD,
        responses=GeographyImportReport,
    )
    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ApiError("validation_error", _("Attach a CSV file."), fields={"file": [_("Required.")]})
        dry_run = request.query_params.get("dry_run") in ("1", "true", "yes")
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError("validation_error", _("Save the file as UTF-8 CSV."), fields={"file": [_("Not UTF-8.")]}) from None
        report = GeographyImporter(dry_run=dry_run, actor=request.user, via=client_via(request)).run_text(text)
        body = {
            "ok": report.ok,
            "dry_run": dry_run,
            "rows": report.rows,
            "errors": report.errors,
            "warnings": report.warnings,
            "counts": {level: vars(counts) for level, counts in report.counts.items()},
            "missing_coordinates": [
                {"state": s, "district": d, "mandal": m, "village": v, "village_lgd_code": code}
                for s, d, m, v, code in report.missing_coordinates[:MAX_MISSING_LISTED]
            ],
            "missing_coordinates_count": len(report.missing_coordinates),
            "missing_coordinates_total": report.missing_coordinates_total,
            "missing_telugu": report.missing_telugu,
        }
        if not report.ok:
            raise ApiError(
                "validation_error",
                _("%(n)d error(s) in the file. Nothing was imported.") % {"n": len(report.errors)},
                report=body,
            )
        if not dry_run:
            record(
                "area.imported",
                actor=request.user,
                target_label=upload.name[:200],
                via=client_via(request),
                data={"rows": report.rows, "counts": body["counts"]},
            )
        return Response(body)
