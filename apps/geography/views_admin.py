"""Master data writes for Super Admins: districts, mandals, villages, CSV import.

docs/api-contract.md, section 4. Areas are never deleted; deactivating one hides
it and everything below it. Every change is written to the audit log.
"""

from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from grama.exceptions import ApiError
from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm

from .areas import AreaRef
from .importer import GeographyImporter
from .models import District, Mandal, State, Village, name_key

MAX_MISSING_LISTED = 200


# --- Serializers ----------------------------------------------------------------


#: Owner decision Q13: only the Super Admin maintains villages and mandals.
MANAGE_MASTER_DATA = HasPerm("geography.manage_master_data", PLATFORM, "Only Super Admins can do this.")


class AreaAdminFields(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name_en = serializers.CharField(max_length=120)
    name_te = serializers.CharField(max_length=120, required=False, allow_blank=True)
    lgd_code = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False)

    def validate_name_en(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Enter a name."))
        return value


class VillageAdminFields(AreaAdminFields):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    pin_code = serializers.RegexField(r"^\d{6}$", required=False, allow_blank=True)


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


# --- Writes ---------------------------------------------------------------------------


LEVEL_CONFIG = {
    "districts": (District, "state", State, AreaAdminFields),
    "mandals": (Mandal, "district", District, AreaAdminFields),
    "villages": (Village, "mandal", Mandal, VillageAdminFields),
}


class AreaWriteMixin:
    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]
    kind: str = ""

    @property
    def config(self):
        return LEVEL_CONFIG[self.kind]

    def check_unique(self, model, parent_field, parent_id, name, exclude_id=None):
        clash = model.objects.filter(**{f"{parent_field}_id": parent_id, "name_key": name_key(name)})
        if exclude_id:
            clash = clash.exclude(id=exclude_id)
        if clash.exists():
            message = _("This name already exists here.")
            raise ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={"name_en": [message]})

    def check_lgd(self, model, code, exclude_id=None):
        if not code:
            return
        clash = model.objects.filter(lgd_code=code)
        if exclude_id:
            clash = clash.exclude(id=exclude_id)
        if clash.exists():
            message = _("This LGD code is already used.")
            raise ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={"lgd_code": [message]})


class AreaCreateView(AreaWriteMixin, APIView):
    def post(self, request):
        model, parent_field, parent_model, fields = self.config
        data = fields(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        parent_id = request.data.get(f"{parent_field}_id")
        parent = parent_model.objects.filter(id=parent_id).first() if str(parent_id or "").isdigit() else None
        if parent is None:
            raise ApiError(
                "validation_error",
                _("Choose the parent area."),
                fields={f"{parent_field}_id": [_("No such area.")]},
            )
        self.check_unique(model, parent_field, parent.id, values["name_en"])
        self.check_lgd(model, values.get("lgd_code"))
        with transaction.atomic():
            obj = model.objects.create(**{parent_field: parent}, **values)
            record(
                "area.created",
                actor=request.user,
                target=obj,
                target_label=obj.name_en,
                area=AreaRef(obj.level, obj.id),
                via=client_via(request),
            )
        return Response(area_row(obj), status=status.HTTP_201_CREATED)


class AreaUpdateView(AreaWriteMixin, APIView):
    def patch(self, request, pk):
        model, parent_field, _parent_model, fields = self.config
        obj = model.objects.filter(pk=pk).first()
        if obj is None:
            raise ApiError("not_found", _("No such area."), status=status.HTTP_404_NOT_FOUND)
        data = fields(obj, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        if "name_en" in values:
            self.check_unique(model, parent_field, getattr(obj, f"{parent_field}_id"), values["name_en"], obj.id)
        if values.get("lgd_code"):
            self.check_lgd(model, values["lgd_code"], obj.id)
        was_active = obj.is_active
        with transaction.atomic():
            for key, value in values.items():
                setattr(obj, key, value)
            obj.save()
            if "is_active" in values and values["is_active"] != was_active:
                action = "area.reactivated" if obj.is_active else "area.deactivated"
            else:
                action = "area.updated"
            record(
                action,
                actor=request.user,
                target=obj,
                target_label=obj.name_en,
                area=AreaRef(obj.level, obj.id),
                via=client_via(request),
                data={"changed": sorted(values)},
            )
        return Response(area_row(obj))


def dispatch_by_method(**views):
    """One URL, different views per HTTP method (public GET, Super Admin writes)."""
    handlers = {method.upper(): view.as_view() for method, view in views.items()}

    def view(request, *args, **kwargs):
        handler = handlers.get(request.method)
        if handler is None:
            handler = next(iter(handlers.values()))
        return handler(request, *args, **kwargs)

    view.csrf_exempt = True
    return view


def create_view(kind):
    return type(f"{kind.title()}CreateView", (AreaCreateView,), {"kind": kind})


def update_view(kind):
    return type(f"{kind.title()}UpdateView", (AreaUpdateView,), {"kind": kind})


# --- Reads for the console --------------------------------------------------------------


class GeoTreeView(APIView):
    """States, districts and mandals with counts, including inactive ones.

    Villages come per mandal from GeoAdminVillagesView, so the tree stays small
    with 17,000 villages.
    """

    permission_classes = [IsAuthenticated, MANAGE_MASTER_DATA]

    @extend_schema(summary="Master data tree for the console")
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

    @extend_schema(summary="Every village of one mandal, including inactive ones")
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

    @extend_schema(summary="Import villages from CSV", request={"multipart/form-data": {"type": "object"}})
    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ApiError("validation_error", _("Attach a CSV file."), fields={"file": [_("Required.")]})
        dry_run = request.query_params.get("dry_run") in ("1", "true", "yes")
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError("validation_error", _("Save the file as UTF-8 CSV."), fields={"file": [_("Not UTF-8.")]}) from None
        report = GeographyImporter(dry_run=dry_run).run_text(text)
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
