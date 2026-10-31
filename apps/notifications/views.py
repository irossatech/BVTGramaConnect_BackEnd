"""Devices, the officials' inbox and notification texts. docs/api-contract.md, section 7."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.audit.services import record
from grama.exceptions import ApiError, permission_denied

from .models import Device, Notification, NotificationTemplate
from .serializers import (
    DeviceSerializer,
    DeviceUpdateSerializer,
    NotificationSerializer,
    NotificationTemplateSerializer,
    NotificationTemplateUpdateSerializer,
)


# --- Devices -------------------------------------------------------------------------


def _user_or_none(request):
    return request.user if request.user and request.user.is_authenticated else None


class DeviceRegisterView(APIView):
    """Citizens register without login; a bearer token links the device to the official."""

    permission_classes = [AllowAny]

    @extend_schema(summary="Register or refresh this device for push", request=DeviceSerializer, responses=DeviceSerializer)
    def post(self, request):
        data = DeviceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        token = values.pop("fcm_token")
        values["user"] = _user_or_none(request)
        if values["user"] is None:
            # Without a login, a known token never unlinks an official (G8): the
            # app's logout deletes the device first. The row is left as it is.
            linked = Device.objects.filter(fcm_token=token, user__isnull=False).first()
            if linked:
                return Response(DeviceSerializer(linked).data, status=status.HTTP_200_OK)
        device, created = Device.objects.update_or_create(fcm_token=token, defaults=values)
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DeviceDetailView(APIView):
    """The device id is known only to the app that registered it."""

    permission_classes = [AllowAny]

    def get_device(self, request, pk) -> Device:
        device = get_object_or_404(Device, pk=pk)
        user = _user_or_none(request)
        if device.user_id and (user is None or user.pk != device.user_id):
            raise ApiError("not_found", "No such device.", status=404)
        return device

    @extend_schema(summary="Change village, language or token", request=DeviceUpdateSerializer, responses=DeviceSerializer)
    def patch(self, request, pk):
        device = self.get_device(request, pk)
        data = DeviceUpdateSerializer(device, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        token = data.validated_data.get("fcm_token")
        if token and token != device.fcm_token:
            Device.objects.filter(fcm_token=token).exclude(pk=device.pk).delete()
        data.save()
        return Response(DeviceSerializer(device).data)

    @extend_schema(summary="Forget this device (logout or opt-out)", responses={204: None})
    def delete(self, request, pk):
        self.get_device(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Inbox -------------------------------------------------------------------------------


class InboxPagination(CursorPagination):
    ordering = ("-created_at", "-id")
    page_size = 20


class NotificationListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    pagination_class = InboxPagination

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") in ("1", "true"):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(
        summary="Your notifications",
        parameters=[OpenApiParameter("unread", OpenApiTypes.BOOL), OpenApiParameter("cursor", OpenApiTypes.STR)],
    )
    def get(self, request, *args, **kwargs):
        response = self.list(request, *args, **kwargs)
        response.data["unread_count"] = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return response


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark one notification read", request=None, responses={204: None})
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark every notification read", request=None, responses={204: None})
    def post(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Notification texts (console Settings page) ----------------------------------------------


CAN_EDIT_TEMPLATES = HasPerm("notifications.manage_templates", PLATFORM, "Only Super Admins can change notification texts.")


class NotificationTemplateListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, CAN_EDIT_TEMPLATES]
    serializer_class = NotificationTemplateSerializer
    pagination_class = None
    queryset = NotificationTemplate.objects.all()


class NotificationTemplateDetailView(APIView):
    permission_classes = [IsAuthenticated, CAN_EDIT_TEMPLATES]

    @extend_schema(
        summary="Change a push text",
        request=NotificationTemplateUpdateSerializer,
        responses=NotificationTemplateSerializer,
    )
    def patch(self, request, key):
        template = get_object_or_404(NotificationTemplate, key=key)
        if not template.editable:
            raise permission_denied("This text cannot be changed.")
        data = NotificationTemplateUpdateSerializer(data=request.data, context={"template": template})
        data.is_valid(raise_exception=True)
        before = {"text_te": template.text_te, "text_en": template.text_en}
        template.text_te = data.validated_data["text_te"]
        template.text_en = data.validated_data["text_en"]
        template.updated_by = request.user
        template.save()
        record(
            "template.updated",
            actor=request.user,
            target=template,
            target_label=template.name_en,
            via=client_via(request),
            data={"before": before},
        )
        return Response(NotificationTemplateSerializer(template).data)
