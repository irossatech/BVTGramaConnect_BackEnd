"""Public website API: site config and contact messages. docs/api-contract.md, section 9."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import MobileField
from grama.http import client_ip

from .models import ContactMessage


class SiteConfigSerializer(serializers.Serializer):
    office_address_te = serializers.CharField()
    office_address_en = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.CharField()
    hours_te = serializers.CharField()
    hours_en = serializers.CharField()
    play_store_url = serializers.CharField(allow_blank=True)
    app_store_url = serializers.CharField(allow_blank=True)


class ContactMessageSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    mobile = MobileField()
    village_text = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=2000)
    language = serializers.ChoiceField(choices=[("te", "te"), ("en", "en")], default="te")
    #: Honeypot. The website keeps this hidden and empty; bots fill it in.
    website = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Enter your name."))
        return value

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("Enter a message."))
        return value


class PublicView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []


@method_decorator(cache_control(public=True, max_age=300), name="dispatch")
class SiteConfigView(PublicView):
    @extend_schema(summary="Contact details and store links for the public website", responses=SiteConfigSerializer)
    def get(self, request):
        return Response(
            {
                "office_address_te": settings.SITE_OFFICE_ADDRESS_TE,
                "office_address_en": settings.SITE_OFFICE_ADDRESS_EN,
                "phone": settings.SITE_PHONE,
                "email": settings.SITE_EMAIL,
                "hours_te": settings.SITE_HOURS_TE,
                "hours_en": settings.SITE_HOURS_EN,
                "play_store_url": settings.PLAY_STORE_URL,
                "app_store_url": settings.APP_STORE_URL,
            }
        )


class ContactMessageView(PublicView):
    @extend_schema(
        summary="Send a message from the website's Contact page",
        request=ContactMessageSerializer,
        responses={201: inline_serializer("ContactMessageCreated", {"id": serializers.IntegerField(), "created_at": serializers.DateTimeField()})},
    )
    def post(self, request):
        data = ContactMessageSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        ip = client_ip(request)

        limit = settings.CONTACT_MESSAGES_PER_IP_PER_HOUR
        if ip:
            hour_ago = timezone.now() - timedelta(hours=1)
            recent = list(
                ContactMessage.objects.filter(ip_address=ip, created_at__gt=hour_ago)
                .order_by("-created_at")
                .values_list("created_at", flat=True)[:limit]
            )
            if len(recent) >= limit:
                wait = int((recent[-1] + timedelta(hours=1) - timezone.now()).total_seconds()) + 1
                raise Throttled(wait=max(wait, 1))

        if values["website"]:
            # Pretend success so bots learn nothing; store nothing.
            return Response({"id": 0, "created_at": timezone.now()}, status=status.HTTP_201_CREATED)

        message = ContactMessage.objects.create(
            name=values["name"],
            mobile=values["mobile"],
            village_text=values["village_text"].strip(),
            message=values["message"],
            language=values["language"],
            ip_address=ip,
        )
        return Response({"id": message.id, "created_at": message.created_at}, status=status.HTTP_201_CREATED)
