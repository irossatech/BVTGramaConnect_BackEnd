"""The Super Admin's referrals and wallets (BVTGC-128; requirements J8, FR-29, FR-33, FR-35; contract
section 21, its Super Admin table).

GET  /api/v1/admin/referrals/                       wallet.review_referrals: ?status, ?q, pages, status_counts
POST /api/v1/admin/referrals/{id}/confirm/, /reject/   wallet.review_referrals: {comment}; 409 already_decided
GET  /api/v1/admin/wallets/{account_id}/            wallet.view_wallets: one citizen's balance and history
GET  /api/v1/admin/wallet/entries/                  wallet.view_wallets: every entry, with the export's filters (BVTGC-151)
POST /api/v1/admin/wallets/{account_id}/adjust/     wallet.adjust_points: {points, reason}; 409 not_enough_points
GET  /api/v1/admin/wallet/export/?type=referrals|entries&format=csv   wallet.view_wallets

Today only the Super Admin holds these permissions, over the whole platform. They work while the wallet is
off too. Nothing here shows a full number or email: names, the last 4 digits and masked emails only.
"""

from __future__ import annotations

import io
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import client_via
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.audit.masking import mask_email
from apps.audit.services import record
from apps.audit.views import CSVRenderer
from apps.citizens.models import CitizenAccount
from grama.csvsafe import safe_writer
from grama.exceptions import ApiError
from grama.pagination import PageNumberPagination
from grama.params import uuid_or_none
from grama.renderers import JSONRenderer

from . import services
from .models import Referral, WalletEntry
from .serializers import (
    REASONS,
    AdjustSerializer,
    AdminEntryRowSerializer,
    AdminWalletEntrySerializer,
    CommentSerializer,
    ReferralRowSerializer,
)

REVIEW, VIEW, ADJUST = "wallet.review_referrals", "wallet.view_wallets", "wallet.adjust_points"
#: Never a silently cut file: more rows than this is a 400 (as for the audit export).
EXPORT_MAX_ROWS = 50_000


def only(perm: str, message) -> list:
    return [IsAuthenticated, HasPerm(perm, PLATFORM, message)]


def referrals():
    return Referral.objects.select_related("referrer__user", "referee__user", "decided_by")


def search_referrals(queryset, q: str):
    """Either side's name, the last 4 digits of either number, or either referral code."""
    q = " ".join((q or "").split())
    if not q:
        return queryset
    found = Q()
    for side in ("referrer", "referee"):
        found |= Q(**{f"{side}__user__name__icontains": q}) | Q(**{f"{side}__user__name_te__icontains": q})
        found |= Q(**{f"{side}__referral_code": q.upper()})
        if len(q) == 4 and q.isdecimal():
            found |= Q(**{f"{side}__mobile_last4": q})
    return queryset.filter(found)


def choice(params, name: str, allowed) -> str:
    value = params.get(name, "").strip()
    if value and value not in allowed:
        message = _("%(name)s is one of: %(values)s.") % {"name": name, "values": ", ".join(allowed)}
        raise ApiError("validation_error", message, fields={name: [message]})
    return value


def account_or_404(account_id) -> CitizenAccount:
    account = CitizenAccount.objects.select_related("user").filter(pk=account_id).first()
    if account is None:
        raise ApiError("not_found", _("No such citizen account."), status=status.HTTP_404_NOT_FOUND)
    return account


# --- Referrals ------------------------------------------------------------------------------------------------

class ReferralsView(APIView):
    permission_classes = only(REVIEW, gettext_lazy("Only the Super Admin can review referrals."))

    @extend_schema(
        summary="Super Admin: referrals, newest first, with counts per status",
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(Referral.Status.values)),
            OpenApiParameter("q", OpenApiTypes.STR, description="A name, the last 4 digits or a referral code, of either side"),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses=ReferralRowSerializer(many=True),
    )
    def get(self, request):
        chosen = choice(request.query_params, "status", Referral.Status.values)
        rows = search_referrals(referrals(), request.query_params.get("q", ""))
        counts = {value: 0 for value in Referral.Status.values}
        counts.update({row["status"]: row["n"] for row in rows.order_by().values("status").annotate(n=Count("pk"))})
        if chosen:
            rows = rows.filter(status=chosen)
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(rows.order_by("-pending_at", "-pk"), request, view=self)
        response = paginator.get_paginated_response(ReferralRowSerializer(page, many=True).data)
        response.data["status_counts"] = counts
        return response


class ReferralDecisionView(APIView):
    """confirm=True: confirm now, before the hold ends; False: not confirmed, with the comment as the reason."""

    permission_classes = only(REVIEW, gettext_lazy("Only the Super Admin can review referrals."))
    confirm = True

    @extend_schema(summary="Super Admin: decide a waiting referral", request=CommentSerializer, responses=ReferralRowSerializer)
    def post(self, request, pk):
        referral = referrals().filter(pk=pk).first()
        if referral is None:
            raise ApiError("not_found", _("No such referral."), status=status.HTTP_404_NOT_FOUND)
        data = CommentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        decided = services.decide(
            referral, confirm=self.confirm, reason_key="" if self.confirm else "rejected", by=request.user,
            comment=data.validated_data["comment"],
        )
        row = ReferralRowSerializer(referrals().get(pk=referral.pk)).data
        if not decided:
            raise ApiError(
                "already_decided", _("This referral was already decided."), status=status.HTTP_409_CONFLICT, referral=row
            )
        return Response(row)


