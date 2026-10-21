"""Login, password change, token refresh, logout, /me and role default passwords.

docs/api-contract.md, section 3.

Officials log in with a username (or their email) and a password. The Super
Admin console uses the same login with client="web_admin"; only Super Admins
get through, and its refresh token goes in an HttpOnly cookie scoped to
/api/v1/auth/, never in the response body.
"""

from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from grama.exceptions import ApiError, permission_denied
from grama.http import client_ip

from .credentials import authenticate_login, invalid_credentials, role_default_row, set_role_default
from .roles import ROLE_ORDER, Role
from .scope import is_super_admin
from .serializers import (
    LoginResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PasswordChangeSerializer,
    RefreshSerializer,
    RolePasswordSerializer,
    RolePasswordUpdateSerializer,
    TokenPairSerializer,
)
from .tokens import blacklist_refresh, issue_tokens, revoke_all_sessions, rotate_refresh, session_revoked

COOKIE_PATH = "/api/v1/auth/"


def set_refresh_cookie(response, token: str) -> None:
    response.set_cookie(
        settings.CONSOLE_REFRESH_COOKIE,
        token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.CONSOLE_COOKIE_SECURE,
        samesite="Strict",
        path=COOKIE_PATH,
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(settings.CONSOLE_REFRESH_COOKIE, path=COOKIE_PATH, samesite="Strict")


def check_same_site(request) -> None:
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.get_host():
        raise ApiError("permission_denied", _("Cross-site request refused."), status=status.HTTP_403_FORBIDDEN)


def refresh_from_request(request) -> tuple[str | None, bool]:
    """The refresh token from the body, else from the console cookie.

    Returns (token, came_from_cookie). Cookie requests must come from this site.
    """
    token = request.data.get("refresh") if hasattr(request.data, "get") else None
    if token:
        return token, False
    token = request.COOKIES.get(settings.CONSOLE_REFRESH_COOKIE)
    if token:
        check_same_site(request)
        return token, True
    return None, False


def require_console_user(user) -> None:
    if not is_super_admin(user):
        raise ApiError(
            "not_console_user",
            _("The console is for Super Admins. Other officials use the mobile app."),
            status=status.HTTP_403_FORBIDDEN,
        )


def token_response(tokens: dict, *, console: bool, extra: dict | None = None) -> Response:
    """Access token in the body; refresh in the body for the app, in the cookie for the console."""
    body = {"access": tokens["access"], **(extra or {})}
    if not console:
        body["refresh"] = tokens["refresh"]
    response = Response(body)
    if console:
        set_refresh_cookie(response, tokens["refresh"])
    return response


class PublicAuthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []


class LoginView(PublicAuthView):
    @extend_schema(summary="Log in with username (or email) and password", request=LoginSerializer, responses=LoginResponseSerializer)
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        console = data.validated_data["client"] == "web_admin"
        if console:
            check_same_site(request)
        user = authenticate_login(
            data.validated_data["username"],
            data.validated_data["password"],
            client_ip(request),
            require=require_console_user if console else None,
        )
        tokens = issue_tokens(user)
        record("auth.login", actor=user, target=user, target_label=user.name, via="console" if console else "app")
        return token_response(tokens, console=console, extra={"user": MeSerializer(user).data})


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Choose a new password; other devices are logged out", request=PasswordChangeSerializer, responses=TokenPairSerializer)
    def post(self, request):
        data = PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = request.user
        current = data.validated_data["current_password"]
        new = data.validated_data["new_password"]
        if not user.check_password(current):
            raise invalid_credentials()
        try:
            validate_password(new, user)
        except DjangoValidationError as exc:
            raise ApiError("validation_error", exc.messages[0], fields={"new_password": list(exc.messages)}) from None
        if new == current:
            message = _("Choose a password different from the current one.")
            raise ApiError("validation_error", message, fields={"new_password": [message]})

        with transaction.atomic():
            user.set_password(new)
            user.must_change_password = False
            user.password_changed_at = timezone.now()
            user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])
            revoke_all_sessions(user)
            record("user.password_changed", actor=user, target=user, target_label=user.name, via=self._via(request))
        console = data.validated_data.get("client") == "web_admin" or settings.CONSOLE_REFRESH_COOKIE in request.COOKIES
        return token_response(issue_tokens(user), console=console)

    @staticmethod
    def _via(request) -> str:
        if request.data.get("client") == "web_admin" or settings.CONSOLE_REFRESH_COOKIE in request.COOKIES:
            return "console"
        return "app"


class RefreshView(PublicAuthView):
    def get_authenticate_header(self, request):
        # Without this, DRF turns the 401 for a dead refresh token into a 403.
        return 'Bearer realm="api"'

    @extend_schema(summary="Swap a refresh token for new tokens", request=RefreshSerializer, responses=TokenPairSerializer)
    def post(self, request):
        token, from_cookie = refresh_from_request(request)
        if not token:
            raise session_revoked()
        tokens = rotate_refresh(token)
        return token_response(tokens, console=from_cookie)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Log out this device, or every device", request=LogoutSerializer, responses={204: None})
    def post(self, request):
        data = LogoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        token, _from_cookie = refresh_from_request(request)
        if token:
            blacklist_refresh(token)
        if data.validated_data["all_devices"]:
            revoke_all_sessions(request.user)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="The logged-in official", responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    @extend_schema(summary="Update your name or language", request=MeUpdateSerializer, responses=MeSerializer)
    def patch(self, request):
        data = MeUpdateSerializer(request.user, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        return Response(MeSerializer(request.user).data)


# --- Default passwords per role (Super Admin) ---------------------------------------------


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        if not is_super_admin(request.user):
            raise permission_denied(_("Only Super Admins can see or change default passwords."))
        return True


class RolePasswordListView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(summary="Default password of each role", responses=RolePasswordSerializer(many=True))
    def get(self, request):
        rows = [role_default_row(role) for role in ROLE_ORDER]
        return Response(RolePasswordSerializer(rows, many=True).data)


class RolePasswordDetailView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(
        summary="Change a role's default password (future invites and resets only)",
        request=RolePasswordUpdateSerializer,
        responses=RolePasswordSerializer,
    )
    def patch(self, request, role):
        if role not in Role.values:
            raise Http404("No such role.")
        data = RolePasswordUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                row = set_role_default(role, data.validated_data["password"], request.user)
                record(
                    "setting.role_password_changed",
                    actor=request.user,
                    target=row,
                    target_label=row.get_role_display(),
                    via="console" if request.headers.get("X-Client") == "web_admin" else "app",
                    data={"role": role},
                )
        except DjangoValidationError as exc:
            raise ApiError("validation_error", exc.messages[0], fields={"password": list(exc.messages)}) from None
        return Response(RolePasswordSerializer(row).data)
