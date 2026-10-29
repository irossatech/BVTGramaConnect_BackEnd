"""Bills and recharges API: docs/api-contract.md, section 18.

Public (no login):  GET bills/
Super Admin:        admin/bills/ list, create, change, reorder. No delete: hide with is_active.
"""

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import PLATFORM, HasPerm

from . import services
from .models import Bill
from .serializers import AdminBillSerializer, BillOrderSerializer, BillSerializer, BillWriteSerializer

MANAGE_BILLS = HasPerm("bills.manage_bills", PLATFORM, gettext_lazy("Only Super Admins can do this."))


@method_decorator(cache_control(public=True, max_age=settings.SERVICES_PUBLIC_MAX_AGE), name="dispatch")
class PublicBillsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(summary="The bills and recharges on Home, in order", responses=BillSerializer(many=True))
    def get(self, request):
        bills = Bill.objects.filter(is_active=True).order_by("order", "id")
        return Response(BillSerializer(bills, many=True, context={"request": request}).data)


def _all_bills(request):
    return AdminBillSerializer(Bill.objects.order_by("order", "id"), many=True, context={"request": request}).data


def _out(request, bill, code=status.HTTP_200_OK):
    return Response(AdminBillSerializer(bill, context={"request": request}).data, status=code)


def _logo_removed(request) -> bool:
    """`"logo": null` in JSON, or an empty `logo` field in a form, takes the logo away."""
    return "logo" in request.data and not request.FILES.get("logo") and request.data.get("logo") in (None, "")


class AdminBillsView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_BILLS]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(summary="Every bill, hidden ones too, in Home order", responses=AdminBillSerializer(many=True))
    def get(self, request):
        return Response(_all_bills(request))

    @extend_schema(summary="Add a bill (JSON, or multipart with a `logo` image); the server makes its key and "
                           "puts it last", request=BillWriteSerializer, responses={201: AdminBillSerializer})
    def post(self, request):
        data = BillWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        bill = services.create_bill(request.user, data.validated_data, request.FILES.get("logo"))
        return _out(request, bill, status.HTTP_201_CREATED)


class AdminBillDetailView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_BILLS]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(summary="Change, hide or show a bill; multipart with `logo` replaces the logo, "
                           '`"logo": null` removes it', request=BillWriteSerializer, responses=AdminBillSerializer)
    def patch(self, request, key):
        bill = get_object_or_404(Bill, key=key)
        data = BillWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        bill = services.update_bill(
            request.user, bill, data.validated_data, request.FILES.get("logo"), remove_logo=_logo_removed(request)
        )
        return _out(request, bill)


class AdminBillOrderView(APIView):
    permission_classes = [IsAuthenticated, MANAGE_BILLS]

    @extend_schema(summary="Put the bills in a new order (every bill key, once)", request=BillOrderSerializer,
                   responses=AdminBillSerializer(many=True))
    def post(self, request):
        data = BillOrderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.reorder_bills(request.user, data.validated_data["keys"])
        return Response(_all_bills(request))