# --- Wallets --------------------------------------------------------------------------------------------------

class AdminWalletView(APIView):
    permission_classes = only(VIEW, gettext_lazy("Only the Super Admin can see citizens' wallets."))

    @extend_schema(
        summary="Super Admin: one citizen's balance and history, newest first",
        parameters=[OpenApiParameter("page", OpenApiTypes.INT), OpenApiParameter("page_size", OpenApiTypes.INT)],
        responses=inline_serializer(
            "AdminWalletPage",
            {
                "confirmed_points": serializers.IntegerField(),
                "held_points": serializers.IntegerField(),
                "next_release_at": serializers.DateTimeField(allow_null=True),
                "count": serializers.IntegerField(),
                "next": serializers.CharField(allow_null=True),
                "previous": serializers.CharField(allow_null=True),
                "results": AdminWalletEntrySerializer(many=True),
            },
        ),
    )
    def get(self, request, account_id):
        account = account_or_404(account_id)
        entries = WalletEntry.objects.filter(wallet__account=account).select_related("created_by")
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(entries.order_by("-created_at", "-pk"), request, view=self)
        response = paginator.get_paginated_response(AdminWalletEntrySerializer(page, many=True).data)
        response.data = {**services.summary(account.wallet), **response.data}
        return response


class AdjustWalletView(APIView):
    permission_classes = only(ADJUST, gettext_lazy("Only the Super Admin can correct points."))

    @extend_schema(
        summary="Super Admin: add or take away confirmed points, with a reason the citizen sees",
        request=AdjustSerializer,
        responses=inline_serializer(
            "WalletSummary",
            {
                "confirmed_points": serializers.IntegerField(),
                "held_points": serializers.IntegerField(),
                "next_release_at": serializers.DateTimeField(allow_null=True),
            },
        ),
    )
    def post(self, request, account_id):
        account = account_or_404(account_id)
        data = AdjustSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(services.adjust(
            account, data.validated_data["points"], reason=data.validated_data["reason"], by=request.user,
            via=client_via(request),
        ))


class WalletEntriesView(APIView):
    """The console's Wallet entries tab (FR-33; the Admin's decision of 2026-11-25): every entry, in pages."""

    permission_classes = only(VIEW, gettext_lazy("Only the Super Admin can see citizens' wallets."))

    @extend_schema(
        summary="Super Admin: every wallet entry, newest first, with the CSV export's filters",
        parameters=[
            OpenApiParameter("account_id", OpenApiTypes.UUID, description="One citizen's entries"),
            OpenApiParameter("kind", OpenApiTypes.STR, enum=list(WalletEntry.Kind.values)),
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(WalletEntry.Status.values)),
            OpenApiParameter("from", OpenApiTypes.DATE, description="From this India date"),
            OpenApiParameter("to", OpenApiTypes.DATE, description="Up to and including this India date"),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses=AdminEntryRowSerializer(many=True),
    )
    def get(self, request):
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(filtered_entries(request.query_params), request, view=self)
        return paginator.get_paginated_response(AdminEntryRowSerializer(page, many=True).data)


# --- CSV export -----------------------------------------------------------------------------------------------

def _time(value) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _date_param(params, name: str):
    value = params.get(name, "").strip()
    if not value:
        return None
    day = parse_date(value) if len(value) == 10 else None
    if day is None:
        message = _("Give a date as YYYY-MM-DD.")
        raise ApiError("validation_error", message, fields={name: [message]})
    return day


def referral_rows(params):
    chosen = choice(params, "status", Referral.Status.values)
    rows = search_referrals(referrals(), params.get("q", ""))
    if chosen:
        rows = rows.filter(status=chosen)
    header = [
        "id", "started", "confirm_after", "status", "reason", "decided_at", "decided_by", "comment",
        "referrer_id", "referrer_name", "referrer_mobile_last4", "referrer_email", "referrer_code",
        "referee_id", "referee_name", "referee_mobile_last4", "referee_email",
    ]

    def lines(chunk):
        for r in chunk:
            yield [
                r.id, _time(r.pending_at), _time(r.confirm_after), r.status, r.reason_key, _time(r.decided_at),
                r.decided_by.name if r.decided_by else "", r.comment,
                r.referrer_id, r.referrer.user.name, r.referrer.mobile_last4, mask_email(r.referrer.email),
                r.referrer.referral_code,
                r.referee_id, r.referee.user.name, r.referee.mobile_last4, mask_email(r.referee.email),
            ]

    return rows.order_by("-pending_at", "-pk"), header, lines


