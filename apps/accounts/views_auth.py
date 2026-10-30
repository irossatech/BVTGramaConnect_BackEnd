"""Login, password change, token refresh, logout, console session and /me.

docs/api-contract.md, section 3; docs/authorization.md has the whole flow.

- The mobile app logs in with POST /auth/login/ and gets a JWT pair.
- The web console gets a CSRF cookie from GET /auth/csrf/, then logs in with
  POST /auth/session/login/, which creates a Django session for users holding
  web_admin.access_web_console only.
Both go through credentials.authenticate_official(), so django-axes lockout,
blocked accounts and the audit log work the same for each.
"""

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.signals import user_logged_in
from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from grama.exceptions import ApiError

from . import authz
from .authentication import CONSOLE_PERMISSION, came_from_console, not_console_user
from .credentials import authenticate_official, check_new_password, refuse, wrong_current_password
from .lockout import SESSION_TOKEN_VERSION
from .serializers import (
    LoginResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PasswordChangeSerializer,
    RefreshSerializer,
    TokenPairSerializer,
)
from .tokens import blacklist_refresh, issue_tokens, revoke_all_sessions, rotate_refresh, session_revoked


class PublicAuthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []


def enforce_csrf(request) -> None:
    """CSRF check for views that run before there is a session (login, logout)."""
    SessionAuthentication().enforce_csrf(request)


def require_console_user(user) -> None:
    # A new Super Admin must still change their password, which happens in the console.
    if not authz.can(user, CONSOLE_PERMISSION, pending_change_ok=True):
        refused = not_console_user()
        raise refuse(user, refused.error_code, refused.detail, "console")


def _signal_login(request, user) -> None:
    """What Django's login() would do for a session: last_login, axes reset of failures."""
    user_logged_in.send(sender=user.__class__, request=request._request, user=user)


# --- Mobile app: JWT ----------------------------------------------------------------------


class LoginView(PublicAuthView):
    @extend_schema(summary="Log in with username and password (mobile app)", request=LoginSerializer, responses=LoginResponseSerializer)
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate_official(
            request._request, data.validated_data["username"], data.validated_data["password"], via="app"
        )
        _signal_login(request, user)
        tokens = issue_tokens(user)
        record("auth.login", actor=user, target=user, target_label=user.name, via="app")
        return Response({**tokens, "user": MeSerializer(user, context={"request": request}).data})


class RefreshView(PublicAuthView):
    def get_authenticate_header(self, request):
        # Without this, DRF turns the 401 for a dead refresh token into a 403.
        return 'Bearer realm="api"'

    @extend_schema(summary="Swap a refresh token for new tokens", request=RefreshSerializer, responses=TokenPairSerializer)
    def post(self, request):
        token = request.data.get("refresh") if hasattr(request.data, "get") else None
        if not token:
            raise session_revoked()
        return Response(rotate_refresh(token))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Log out this device, or every device", request=LogoutSerializer, responses={204: None})
    def post(self, request):
        data = LogoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        if data.validated_data.get("refresh"):
            blacklist_refresh(data.validated_data["refresh"])
        if data.validated_data["all_devices"]:
            revoke_all_sessions(request.user)
        if came_from_console(request):
            django_logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Web console: Django session ----------------------------------------------------------


class CsrfView(PublicAuthView):
    @extend_schema(
        summary="Set the csrftoken cookie for the console",
        responses=inline_serializer("CsrfOk", {"detail": serializers.CharField()}),
    )
    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"detail": "ok"})


class SessionLoginView(PublicAuthView):
    @extend_schema(
        summary="Console login: creates a session cookie (send X-CSRFToken)",
        request=LoginSerializer,
        responses=inline_serializer("SessionLogin", {"user": MeSerializer()}),
    )
    def post(self, request):
        enforce_csrf(request)
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate_official(
            request._request,
            data.validated_data["username"],
            data.validated_data["password"],
            via="console",
            require=require_console_user,
        )
        django_login(request._request, user, backend=getattr(user, "backend", None))
        record("auth.login", actor=user, target=user, target_label=user.name, via="console")
        return Response({"user": MeSerializer(user, context={"request": request}).data})


class SessionLogoutView(PublicAuthView):
    @extend_schema(summary="Console logout: ends the session", request=None, responses={204: None})
    def post(self, request):
        enforce_csrf(request)
        django_logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Both ---------------------------------------------------------------------------------


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Choose a new password; every other session ends",
        request=PasswordChangeSerializer,
        responses=TokenPairSerializer,
    )
    def post(self, request):
        data = PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = request.user
        current = data.validated_data["current_password"]
        new = data.validated_data["new_password"]
        console = came_from_console(request)
        if not user.check_password(current):
            # Audited and counted; after too many the error is session_revoked (credentials.py).
            raise wrong_current_password(user, via="console" if console else "app")
        check_new_password(new, user, field="new_password")
        if new == current:
            message = _("Choose a password different from the current one.")
            raise ApiError("validation_error", message, fields={"new_password": [message]})

        with transaction.atomic():
            user.set_password(new)
            user.must_change_password = False
            user.password_changed_at = timezone.now()
            user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])
            revoke_all_sessions(user)
            record("user.password_changed", actor=user, target=user, target_label=user.name, via="console" if console else "app")
        if console:
            # This session stays; every other session and token has ended.
            update_session_auth_hash(request._request, user)
            request._request.session[SESSION_TOKEN_VERSION] = user.token_version
            return Response({"user": MeSerializer(user, context={"request": request}).data})
        return Response(issue_tokens(user))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="The logged-in official, their groups, areas and permissions", responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)

    @extend_schema(summary="Update your name or language", request=MeUpdateSerializer, responses=MeSerializer)
    def patch(self, request):
        data = MeUpdateSerializer(request.user, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        return Response(MeSerializer(request.user, context={"request": request}).data)
