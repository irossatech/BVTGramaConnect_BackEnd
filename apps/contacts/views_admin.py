"""Important numbers for officials: the app (JWT) and the console (session + CSRF).

/api/v1/admin/contacts/                list, add
/api/v1/admin/contacts/{id}/           read, edit (also "verified": true), remove (soft delete)
/api/v1/admin/contacts/targets/        the "Who sees it" choices
/api/v1/admin/contacts/import/         CSV import, Super Admin
/api/v1/admin/contact-types/           types, Super Admin (list, add, edit, delete when unused)

Every write goes through services.py, which checks the permission over the area.
"""

import re

from django.conf import settings
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import authz
from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.geography.areas import DEPTH, AreaDescriber, AreaRef, within_q
from apps.geography.models import AreaLevel
from grama.exceptions import ApiError

from . import services
from .models import Contact, ContactSection, ContactType
from .selectors import manageable_contacts
from .serializers import (
    AdminContactSerializer,
    AdminContactTypeSerializer,
    ContactWriteSerializer,
    ImportReportSerializer,
)

PERM = services.PERM
UUID = r"[0-9a-fA-F-]{36}"

LIST_FILTERS = [
    OpenApiParameter("section", OpenApiTypes.STR, enum=[s.value for s in ContactSection], description="Default AREA"),
    OpenApiParameter("level", OpenApiTypes.STR, enum=[lv.value for lv in AreaLevel]),
    OpenApiParameter("area_id", OpenApiTypes.INT),
    OpenApiParameter("type", OpenApiTypes.STR, description="Type key"),
    OpenApiParameter("q", OpenApiTypes.STR, description="Name, or digits of the phone"),
]


def ordered(contacts):
    depth = Case(*[When(area_level=level, then=Value(i)) for level, i in DEPTH.items()], output_field=IntegerField())
    return contacts.annotate(depth=depth).order_by("depth", "area_id", "type__sort_order", "type_id", "sort_order", "name")


class AdminContactViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasPerm(PERM)]
    serializer_class = AdminContactSerializer
    lookup_value_regex = UUID

    def describe(self, contacts) -> dict:
        describer = AreaDescriber()
        describer.prime([contact.area for contact in contacts])
        return {"request": self.request, "describer": describer}

    def filtered(self):
        params = self.request.query_params
        section = params.get("section", ContactSection.AREA).upper()
        if section == ContactSection.HELPLINE:
            contacts = Contact.objects.filter(
                is_active=True, area_level=AreaLevel.STATE, type__section=ContactSection.HELPLINE
            ).select_related("type", "created_by")
        elif section == ContactSection.AREA:
            contacts = manageable_contacts(self.request.user)
        else:
            raise ApiError("validation_error", _("Section is AREA or HELPLINE."), fields={"section": list(ContactSection.values)})
        level, area_id = params.get("level"), params.get("area_id")
        if level or area_id:
            if level not in AreaLevel.values or not (area_id or "").isdigit():
                raise ApiError("validation_error", _("Give both level and area_id."), fields={"area_id": [_("Required with level.")]})
            contacts = contacts.filter(within_q(AreaRef(level, int(area_id))))
        if params.get("type"):
            contacts = contacts.filter(type__key=params["type"].strip())
        query = params.get("q", "").strip()
        if query:
            match = Q(name__icontains=query) | Q(name_te__icontains=query) | Q(custom_type__icontains=query)
            digits = re.sub(r"\D", "", query)
            if digits:
                match |= Q(phone__contains=digits)
            contacts = contacts.filter(match)
        return ordered(contacts)

    @extend_schema(summary="Important numbers I manage (AREA) or the statewide helplines (HELPLINE)", parameters=LIST_FILTERS)
    def list(self, request):
        page = self.paginate_queryset(self.filtered())
        return self.get_paginated_response(AdminContactSerializer(page, many=True, context=self.describe(page)).data)

    @extend_schema(summary="Add an important number", request=ContactWriteSerializer, responses={201: AdminContactSerializer})
    def create(self, request):
        data = ContactWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        contact = services.create_contact(request.user, data.validated_data, via=client_via(request))
        return Response(AdminContactSerializer(contact, context=self.describe([contact])).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="One important number")
    def retrieve(self, request, pk=None):
        contact = Contact.objects.select_related("type", "created_by").filter(id=pk, is_active=True).first()
        readable = contact is not None and (
            contact.type.section == ContactSection.HELPLINE or services.may_manage(request.user, contact.area)
        )
        if not readable:
            raise ApiError("not_found", _("No such number."), status=status.HTTP_404_NOT_FOUND)
        return Response(AdminContactSerializer(contact, context=self.describe([contact])).data)

    @extend_schema(summary="Edit an important number; \"verified\": true marks it checked", request=ContactWriteSerializer)
    def partial_update(self, request, pk=None):
        data = ContactWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        contact = services.update_contact(request.user, pk, data.validated_data, via=client_via(request))
        return Response(AdminContactSerializer(contact, context=self.describe([contact])).data)

    @extend_schema(summary="Remove an important number (hidden from citizens, kept for the audit trail)")
    def destroy(self, request, pk=None):
        services.remove_contact(request.user, pk, via=client_via(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="'Who sees it' choices for a new number",
        responses=inline_serializer("ContactTargets", {"levels": serializers.ListField(child=serializers.DictField())}),
    )
    @action(detail=False, methods=["get"])
    def targets(self, request):
        return Response(authz.area_targets(request.user, PERM, include_state=False))


class ContactImportView(APIView):
    """CSV import for the Super Admin. ?dry_run=1 checks every row and saves nothing."""

    permission_classes = [IsAuthenticated, HasPerm(PERM, PLATFORM, gettext_lazy("Only Super Admins can do this."))]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Import important numbers from CSV",
        parameters=[OpenApiParameter("dry_run", OpenApiTypes.BOOL)],
        request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
        responses=ImportReportSerializer,
    )
    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ApiError("validation_error", _("Attach a CSV file."), fields={"file": [_("Required.")]})
        if upload.size > settings.CONTACTS_IMPORT_MAX_BYTES:
            raise ApiError("file_too_large", _("The file is too large."), status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        dry_run = request.query_params.get("dry_run") in ("1", "true", "yes")
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError("validation_error", _("Save the file as UTF-8 CSV."), fields={"file": [_("Not UTF-8.")]}) from None
        report = services.import_contacts(request.user, text, dry_run=dry_run, via=client_via(request))
        body = {"dry_run": dry_run, **report.as_dict()}
        if not report.ok:
            raise ApiError("validation_error", _("Nothing was imported: fix the rows listed."), report=body)
        return Response(body)


@extend_schema_view(list=extend_schema(summary="Contact types, with how many active numbers each has"))
class AdminContactTypeViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [
        IsAuthenticated, HasPerm(services.TYPES_PERM, PLATFORM, gettext_lazy("Only Super Admins can do this.")),
    ]
    serializer_class = AdminContactTypeSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return ContactType.objects.live().annotate(
            contact_count=Count("contacts", filter=Q(contacts__is_active=True))
        ).order_by("section", "sort_order", "id")

    @extend_schema(summary="Add a contact type", responses={201: AdminContactTypeSerializer})
    def create(self, request):
        data = AdminContactTypeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        contact_type = services.create_type(request.user, data.validated_data, via=client_via(request))
        return Response(AdminContactTypeSerializer(contact_type).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Rename, reorder or switch off a contact type (key and section stay)")
    def partial_update(self, request, pk=None):
        contact_type = self.get_queryset().filter(pk=pk).first()
        if contact_type is None:
            raise ApiError("not_found", _("No such type."), status=status.HTTP_404_NOT_FOUND)
        data = AdminContactTypeSerializer(contact_type, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        contact_type = services.update_type(request.user, contact_type, data.validated_data, via=client_via(request))
        return Response(AdminContactTypeSerializer(contact_type).data)

    @extend_schema(summary="Delete a contact type no active number uses (409 type_in_use otherwise)")
    def destroy(self, request, pk=None):
        services.delete_type(request.user, pk, via=client_via(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