def filtered_entries(params):
    """Every wallet entry, newest first, narrowed by the entries' filters; the list and the export share them."""
    rows = WalletEntry.objects.select_related("wallet__account__user", "created_by")
    account = params.get("account_id", "").strip()
    if account:
        if uuid_or_none(account) is None:
            message = _("Not an account id.")
            raise ApiError("validation_error", message, fields={"account_id": [message]})
        rows = rows.filter(wallet__account_id=uuid_or_none(account))
    kind = choice(params, "kind", WalletEntry.Kind.values)
    if kind:
        rows = rows.filter(kind=kind)
    chosen = choice(params, "status", WalletEntry.Status.values)
    if chosen:
        rows = rows.filter(status=chosen)
    start, end = _date_param(params, "from"), _date_param(params, "to")
    if start:  # India dates: the whole day from midnight
        rows = rows.filter(created_at__gte=timezone.make_aware(datetime.combine(start, time.min)))
    if end:
        rows = rows.filter(created_at__lt=timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))
    return rows.order_by("-created_at", "-pk")


def entry_rows(params):
    rows = filtered_entries(params)
    header = [
        "id", "time", "account_id", "name", "mobile_last4", "email", "kind", "points", "status", "reason_key",
        "reason", "referral_id", "decided_at", "created_by",
    ]

    def lines(chunk):
        for e in chunk:
            account = e.wallet.account
            words = e.reason or (str(REASONS[e.reason_key]) if e.reason_key in REASONS else "")
            yield [
                e.id, _time(e.created_at), account.pk, account.user.name, account.mobile_last4, mask_email(account.email),
                e.kind, e.points, e.status, e.reason_key, words, e.referral_id or "", _time(e.decided_at),
                e.created_by.name if e.created_by else "",
            ]

    return rows, header, lines


EXPORTS = {"referrals": referral_rows, "entries": entry_rows}
#: Filters whose values hold nothing personal, kept in the export's audit entry (q only as "(given)").
PLAIN_FILTERS = ("type", "status", "kind", "account_id", "from", "to")


class WalletExportView(APIView):
    permission_classes = only(VIEW, gettext_lazy("Only the Super Admin can export wallets."))
    renderer_classes = [JSONRenderer, CSVRenderer]

    def handle_exception(self, exc):
        # Errors always go out as JSON, even when ?format=csv picked the CSV renderer.
        response = super().handle_exception(exc)
        self.request.accepted_renderer = response.accepted_renderer = JSONRenderer()
        self.request.accepted_media_type = response.accepted_media_type = JSONRenderer.media_type
        return response

    @extend_schema(
        summary="Super Admin: referrals or wallet entries as CSV (last 4 digits and masked emails only)",
        parameters=[
            OpenApiParameter("type", OpenApiTypes.STR, enum=list(EXPORTS), required=True),
            OpenApiParameter("format", OpenApiTypes.STR, enum=["csv"]),
            OpenApiParameter("status", OpenApiTypes.STR),
            OpenApiParameter("q", OpenApiTypes.STR, description="referrals: as the list"),
            OpenApiParameter("account_id", OpenApiTypes.UUID, description="entries: one citizen's"),
            OpenApiParameter("kind", OpenApiTypes.STR, enum=list(WalletEntry.Kind.values), description="entries"),
            OpenApiParameter("from", OpenApiTypes.DATE, description="entries: from this India date"),
            OpenApiParameter("to", OpenApiTypes.DATE, description="entries: up to and including this India date"),
        ],
        responses={(200, "text/csv"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        params = request.query_params
        if params.get("format", "csv") != "csv":
            raise ApiError("validation_error", _("Only CSV export is available."), fields={"format": ["csv"]})
        kind = params.get("type", "").strip()
        if kind not in EXPORTS:
            message = _("type is referrals or entries.")
            raise ApiError("validation_error", message, fields={"type": [message]})
        rows, header, lines = EXPORTS[kind](params)
        if rows[: EXPORT_MAX_ROWS + 1].count() > EXPORT_MAX_ROWS:
            message = _("More than %(n)d rows match. Narrow the filters.") % {"n": EXPORT_MAX_ROWS}
            raise ApiError("validation_error", message)
        buffer = io.StringIO()
        buffer.write("\ufeff")  # BOM: Excel then reads Telugu correctly
        writer = safe_writer(buffer)  # names and texts never run as spreadsheet formulas
        writer.writerow(header)
        writer.writerows(lines(rows[:EXPORT_MAX_ROWS].iterator(chunk_size=2000)))
        filters = {name: params.get(name) for name in PLAIN_FILTERS if params.get(name)}
        if params.get("q"):
            filters["q"] = "(given)"
        record(
            "report.exported", actor=request.user, target_label=f"wallet {kind}", via=client_via(request),
            data={"report": f"wallet_{kind}", "filters": filters, "format": "csv"},
        )
        response = HttpResponse(buffer.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="wallet-{kind}-{timezone.localdate():%Y%m%d}.csv"'
        return response
