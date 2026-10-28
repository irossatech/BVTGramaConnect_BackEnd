"""Services and service requests API: docs/api-contract.md, section 16.

Public (no login):          GET services/
Citizens (X-Citizen-Token): create and list their own requests.
Officials (JWT or session): list, read and change the requests they may see.
Super Admin:                admin/services/ list, create, change, reorder.
"""

from django.conf import settings
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import PLATFORM, HasPerm
from apps.citizens import services as citizens
from apps.citizens.views import TOKEN_PARAM, CitizenView
from grama.exceptions import ApiError

from . import selectors, services
from .models import RequestStatus, Service, ServiceRequest, ServiceRequestEvent
from .serializers import (
    AdminServiceSerializer,
    CitizenServiceRequestSerializer,
    CitizenServiceRequestWriteSerializer,
    ServiceOrderSerializer,
    ServiceRequestSerializer,
    ServiceRequestUpdateSerializer,
    ServiceSerializer,
    ServiceWriteSerializer,
)

MY_REQUESTS_LIMIT = 50


def with_related(queryset):
    return queryset.select_related("service", "village__mandal__district", "assigned_to", "device").prefetch_related(
        Prefetch("history", queryset=ServiceRequestEvent.objects.select_related("by", "assigned_to"))
    )


# --- Public and citizens ---------------------------------------------------------------------------


@method_decorator(cache_control(public=True, max_age=settings.SERVICES_PUBLIC_MAX_AGE), name="dispatch")
class PublicServicesView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(summary="The services citizens can ask for, in Home order", responses=ServiceSerializer(many=True))
    def get(self, request):
        return Response(ServiceSerializer(selectors.active_services(), many=True, context={"request": request}).data)


class CitizenServiceRequestsView(CitizenView):
    @extend_schema(
        summary="This phone's service requests, newest first",
        parameters=TOKEN_PARAM,
        responses=CitizenServiceRequestSerializer(many=True),
    )
    def get(self, request):
        device = citizens.device_from_request(request)
        rows = with_related(ServiceRequest.objects.filter(device__in=citizens.same_phone(device))).order_by("-created_at", "-id")
        return Response(CitizenServiceRequestSerializer(rows[:MY_REQUESTS_LIMIT], many=True).data)

    @extend_schema(
        summary="Ask the officials of your village for help with a service",
        parameters=TOKEN_PARAM,
        request=CitizenServiceRequestWriteSerializer,
        responses={201: CitizenServiceRequestSerializer},
    )
    def post(self, request):
        device = citizens.device_from_request(request)
        data = CitizenServiceRequestWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        created = services.create_request(device, **data.validated_data)
        created = with_related(ServiceRequest.objects.filter(pk=created.pk)).get()
        return Response(CitizenServiceRequestSerializer(created).data, status=status.HTTP_201_CREATED)


# --- Officials --------------------------------------------------------------------------------------

VIEW_PERM = HasPerm(selectors.VIEW, message=gettext_lazy("Your role cannot see service requests."))


class ServiceRequestListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, VIEW_PERM]
    serializer_class = ServiceRequestSerializer

    @extend_schema(
        summary="Service requests you may see, newest change first, with counts per status",
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=RequestStatus.values),
            OpenApiParameter("assigned", OpenApiTypes.STR, enum=["me"], description="me: only those assigned to you"),
            OpenApiParameter("q", OpenApiTypes.STR, description="The citizen's name or phone"),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def _filtered_except_status(self):
        params = self.request.query_params
        queryset = selectors.visible_requests(self.request.user)
        if params.get("assigned") == "me":
            queryset = queryset.filter(assigned_to=self.request.user)
        query = " ".join(params.get("q", "").split())
        if query:
            digits = "".join(ch for ch in query if ch.isdigit())
            match = Q(name__icontains=query)
            if digits:
                match |= Q(phone__contains=digits)
            queryset = queryset.filter(match)
        return queryset

    def get_queryset(self):
        queryset = self._filtered_except_status()
        wanted = self.request.query_params.get("status", "").strip().upper()
        if wanted:
            if wanted not in RequestStatus.values:
                raise ApiError("validation_error", _("Unknown status."), fields={"status": RequestStatus.values})
            queryset = queryset.filter(status=wanted)
        return with_related(queryset).order_by("-updated_at", "-id")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        counts = {
            row["status"]: row["n"]
            for row in self._filtered_except_status().order_by().values("status").annotate(n=Count("id"))
        }
        response.data["status_counts"] = {value: counts.get(value, 0) for value in RequestStatus.values}
        return response


