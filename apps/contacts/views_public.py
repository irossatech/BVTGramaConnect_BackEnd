"""Important numbers for citizens: no login (docs/api-contract.md, section 14).

GET /api/v1/contacts/?village_id=12   the whole Contacts tab in one call
GET /api/v1/contact-types/            the type chips and their names
"""

from django.conf import settings
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel, Village
from grama.exceptions import ApiError

from .models import ContactSection, ContactType
from .selectors import contacts_for_village, grouped
from .serializers import CitizenContactsSerializer, PublicContactTypeSerializer


class PublicView(APIView):
    """No login; any Authorization header is ignored, so a stale token on a phone never breaks it."""

    permission_classes = [AllowAny]
    authentication_classes: list = []


def village_from(request) -> int:
    raw = request.query_params.get("village_id", "").strip()
    if not raw.isdigit():
        raise ApiError("validation_error", _("Give a village id."), fields={"village_id": [_("Give a village id.")]})
    village_id = int(raw)
    if not Village.objects.active().filter(id=village_id).exists():
        raise ApiError("not_found", _("No such village."), status=404)
    return village_id


@method_decorator(cache_control(public=True, max_age=settings.CONTACTS_PUBLIC_MAX_AGE), name="dispatch")
class CitizenContactsView(PublicView):
    @extend_schema(
        summary="Important numbers for one village: 'For you' and 'Government helplines'",
        parameters=[OpenApiParameter("village_id", OpenApiTypes.INT, required=True)],
        responses=CitizenContactsSerializer,
    )
    def get(self, request):
        village_id = village_from(request)
        rows = contacts_for_village(village_id)
        village = AreaRef(AreaLevel.VILLAGE, village_id)
        describer = AreaDescriber()
        describer.prime([village, *(row.area for row in rows)])
        body = {
            "village": describer.describe(village),
            "for_you": grouped([row for row in rows if row.type.section == ContactSection.AREA]),
            "helplines": grouped([row for row in rows if row.type.section == ContactSection.HELPLINE]),
            "updated_at": max((row.updated_at for row in rows), default=None),
        }
        return Response(CitizenContactsSerializer(body, context={"describer": describer}).data)


@method_decorator(cache_control(public=True, max_age=settings.CONTACTS_PUBLIC_MAX_AGE), name="dispatch")
class ContactTypeListView(PublicView):
    @extend_schema(summary="Contact types (active)", responses=PublicContactTypeSerializer(many=True))
    def get(self, request):
        types = ContactType.objects.live().filter(is_active=True).order_by("section", "sort_order", "id")
        return Response(PublicContactTypeSerializer(types, many=True).data)
