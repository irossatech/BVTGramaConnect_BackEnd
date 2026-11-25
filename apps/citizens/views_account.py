"""Citizens' wallet accounts: sign-up, login and the citizen's own account (contract section 21).

The sign-up endpoints are public: no login, and no Authorization header is read (authentication_classes is
empty), so a stale token in the app never gets in the way. Every endpoint answers 404 while the wallet is
off. The services in services_account.py do the work; these views only read and answer.
"""

from __future__ import annotations

from django.contrib.auth.signals import user_logged_in
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.tokens import issue_tokens
from apps.audit.services import record

from . import services_account as accounts
from .serializers_account import (
    AccountSerializer,
    CodeSentSerializer,
    CitizenForgotSerializer,
    CitizenLoginSerializer,
    CitizenResetSerializer,
    SignedInSerializer,
    SignupSerializer,
    SignupStartedSerializer,
    SignupTokenSerializer,
    SignupVerifySerializer,
)


class WalletPublicView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        accounts.require_wallet()


def signed_in(account, request) -> dict:
    tokens = issue_tokens(account.user)
    return {
        "access": tokens["access"],
        "refresh": tokens["refresh"],
        "account": AccountSerializer(account, context={"request": request}).data,
    }


class SignupView(WalletPublicView):
    @extend_schema(
        summary="Start a citizen wallet sign-up: check the form and email a code",
        request=SignupSerializer,
        responses=SignupStartedSerializer,
    )
    def post(self, request):
        data = SignupSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        token, sent = accounts.start_signup(**data.validated_data)
        return Response({"signup_token": token, **sent.as_dict(with_hint=True)})


class SignupResendView(WalletPublicView):
    @extend_schema(summary="Send a new sign-up code", request=SignupTokenSerializer, responses=CodeSentSerializer)
    def post(self, request):
        data = SignupTokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(accounts.resend_signup(data.validated_data["signup_token"]).as_dict())


class SignupVerifyView(WalletPublicView):
    @extend_schema(
        summary="Finish the sign-up with the email code: the account is made and logged in",
        request=SignupVerifySerializer,
        responses={201: SignedInSerializer},
    )
    def post(self, request):
        data = SignupVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        account = accounts.finish_signup(data.validated_data["signup_token"], data.validated_data["code"])
        return Response(signed_in(account, request), status=status.HTTP_201_CREATED)


class LoginView(WalletPublicView):
    @extend_schema(
        summary="Log in to a citizen wallet account with the email or the mobile number",
        request=CitizenLoginSerializer,
        responses=SignedInSerializer,
    )
    def post(self, request):
        data = CitizenLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        account = accounts.log_in(request._request, data.validated_data["login"], data.validated_data["password"])
        user = account.user
        # As the officials' login: last_login, and django-axes clears the failures of this account.
        user_logged_in.send(sender=user.__class__, request=request._request, user=user)
        record("auth.login", actor=user, target=user, target_label=user.name, via="citizen")
        return Response(signed_in(account, request))


class ForgotPasswordView(WalletPublicView):
    @extend_schema(
        summary="Email a code to reset a forgotten password (the same answer for every login)",
        request=CitizenForgotSerializer,
        responses=CodeSentSerializer,
    )
    def post(self, request):
        data = CitizenForgotSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(accounts.forgot_password(data.validated_data["login"]))


class ResetPasswordView(WalletPublicView):
    @extend_schema(
        summary="Set a new password with the emailed code; every other login ends",
        request=CitizenResetSerializer,
        responses=SignedInSerializer,
    )
    def post(self, request):
        data = CitizenResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        account = accounts.reset_password(
            data.validated_data["login"], data.validated_data["code"], data.validated_data["new_password"]
        )
        return Response(signed_in(account, request))
