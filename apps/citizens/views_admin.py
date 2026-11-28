"""The Super Admin's blocked citizens: docs/api-contract.md, section 15 (owner's request of 2026-11-21).

GET  /api/v1/admin/citizens/blocked/        one row per blocked phone (?active=1 now, 0 ended), with counts
GET  /api/v1/admin/citizens/{id}/blocks/    every block of that phone, newest first
POST /api/v1/admin/citizens/{id}/unblock/   lift the phone's block at once, without a post

`id` is any install of the phone; the list gives its first install. Nothing here names a phone number,
IP address or Android ID. Only posts.block_citizen (the Super Admin) may call these.

Citizen wallet accounts (BVTGC-128, contract section 21, its Super Admin table), for accounts.manage_citizen_accounts:
GET  /api/v1/admin/citizen-accounts/                       the list: ?q (name, last 4 digits, referral code),
                                                           ?village_id, ?status (active, blocked, deleted)
POST /api/v1/admin/citizen-accounts/find/                  {mobile} or {email} a caller gives; audited
POST /api/v1/admin/citizen-accounts/{id}/email/            {email, reason, called_back: true}
POST /api/v1/admin/citizen-accounts/{id}/release-mobile/   {reason, called_back: true}
POST /api/v1/admin/citizen-accounts/{id}/block/, /unblock/, /delete/   {reason}
"""

from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import authz
from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.geography.models import Village
from apps.posts.services import BLOCK_CITIZEN
from grama.exceptions import ApiError
from grama.pagination import PageNumberPagination
from grama.params import is_number, uuid_or_none

from . import services, services_admin
from .models import CitizenBlock
from .serializers import BlockedCitizenSerializer, CitizenBlockSerializer, CitizenDeviceSerializer
from .serializers_account import (
    CalledBackSerializer,
    CitizenAccountRowSerializer,
    FindAccountSerializer,
    ReasonSerializer,
    SetEmailSerializer,
)

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


# --- Citizen wallet accounts (BVTGC-128) ---------------------------------------------------------------------

MANAGE_ACCOUNTS = "accounts.manage_citizen_accounts"


class AdminAccountsView(APIView):
    """Works while the wallet is off too: deletions and help may still be needed after it is switched off."""

    permission_classes = [
        IsAuthenticated,
        HasPerm(MANAGE_ACCOUNTS, PLATFORM, gettext_lazy("Only the Super Admin can manage citizen wallet accounts.")),
    ]

    def rows(self, accounts) -> list:
        context = {"can_adjust": authz.can(self.request.user, "wallet.adjust_points", PLATFORM)}
        CitizenAccountRowSerializer.prime(accounts, context)
        return CitizenAccountRowSerializer(accounts, many=True, context=context).data

    def row(self, account) -> Response:
        return Response(self.rows([account])[0])


class CitizenAccountsView(AdminAccountsView):
    @extend_schema(
        summary="Super Admin: citizen wallet accounts, newest first",
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="A name, the last 4 digits of the number, or a referral code"),
            OpenApiParameter("village_id", OpenApiTypes.INT),
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(services_admin.STATUSES)),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses=CitizenAccountRowSerializer(many=True),
    )
    def get(self, request):
        params = request.query_params
        rows = services_admin.search(services_admin.accounts(), params.get("q", ""))
        village = params.get("village_id", "").strip()
        if village:
            if not is_number(village):
                raise ApiError("validation_error", _("Give a village id."), fields={"village_id": [_("Not a village id.")]})
            rows = rows.filter(village_id=int(village))
        chosen = params.get("status", "").strip()
        if chosen:
            if chosen not in services_admin.STATUSES:
                message = _("status is active, blocked or deleted.")
                raise ApiError("validation_error", message, fields={"status": [message]})
            rows = rows.filter(services_admin.status_q(chosen))
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(rows.order_by("-created_at", "-pk"), request, view=self)
        return paginator.get_paginated_response(self.rows(page))


class FindCitizenAccountView(AdminAccountsView):
    @extend_schema(
        summary="Super Admin: the account with the full number or email a caller gives (a POST: nothing personal in the URL)",
        request=FindAccountSerializer,
        responses=inline_serializer("FoundAccounts", {"results": CitizenAccountRowSerializer(many=True)}),
    )
    def post(self, request):
        data = FindAccountSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        found = services_admin.find(
            mobile=(data.validated_data.get("mobile") or "").strip(),
            email=(data.validated_data.get("email") or "").strip(),
            by=request.user,
            via=client_via(request),
        )
        return Response({"results": self.rows(found)})


class SetAccountEmailView(AdminAccountsView):
    @extend_schema(
        summary="Super Admin, after a call-back: a new email, which the citizen confirms through Forgot password",
        request=SetEmailSerializer,
        responses=CitizenAccountRowSerializer,
    )
    def post(self, request, pk):
        account = services_admin.account_or_404(pk)
        data = SetEmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self.row(services_admin.set_email(
            account, data.validated_data["email"], reason=data.validated_data["reason"], by=request.user,
            via=client_via(request),
        ))


class ReleaseAccountMobileView(AdminAccountsView):
    @extend_schema(
        summary="Super Admin, after a call-back: remove the number from this account",
        request=CalledBackSerializer,
        responses=CitizenAccountRowSerializer,
    )
    def post(self, request, pk):
        account = services_admin.account_or_404(pk)
        data = CalledBackSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self.row(services_admin.release_mobile(
            account, reason=data.validated_data["reason"], by=request.user, via=client_via(request)
        ))


class BlockAccountView(AdminAccountsView):
    @extend_schema(summary="Super Admin: block a citizen account; every session ends", request=ReasonSerializer,
                   responses=CitizenAccountRowSerializer)
    def post(self, request, pk):
        account = services_admin.account_or_404(pk)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self.row(services_admin.block(
            account, reason=data.validated_data["reason"], by=request.user, via=client_via(request)
        ))


class UnblockAccountView(AdminAccountsView):
    @extend_schema(summary="Super Admin: unblock a citizen account", request=ReasonSerializer,
                   responses=CitizenAccountRowSerializer)
    def post(self, request, pk):
        account = services_admin.account_or_404(pk)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self.row(services_admin.unblock(
            account, reason=data.validated_data["reason"], by=request.user, via=client_via(request)
        ))


class DeleteAccountView(AdminAccountsView):
    @extend_schema(summary="Super Admin: delete a citizen account (for example fraud or a legal request)",
                   request=ReasonSerializer, responses=CitizenAccountRowSerializer)
    def post(self, request, pk):
        account = services_admin.account_or_404(pk)
        data = ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self.row(services_admin.delete(account, reason=data.validated_data["reason"], by=request.user))
