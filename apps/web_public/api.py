"""Public website API, docs/api-contract.md section 9.

- GET  /public/site-config/        contact details, store links, website link, help lines
- GET  /public/showcase/           live posts for the home page's phone picture
- POST /public/contact-messages/   the Contact page form
- GET/PATCH /admin/contact-messages/  Super Admins read and close them in the console
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import MobileField
from apps.audit.services import record
from apps.feed.serializers import FeedItemSerializer
from apps.feed.views import describer_with, published_posts, targets_for_village
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Village
from apps.posts.views import IsSuperAdmin
from grama.exceptions import ApiError
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
    website_url = serializers.CharField()
    helplines = serializers.ListField(child=serializers.DictField())


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
                "website_url": settings.SITE_WEBSITE_URL,
                "helplines": settings.SITE_HELPLINES,
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


# --- Showcase: live posts for the home page's phone picture (owner decision Q16) ---------


@method_decorator(cache_control(public=True, max_age=60), name="dispatch")
class ShowcaseView(PublicView):
    @extend_schema(
        summary="Live posts for the home page's phone picture",
        responses=inline_serializer(
            "Showcase",
            {
                "location": serializers.DictField(allow_null=True),
                "featured": FeedItemSerializer(allow_null=True),
                "items": FeedItemSerializer(many=True),
            },
        ),
    )
    def get(self, request):
        location = None
        posts = []
        village_id = settings.SITE_SHOWCASE_VILLAGE_ID
        if village_id and Village.objects.active().filter(id=village_id).exists():
            location = AreaRef(AreaLevel.VILLAGE, village_id)
            posts = list(published_posts().filter(targets_for_village(village_id)).order_by("-published_at", "-id")[:10])
        if not posts:
            location = None
            posts = list(published_posts().order_by("-published_at", "-id")[:10])
        with_photo = [post for post in posts if post.thumbnail_name]
        featured = with_photo[0] if with_photo else (posts[0] if posts else None)
        items = [post for post in posts if post is not featured][:2]
        if featured is not None and location is None:
            location = featured.target
        shown = [post for post in [featured, *items] if post is not None]
        describer = describer_with(shown)
        if location is not None:
            describer.prime([location])
        place = describer.describe(location) if location is not None else None
        context = {"request": request, "describer": describer}
        return Response(
            {
                "location": {key: place[key] for key in ("level", "id", "name")} if place else None,
                "featured": FeedItemSerializer(featured, context=context).data if featured else None,
                "items": FeedItemSerializer(items, many=True, context=context).data,
            }
        )


# --- Contact messages in the console (owner decision Q16) ------------------------------


class ContactMessageAdminSerializer(serializers.ModelSerializer):
    handled_by = serializers.SerializerMethodField()

    class Meta:
        model = ContactMessage
        fields = [
            "id", "name", "mobile", "village_text", "message", "language",
            "created_at", "handled", "handled_at", "handled_by",
        ]

    def get_handled_by(self, obj) -> dict | None:
        if obj.handled_by_id is None:
            return None
        return {"id": str(obj.handled_by_id), "name": obj.handled_by.name}


class ContactHandledSerializer(serializers.Serializer):
    handled = serializers.BooleanField()


class ContactMessageListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = ContactMessageAdminSerializer

    def get_queryset(self):
        queryset = ContactMessage.objects.select_related("handled_by").order_by("-created_at", "-id")
        wanted = self.request.query_params.get("status", "").strip().lower()
        if wanted == "new":
            queryset = queryset.filter(handled=False)
        elif wanted == "handled":
            queryset = queryset.filter(handled=True)
        elif wanted:
            raise ApiError("validation_error", _("Status is new or handled."), fields={"status": ["new", "handled"]})
        query = self.request.query_params.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(mobile__contains=query)
                | Q(village_text__icontains=query) | Q(message__icontains=query)
            )
        return queryset

    @extend_schema(summary="Website contact messages, newest first")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ContactMessageDetailView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(summary="Mark a contact message handled, or open it again", request=ContactHandledSerializer, responses=ContactMessageAdminSerializer)
    def patch(self, request, pk):
        message = ContactMessage.objects.select_related("handled_by").filter(pk=pk).first()
        if message is None:
            raise ApiError("not_found", _("No such message."), status=404)
        data = ContactHandledSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        handled = data.validated_data["handled"]
        if message.handled != handled:
            message.handled = handled
            message.handled_at = timezone.now() if handled else None
            message.handled_by = request.user if handled else None
            message.save(update_fields=["handled", "handled_at", "handled_by"])
            record(
                "contact.handled" if handled else "contact.reopened",
                actor=request.user,
                actor_role="SUPER_ADMIN",
                target=message,
                target_label=f"#{message.id}",  # never the sender's name or number
                via="console",
            )
        return Response(ContactMessageAdminSerializer(message).data)

