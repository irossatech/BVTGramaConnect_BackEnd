"""Audit log API for Super Admins: list with filters, CSV export. Contract section 8."""

import io
import json
from datetime import datetime, time

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from apps.accounts.serializers import describer_from
from apps.geography.areas import AreaRef
from grama.csvsafe import safe_writer
from grama.exceptions import ApiError
from grama.renderers import JSONRenderer
from apps.accounts import authz
from apps.accounts.authentication import client_via
from apps.accounts.permissions import HasPerm

from .models import AuditLog, AuditType
from .services import record

FILTERS = [
    OpenApiParameter("q", OpenApiTypes.STR, description="Actor name, action, target or role"),
    OpenApiParameter("type", OpenApiTypes.STR, enum=[t.value for t in AuditType]),
    OpenApiParameter("actor", OpenApiTypes.UUID),
    OpenApiParameter("action", OpenApiTypes.STR),
    OpenApiParameter("from", OpenApiTypes.DATE),
    OpenApiParameter("to", OpenApiTypes.DATE),
]


class AuditEntrySerializer(serializers.ModelSerializer):
    at = serializers.DateTimeField(source="created_at")
    actor = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "at", "actor", "action", "type", "target_label", "target", "area", "via", "data"]

    def get_actor(self, obj) -> dict:
        if obj.actor_id is None and not obj.actor_name:
            return {"id": None, "name": "System", "role": "SYSTEM"}
        return {"id": str(obj.actor_id) if obj.actor_id else None, "name": obj.actor_name, "role": obj.actor_role}

    def get_target(self, obj) -> dict | None:
        if not obj.target_type:
            return None
        return {"type": obj.target_type.split(".")[-1], "id": obj.target_id}

    def get_area(self, obj):
        if not obj.area_level or obj.area_id is None:
            return None
        return describer_from(self.context).describe(AreaRef(obj.area_level, obj.area_id))


def _day(value: str, name: str, end: bool = False):
    day = parse_date(value)
    if day is None:
        raise ApiError("validation_error", _("Use a date like 2026-10-14."), fields={name: [_("Invalid date.")]})
    return timezone.make_aware(datetime.combine(day, time.max if end else time.min))


def filtered(request, *, with_type: bool = True):
    params = request.query_params
    queryset = AuditLog.objects.all()
    query = params.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(actor_name__icontains=query)
            | Q(action__icontains=query)
            | Q(target_label__icontains=query)
            | Q(actor_role__icontains=query)
        )
    if params.get("actor"):
        queryset = queryset.filter(actor_id=params["actor"])
    if params.get("action"):
        queryset = queryset.filter(action=params["action"])
    if params.get("from"):
        queryset = queryset.filter(created_at__gte=_day(params["from"], "from"))
    if params.get("to"):
        queryset = queryset.filter(created_at__lte=_day(params["to"], "to", end=True))
    if with_type and params.get("type"):
        queryset = queryset.filter(type=params["type"])
    return queryset


VIEW = "audit.view_audit_log"
EXPORT = "audit.export_audit_log"


def in_reach(request, queryset, perm: str):
    """Entries inside the areas where the user holds `perm`; entries without an area need it platform-wide."""
    scope = authz.area_q(request.user, perm)
    if not authz.can(request.user, perm, authz.PLATFORM):
        scope &= ~Q(area_level="")
    return queryset.filter(scope)


class AuditListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, HasPerm(VIEW, message="Only Super Admins can read the audit log.")]
    serializer_class = AuditEntrySerializer

    def get_queryset(self):
        return in_reach(self.request, filtered(self.request), VIEW).order_by("-created_at")

    @extend_schema(summary="Audit log", parameters=FILTERS)
    def get(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        context = self.get_serializer_context()
        describer_from(context).prime(
            [AreaRef(e.area_level, e.area_id) for e in page if e.area_level and e.area_id is not None]
        )
        response = self.get_paginated_response(AuditEntrySerializer(page, many=True, context=context).data)
        counts = in_reach(request, filtered(request, with_type=False), VIEW).order_by().values("type").annotate(n=Count("id"))
        by_type = {t.value: 0 for t in AuditType}
        by_type.update({row["type"]: row["n"] for row in counts})
        response.data["counts_by_type"] = by_type
        return response


class CSVRenderer(BaseRenderer):
    """Lets ?format=csv through DRF's content negotiation. Errors still go out as JSON text."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (dict, list)):
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return data


#: Filters whose values hold no personal data; they are kept in the export's audit entry.
PLAIN_FILTERS = ("type", "actor", "action", "from", "to", "format")


def export_filters(params) -> dict:
    """What the export was filtered by. A search text (q) can be an email or mobile number, and the
    audit log is never cleaned, so only the fact that one was given is kept, never its value."""
    filters = {name: params.get(name) for name in PLAIN_FILTERS if params.get(name)}
    if params.get("q"):
        filters["q"] = "(given)"
    return filters


class AuditExportView(APIView):
    permission_classes = [IsAuthenticated, HasPerm(EXPORT, message="Only Super Admins can download the audit log.")]
    renderer_classes = [JSONRenderer, CSVRenderer]

    @extend_schema(summary="Audit log as CSV", parameters=[*FILTERS, OpenApiParameter("format", OpenApiTypes.STR, enum=["csv"])], responses={(200, "text/csv"): OpenApiTypes.BINARY})
    def get(self, request):
        if request.query_params.get("format", "csv") != "csv":
            raise ApiError("validation_error", _("Only CSV export is available."), fields={"format": ["csv"]})
        rows = in_reach(request, filtered(request), EXPORT).order_by("-created_at")[:50000]
        buffer = io.StringIO()
        writer = safe_writer(buffer)  # names and titles never run as spreadsheet formulas
        writer.writerow(["time", "user", "role", "action", "type", "target", "area_level", "area_id", "via"])
        for entry in rows:
            writer.writerow([
                timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                entry.actor_name or "System", entry.actor_role, entry.action, entry.type,
                entry.target_label, entry.area_level, entry.area_id or "", entry.via,
            ])
        record("report.exported", actor=request.user, via=client_via(request),
               data={"report": "audit", "filters": export_filters(request.query_params)})
        response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="audit-{timezone.localdate():%Y%m%d}.csv"'
        return response
