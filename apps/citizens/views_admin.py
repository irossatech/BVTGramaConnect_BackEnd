"""The Super Admin's blocked citizens: docs/api-contract.md, section 15 (owner's request of 2026-11-21).

GET  /api/v1/admin/citizens/blocked/        one row per blocked phone (?active=1 now, 0 ended), with counts
GET  /api/v1/admin/citizens/{id}/blocks/    every block of that phone, newest first
POST /api/v1/admin/citizens/{id}/unblock/   lift the phone's block at once, without a post

`id` is any install of the phone; the list gives its first install. Nothing here names a phone number,
IP address or Android ID. Only posts.block_citizen (the Super Admin) may call these.
"""

from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import PLATFORM, HasPerm
from apps.geography.models import Village
from apps.posts.services import BLOCK_CITIZEN
from grama.exceptions import ApiError
from grama.pagination import PageNumberPagination
from grama.params import is_number, uuid_or_none

from . import services
from .models import CitizenBlock
from .serializers import BlockedCitizenSerializer, CitizenBlockSerializer, CitizenDeviceSerializer

SUPER_ADMIN_ONLY = HasPerm(BLOCK_CITIZEN, PLATFORM, gettext_lazy("Only the Super Admin can block citizens or discard all their posts."))


class AdminCitizenView(APIView):
    permission_classes = [IsAuthenticated, SUPER_ADMIN_ONLY]


def _device(pk):
    return services.device_or_404(uuid_or_none(str(pk)))


class BlockedCitizensView(AdminCitizenView):
    @extend_schema(
        summary="Super Admin: blocked citizens, one row per phone",
        parameters=[
            OpenApiParameter("active", OpenApiTypes.STR, enum=["1", "0"], description="1 blocked now (default), 0 block ended"),
            OpenApiParameter("district", OpenApiTypes.INT),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses=BlockedCitizenSerializer(many=True),
    )
    def get(self, request):
        params = request.query_params
        active = params.get("active", "1").strip()
        if active not in ("1", "0"):
            raise ApiError("validation_error", _("active is 1 or 0."), fields={"active": [_("1 or 0")]})
        now = timezone.now()
        rows = services.latest_blocks()
        district = params.get("district", "").strip()
        if district:
            if not is_number(district):
                raise ApiError("validation_error", _("Give a district id."), fields={"district": [_("Not a district id.")]})
            rows = rows.filter(village_id__in=Village.objects.filter(mandal__district_id=int(district)).values("id"))
        running = services.running_q(now)
        counted = rows.order_by().aggregate(active=Count("pk", filter=running), ended=Count("pk", filter=~running))
        counts = {"active": counted["active"] or 0, "ended": counted["ended"] or 0}
        chosen = rows.filter(running) if active == "1" else rows.exclude(running)
        chosen = chosen.select_related("blocked_by", "ended_by").order_by("-blocked_at", "-pk")
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(chosen, request, view=self)
        context = {"now": now, **_page_context(page)}
        response = paginator.get_paginated_response(BlockedCitizenSerializer(page, many=True, context=context).data)
        response.data["counts"] = counts
        return response


def _page_context(rows) -> dict:
    """The phones' first installs, post counts and newest posts for one page, in a few queries."""
    phones = {row.phone for row in rows}
    anchors = services.phone_anchors(phones)
    installs = services.phone_installs(phones)
    records = services.phone_records(list(anchors.values()))
    return {
        "anchors": anchors,
        "records": {key: records.get(device.pk) for key, device in anchors.items()},
        "latest_posts": services.latest_posts(installs),
    }


class CitizenBlocksView(AdminCitizenView):
    @extend_schema(summary="Super Admin: every block of one phone, newest first", responses=CitizenBlockSerializer(many=True))
    def get(self, request, pk):
        device = _device(pk)
        blocks = (
            CitizenBlock.objects.filter(phone=services.phone_key(device))
            .select_related("blocked_by", "ended_by")
            .order_by("-blocked_at", "-pk")
        )
        return Response(CitizenBlockSerializer(blocks, many=True, context={"now": timezone.now()}).data)


class UnblockCitizenView(AdminCitizenView):
    @extend_schema(summary="Super Admin: lift a phone's block at once", request=None, responses=CitizenDeviceSerializer)
    def post(self, request, pk):
        device = services.unblock_phone(request.user, _device(pk))
        return Response(CitizenDeviceSerializer(device).data)