class ServiceRequestDetailView(APIView):
    permission_classes = [IsAuthenticated, VIEW_PERM]

    def get_object(self, pk) -> ServiceRequest:
        # Outside the official's reach answers 404, so its existence stays hidden.
        return get_object_or_404(with_related(selectors.visible_requests(self.request.user)), pk=pk)

    def _out(self, pk):
        return Response(ServiceRequestSerializer(self.get_object(pk), context={"request": self.request}).data)

    @extend_schema(summary="One service request with its history", responses=ServiceRequestSerializer)
    def get(self, request, pk):
        return self._out(pk)

    @extend_schema(
        summary="Change the status, assign, or write a note to the citizen",
        request=ServiceRequestUpdateSerializer,
        responses=ServiceRequestSerializer,
    )
    def patch(self, request, pk):
        target = self.get_object(pk)
        data = ServiceRequestUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        services.update_request(
            request.user,
            target,
            status=values.get("status"),
            assigned_to=values["assigned_to"] if "assigned_to" in values else services.UNSET,
            note=values.get("note"),
        )
        # Not through visible_requests again: a Panchayat member who passed it on no longer sees it,
        # but still gets the saved request (with no allowed_actions) instead of a 404.
        saved = with_related(ServiceRequest.objects.all()).get(pk=target.pk)
        return Response(ServiceRequestSerializer(saved, context={"request": request}).data)


# --- Super Admin: the services list -------------------------------------------------------------------

MANAGE_SERVICES = HasPerm("service_requests.manage_services", PLATFORM, gettext_lazy("Only Super Admins can do this."))


def _all_services(request):
    return AdminServiceSerializer(selectors.all_services(), many=True, context={"request": request}).data


def _out(request, service, code=status.HTTP_200_OK):
    return Response(AdminServiceSerializer(service, context={"request": request}).data, status=code)


def _logo_removed(request) -> bool:
    """`"logo": null` in JSON, or an empty `logo` field in a form, takes the logo away."""
    return "logo" in request.data and not request.FILES.get("logo") and request.data.get("logo") in (None, "")


class AdminServicesView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_SERVICES]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(summary="Every service, hidden ones too, in Home order", responses=AdminServiceSerializer(many=True))
    def get(self, request):
        return Response(_all_services(request))

    @extend_schema(summary="Add a service (JSON, or multipart with a `logo` image); the server makes its key and "
                           "puts it last", request=ServiceWriteSerializer, responses={201: AdminServiceSerializer})
    def post(self, request):
        data = ServiceWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        service = services.create_service(request.user, data.validated_data, request.FILES.get("logo"))
        return _out(request, service, status.HTTP_201_CREATED)


class AdminServiceDetailView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_SERVICES]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(summary="Change, hide or show a service; multipart with `logo` replaces the logo, "
                           '`"logo": null` removes it', request=ServiceWriteSerializer, responses=AdminServiceSerializer)
    def patch(self, request, key):
        service = get_object_or_404(Service, key=key)
        data = ServiceWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        service = services.update_service(
            request.user, service, data.validated_data, request.FILES.get("logo"), remove_logo=_logo_removed(request)
        )
        return _out(request, service)


class AdminServiceOrderView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_SERVICES]

    @extend_schema(summary="Put the services in a new order (every key, once)", request=ServiceOrderSerializer,
                   responses=AdminServiceSerializer(many=True))
    def post(self, request):
        data = ServiceOrderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.reorder_services(request.user, data.validated_data["keys"])
        return Response(_all_services(request))
