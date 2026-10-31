"""Home banner ads API: docs/api-contract.md, section 17.

GET /api/v1/ads/?village_id=12   the banners for that village's Home (public, no login)
/api/v1/admin/ads/               the Super Admin's list, create, change and delete
"""

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import PLATFORM, HasPerm
from apps.geography.models import Village
from grama.exceptions import ApiError

from . import services
from .models import Ad
from .serializers import AdminAdSerializer, AdWriteSerializer, PublicAdSerializer


def village_from(request) -> int:
    """The same answers as /contacts/: 400 without a number, 404 for an unknown or inactive village."""
    raw = request.query_params.get("village_id", "").strip()
    if not raw.isdigit():
        raise ApiError("validation_error", _("Give a village id."), fields={"village_id": [_("Give a village id.")]})
    village_id = int(raw)
    if not Village.objects.active().filter(id=village_id).exists():
        raise ApiError("not_found", _("No such village."), status=status.HTTP_404_NOT_FOUND)
    return village_id


@method_decorator(cache_control(public=True, max_age=settings.ADS_PUBLIC_MAX_AGE), name="dispatch")
class PublicAdsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="Banner ads for a village's Home, newest first",
        parameters=[OpenApiParameter("village_id", OpenApiTypes.INT, required=True)],
        responses=PublicAdSerializer(many=True),
    )
    def get(self, request):
        ads = services.for_village(village_from(request))
        return Response(PublicAdSerializer(ads, many=True, context={"request": request}).data)


class AdminAdViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    permission_classes = [IsAuthenticated, HasPerm("ads.manage_ad", PLATFORM, gettext_lazy("Only Super Admins can do this."))]
    serializer_class = AdminAdSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = Ad.objects.order_by("-created_at", "-id")

    def get_object(self) -> Ad:
        return get_object_or_404(Ad, pk=self.kwargs["pk"])

    def _out(self, ad: Ad, code=status.HTTP_200_OK):
        return Response(AdminAdSerializer(ad, context=self.get_serializer_context()).data, status=code)

    def _files(self, request, data) -> dict:
        return {
            "image": request.FILES.get("image"),
            "video": request.FILES.get("video"),
            "duration_s": data.validated_data.pop("duration_s", None),
        }

    @extend_schema(summary="New ad (multipart, with the banner image or video)",
                   request={"multipart/form-data": AdWriteSerializer}, responses={201: AdminAdSerializer})
    def create(self, request):
        data = AdWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ad = services.create(request.user, data.validated_data, **self._files(request, data))
        return self._out(ad, status.HTTP_201_CREATED)

    @extend_schema(summary="Change an ad: multipart with a new image or video, or JSON with only the details",
                   request=AdWriteSerializer, responses=AdminAdSerializer)
    def partial_update(self, request, pk=None):
        ad = self.get_object()
        data = AdWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        return self._out(services.update(request.user, ad, data.validated_data, **self._files(request, data)))

    @extend_schema(summary="Delete an ad, its image and its video", responses={204: None})
    def destroy(self, request, pk=None):
        services.delete(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
