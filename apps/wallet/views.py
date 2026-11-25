"""GET /wallet/: the citizen's balance and history (contract section 21; BVTGC-126)."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasPerm
from apps.citizens.services_account import require_wallet

from . import services
from .models import Wallet, WalletEntry
from .serializers import WalletEntrySerializer


class HistoryPagination(CursorPagination):
    ordering = ("-created_at", "-id")
    page_size = 20


class WalletView(APIView):
    """The cursor list of section 1, with the balance as sibling keys on every page."""

    permission_classes = [HasPerm("wallet.use_wallet")]

    def initial(self, request, *args, **kwargs):
        require_wallet()
        super().initial(request, *args, **kwargs)

    @extend_schema(
        summary="The citizen's wallet: balance, points on hold and the history, newest first",
        responses=inline_serializer(
            "WalletPage",
            {
                "confirmed_points": serializers.IntegerField(),
                "held_points": serializers.IntegerField(),
                "next_release_at": serializers.DateTimeField(allow_null=True),
                "next": serializers.CharField(allow_null=True),
                "previous": serializers.CharField(allow_null=True),
                "results": WalletEntrySerializer(many=True),
            },
        ),
    )
    def get(self, request):
        wallet = Wallet.objects.get(account__user=request.user)
        paginator = HistoryPagination()
        page = paginator.paginate_queryset(WalletEntry.objects.filter(wallet=wallet), request, view=self)
        return Response(
            {
                **services.summary(wallet),
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": WalletEntrySerializer(page, many=True).data,
            }
        )
