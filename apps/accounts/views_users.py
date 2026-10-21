"""User management. docs/api-contract.md, section 3, and the matrix in section 11.

An admin manages only roles below their own, and only inside their own area:
- Super Admin: District, Mandal, Village Admins and Panchayat members anywhere.
- District Admin: Mandal Admins, Village Admins, Panchayat members in the district.
- Mandal Admin: Village Admins and Panchayat members in the mandal.
- Village Admin: Panchayat members in the village.

Users outside that reach are invisible: reading or changing them answers 404.
Creating a user or an assignment in an area outside it answers 403 out_of_area.
"""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.audit.services import record
from apps.geography.areas import AreaRef, area_exists, within_q
from apps.geography.models import AreaLevel
from grama.exceptions import ApiError, out_of_area, permission_denied

from .models import RoleAssignment, User, UserStatus
from .roles import ROLE_LEVEL, Role
from .scope import can_manage, manageable_roles, manageable_users, role_matches_level
from .serializers import (
    BlockSerializer,
    RoleAssignmentCreateSerializer,
    RoleAssignmentSerializer,
    RoleAssignmentUpdateSerializer,
    UserCountsSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
    prime_describer,
)
from .credentials import give_default_password, highest_role, role_default
from .tasks import queue_credentials_email
from .tokens import block_user, revoke_all_sessions, unblock_user


def via(request) -> str:
    """'console' for the Super Admin console (it sends X-Client: web_admin), else 'app'."""
    return "console" if request.headers.get("X-Client") == "web_admin" else "app"


class ManagesUsers(BasePermission):
    def has_permission(self, request, view):
        if not manageable_roles(request.user):
            raise permission_denied(_("Your role cannot manage users."))
        return True


def check_assignment_allowed(actor, role: str, area: AreaRef) -> None:
    """Raise unless `actor` may give `role` in `area` to someone."""
    if not role_matches_level(role, area):
        raise ApiError(
            "validation_error",
            _("This role is assigned to a %(level)s.") % {"level": ROLE_LEVEL[role].lower()},
            fields={"area": [_("Must be a %(level)s.") % {"level": ROLE_LEVEL[role].lower()}]},
        )
    if not area_exists(area, active_only=True):
        raise ApiError(
            "validation_error",
            _("No such active area."),
            fields={"area": [_("No such active area.")]},
        )
    if role not in manageable_roles(actor):
        raise permission_denied(_("Your role cannot give this role."))
    if not can_manage(actor, role, area):
        raise out_of_area()


def duplicate(message, field: str) -> ApiError:
    return ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={field: [message]})


LIST_PARAMETERS = [
    OpenApiParameter("role", OpenApiTypes.STR, enum=[r.value for r in Role]),
    OpenApiParameter("status", OpenApiTypes.STR, enum=[s.value for s in UserStatus]),
    OpenApiParameter("level", OpenApiTypes.STR, enum=[lv.value for lv in AreaLevel], description="With area_id: users inside that area."),
    OpenApiParameter("area_id", OpenApiTypes.INT),
    OpenApiParameter("district", OpenApiTypes.INT, description="Short for level=DISTRICT&area_id=…"),
    OpenApiParameter("q", OpenApiTypes.STR, description="Name, Telugu name, username, email or mobile."),
]


class UserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated, ManagesUsers]
    serializer_class = UserSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return (
            manageable_users(self.request.user)
            .select_related("created_by")
            .prefetch_related("role_assignments")
            .order_by("name", "id")
        )

    def _respond(self, users, *, status_code=status.HTTP_200_OK, many=False):
        context = self.get_serializer_context()
        prime_describer(context, users if many else [users])
        data = UserSerializer(users, many=many, context=context).data
        return data if many else Response(data, status=status_code)

    # --- Reading ----------------------------------------------------------------

    def _area_and_text_filters(self, queryset):
        params = self.request.query_params
        level, area_id = params.get("level"), params.get("area_id")
        if params.get("district"):
            level, area_id = AreaLevel.DISTRICT, params["district"]
        if level or area_id:
            if level not in AreaLevel.values or not (area_id or "").isdigit():
                raise ApiError(
                    "validation_error",
                    _("Give both level and area_id."),
                    fields={"area_id": [_("Give a level and a numeric area_id.")]},
                )
            inside = RoleAssignment.objects.filter(within_q(AreaRef(level, int(area_id))))
            queryset = queryset.filter(id__in=inside.values("user_id"))
        query = params.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(name_te__icontains=query)
                | Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(mobile__contains=query)
            )
        return queryset

    def _role_and_status_filters(self, queryset):
        params = self.request.query_params
        role = params.get("role")
        if role:
            if role not in Role.values:
                raise ApiError("validation_error", _("Unknown role."), fields={"role": [_("Unknown role.")]})
            queryset = queryset.filter(id__in=RoleAssignment.objects.filter(role=role).values("user_id"))
        user_status = (params.get("status") or "").upper()
        if user_status:
            if user_status not in UserStatus.values:
                raise ApiError("validation_error", _("Unknown status."), fields={"status": [_("Unknown status.")]})
            queryset = queryset.filter(status=user_status)
        return queryset

    @staticmethod
    def _counts(queryset) -> dict:
        by_role = (
            RoleAssignment.objects.filter(user__in=queryset.values("id"))
            .values("role")
            .annotate(users=Count("user", distinct=True))
        )
        return {
            "total": queryset.count(),
            "active": queryset.filter(status=UserStatus.ACTIVE).count(),
            "blocked": queryset.filter(status=UserStatus.BLOCKED).count(),
            "by_role": {row["role"]: row["users"] for row in by_role},
        }

    @extend_schema(
        summary="Users you may manage",
        parameters=LIST_PARAMETERS,
        responses=inline_serializer(
            "UserPage",
            fields={
                "count": serializers.IntegerField(),
                "next": serializers.URLField(allow_null=True),
                "previous": serializers.URLField(allow_null=True),
                "results": UserSerializer(many=True),
                "counts": UserCountsSerializer(),
            },
        ),
    )
    def list(self, request, *args, **kwargs):
        base = self._area_and_text_filters(self.get_queryset())
        counts = self._counts(base.order_by())
        page = self.paginate_queryset(self._role_and_status_filters(base))
        response = self.get_paginated_response(self._respond(page, many=True))
        response.data["counts"] = counts
        return response

    @extend_schema(summary="One user you may manage")
    def retrieve(self, request, *args, **kwargs):
        return self._respond(self.get_object())

    # --- Writing ----------------------------------------------------------------

    @extend_schema(
        summary="Invite an official: they get an email with their username and the role's default password",
        request=UserCreateSerializer,
        responses={201: UserSerializer},
    )
    def create(self, request, *args, **kwargs):
        data = UserCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        check_assignment_allowed(request.user, values["role"], values["area"])
        if User.objects.filter(email=values["email"]).exists():
            raise duplicate(_("This email already belongs to an official."), "email")
        if User.objects.filter(username=values["username"]).exists():
            raise duplicate(_("This username is taken."), "username")
        if values["mobile"] and User.objects.filter(mobile=values["mobile"]).exists():
            raise duplicate(_("This mobile number already belongs to an official."), "mobile")

        with transaction.atomic():
            user = User.objects.create_user(
                username=values["username"],
                email=values["email"],
                mobile=values["mobile"],
                name=values["name"],
                name_te=values["name_te"],
                preferred_language=values["preferred_language"],
                password=role_default(values["role"]),
                must_change_password=settings.FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN,
                created_by=request.user,
            )
            assignment = RoleAssignment.objects.create(
                user=user,
                role=values["role"],
                area_level=values["area"].level,
                area_id=values["area"].id,
                created_by=request.user,
            )
            record(
                "user.created", actor=request.user, target=user, target_label=user.name,
                area=assignment.area, via=via(request), data={"role": assignment.role},
            )
            transaction.on_commit(lambda: queue_credentials_email(user.id, "invite"))
        return self._respond(self.get_queryset().get(pk=user.pk), status_code=status.HTTP_201_CREATED)

    @extend_schema(summary="Change a user's name or language", request=UserUpdateSerializer, responses=UserSerializer)
    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        data = UserUpdateSerializer(user, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        record("user.updated", actor=request.user, target=user, target_label=user.name, via=via(request), data=dict(data.validated_data))
        return self._respond(self.get_queryset().get(pk=user.pk))

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    @extend_schema(summary="Block a user and end all their sessions", request=BlockSerializer, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        user = self.get_object()
        reason = BlockSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        if not user.is_blocked:
            with transaction.atomic():
                block_user(user)
                record(
                    "user.blocked", actor=request.user, target=user, target_label=user.name, via=via(request),
                    data={"reason": reason.validated_data.get("reason", "")},
                )
        return self._respond(self.get_queryset().get(pk=user.pk))

    @extend_schema(summary="Unblock a user", request=None, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        user = self.get_object()
        if user.is_blocked:
            with transaction.atomic():
                unblock_user(user)
                record("user.unblocked", actor=request.user, target=user, target_label=user.name, via=via(request))
        return self._respond(self.get_queryset().get(pk=user.pk))


    @extend_schema(
        summary="Reset a password to the role's default and email it; all sessions end",
        request=None,
        responses=UserSerializer,
    )
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        role = highest_role(user)
        if role is None:
            raise ApiError("validation_error", _("This user has no role, so there is no default password to give."))
        if not user.email:
            raise ApiError(
                "validation_error",
                _("Add an email for this user first; the new password is sent by email."),
                fields={"email": [_("Required to send the password.")]},
            )
        with transaction.atomic():
            give_default_password(user, role)
            revoke_all_sessions(user)
            record("user.password_reset", actor=request.user, target=user, target_label=user.name, via=via(request))
            transaction.on_commit(lambda: queue_credentials_email(user.id, "reset"))
        return self._respond(self.get_queryset().get(pk=user.pk))


class RoleAssignmentViewSet(viewsets.GenericViewSet):
    """Assignments of one user the actor may manage."""

    permission_classes = [IsAuthenticated, ManagesUsers]
    serializer_class = RoleAssignmentSerializer

    def get_target_user(self) -> User:
        return get_object_or_404(manageable_users(self.request.user), pk=self.kwargs["user_pk"])

    def get_assignment(self, user) -> RoleAssignment:
        return get_object_or_404(RoleAssignment, pk=self.kwargs["pk"], user=user)

    def _respond(self, assignment, status_code=status.HTTP_200_OK):
        return Response(RoleAssignmentSerializer(assignment, context=self.get_serializer_context()).data, status=status_code)

    @extend_schema(summary="Give a user another role", request=RoleAssignmentCreateSerializer, responses={201: RoleAssignmentSerializer})
    def create(self, request, user_pk=None):
        user = self.get_target_user()
        data = RoleAssignmentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        role, area = data.validated_data["role"], data.validated_data["area"]
        check_assignment_allowed(request.user, role, area)
        try:
            with transaction.atomic():
                assignment = RoleAssignment.objects.create(
                    user=user, role=role, area_level=area.level, area_id=area.id, created_by=request.user
                )
                record(
                    "user.assignment_added", actor=request.user, target=user, target_label=user.name,
                    area=area, via=via(request), data={"role": role},
                )
        except IntegrityError:
            raise duplicate(_("The user already has this role in this area."), "role") from None
        return self._respond(assignment, status.HTTP_201_CREATED)

    @extend_schema(summary="Move an assignment to another area", request=RoleAssignmentUpdateSerializer, responses=RoleAssignmentSerializer)
    def partial_update(self, request, user_pk=None, pk=None):
        user = self.get_target_user()
        assignment = self.get_assignment(user)
        data = RoleAssignmentUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        area = data.validated_data["area"]
        check_assignment_allowed(request.user, assignment.role, area)
        old_area = assignment.area
        assignment.area_level, assignment.area_id = area.level, area.id
        try:
            with transaction.atomic():
                assignment.save(update_fields=["area_level", "area_id"])
                record(
                    "user.reassigned", actor=request.user, target=user, target_label=user.name, area=area,
                    via=via(request),
                    data={"role": assignment.role, "from": [old_area.level, old_area.id], "to": [area.level, area.id]},
                )
        except IntegrityError:
            raise duplicate(_("The user already has this role in this area."), "area") from None
        return self._respond(assignment)

    @extend_schema(summary="Remove an assignment", responses={204: None})
    def destroy(self, request, user_pk=None, pk=None):
        user = self.get_target_user()
        assignment = self.get_assignment(user)
        with transaction.atomic():
            assignment.delete()
            record(
                "user.assignment_removed", actor=request.user, target=user, target_label=user.name,
                area=assignment.area, via=via(request), data={"role": assignment.role},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
