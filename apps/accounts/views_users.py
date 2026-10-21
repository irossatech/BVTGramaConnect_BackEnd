"""User management. docs/api-contract.md, section 3, and the matrix in docs/authorization.md.

Every rule comes from permissions (accounts.authz), checked per area:
- Giving, moving or removing a role needs that role's manage permission
  (accounts.manage_village_admin, ...) over the area, through an assignment
  ranked strictly higher than the role.
- Blocking, resetting a password, unlocking and moving a role also need
  accounts.block_user, reset_password, unlock_user or reassign_user, under the
  same rule, for every assignment the person holds.

Users outside the actor's reach are invisible: reading or changing them
answers 404. Creating a user or an assignment in an area outside it answers
403 out_of_area; a role the actor may not give at all answers 403 permission_denied.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.services import record
from apps.geography.areas import AreaRef, area_exists, within_q
from apps.geography.models import AreaLevel
from grama.exceptions import ApiError, out_of_area, permission_denied

from . import authz, lockout
from .authentication import client_via
from .authz import ANYWHERE, RoleInfo
from .credentials import check_new_password, set_temporary_password
from .models import RoleAssignment, User
from .permissions import ManagesUsers
from .serializers import (
    BlockSerializer,
    PasswordResetSerializer,
    RoleAssignmentCreateSerializer,
    RoleAssignmentSerializer,
    RoleAssignmentUpdateSerializer,
    UserCountsSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
    prime_describer,
)
from .tokens import block_user, revoke_all_sessions, unblock_user


def via(request) -> str:
    return client_via(request)


def check_assignment_allowed(actor, role: RoleInfo, area: AreaRef) -> None:
    """Raise unless `actor` may give `role` in `area` to someone."""
    if role.level != area.level:
        raise ApiError(
            "validation_error",
            _("This role is assigned to a %(level)s.") % {"level": role.level.lower()},
            fields={"area": [_("Must be a %(level)s.") % {"level": role.level.lower()}]},
        )
    if not area_exists(area, active_only=True):
        raise ApiError(
            "validation_error",
            _("No such active area."),
            fields={"area": [_("No such active area.")]},
        )
    if not authz.can_assign(actor, role, ANYWHERE):
        raise permission_denied(_("Your role cannot give this role."))
    if not authz.can_assign(actor, role, area):
        raise out_of_area()


def require_action(actor, perm: str, target: User, message) -> None:
    if not authz.can_act_on(actor, perm, target):
        raise permission_denied(message)


def duplicate(message, field: str) -> ApiError:
    return ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={field: [message]})


def assignments_prefetch() -> Prefetch:
    return Prefetch("role_assignments", queryset=RoleAssignment.objects.select_related("group__role_group"))


LIST_PARAMETERS = [
    OpenApiParameter("role", OpenApiTypes.STR, description="Role key, for example VILLAGE_ADMIN."),
    OpenApiParameter("status", OpenApiTypes.STR, enum=["ACTIVE", "BLOCKED"]),
    OpenApiParameter("level", OpenApiTypes.STR, enum=[lv.value for lv in AreaLevel], description="With area_id: users inside that area."),
    OpenApiParameter("area_id", OpenApiTypes.INT),
    OpenApiParameter("district", OpenApiTypes.INT, description="Short for level=DISTRICT&area_id=…"),
    OpenApiParameter("q", OpenApiTypes.STR, description="Name, Telugu name, username, email or mobile."),
]

CREATED_RESPONSE = inline_serializer(
    "UserCreated",
    fields={"user": UserSerializer(), "temporary_password": serializers.CharField(required=False)},
)


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
            authz.manageable_users(self.request.user)
            .select_related("created_by")
            .prefetch_related(assignments_prefetch())
            .order_by("name", "id")
        )

    def _data(self, users, *, many=False):
        rows = list(users) if many else [users]
        context = self.get_serializer_context()
        prime_describer(context, rows)
        authz.prime(self.request.user, [ra.area for user in rows for ra in user.role_assignments.all()])
        context["locked_usernames"] = lockout.locked_usernames([user.username for user in rows])
        return UserSerializer(rows if many else users, many=many, context=context).data

    def _respond(self, user, *, status_code=status.HTTP_200_OK, temporary_password=None, wrap=False):
        data = self._data(self.get_queryset().get(pk=user.pk))
        if not wrap:
            return Response(data, status=status_code)
        body = {"user": data}
        if temporary_password:
            body["temporary_password"] = temporary_password
        return Response(body, status=status_code)

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
        role = (params.get("role") or "").strip().upper()
        if role:
            if role not in authz.roles():
                raise ApiError("validation_error", _("Unknown role."), fields={"role": [_("Unknown role.")]})
            queryset = queryset.filter(id__in=RoleAssignment.objects.of_role(role).values("user_id"))
        user_status = (params.get("status") or "").upper()
        if user_status:
            if user_status not in ("ACTIVE", "BLOCKED"):
                raise ApiError("validation_error", _("Unknown status."), fields={"status": [_("Unknown status.")]})
            queryset = queryset.filter(is_active=user_status == "ACTIVE")
        return queryset

    @staticmethod
    def _counts(queryset) -> dict:
        by_role = (
            RoleAssignment.objects.filter(user__in=queryset.values("id"))
            .values("group__role_group__key")
            .annotate(users=Count("user", distinct=True))
        )
        return {
            "total": queryset.count(),
            "active": queryset.filter(is_active=True).count(),
            "blocked": queryset.filter(is_active=False).count(),
            "by_role": {row["group__role_group__key"]: row["users"] for row in by_role if row["group__role_group__key"]},
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
        response = self.get_paginated_response(self._data(page, many=True))
        response.data["counts"] = counts
        return response

    @extend_schema(summary="One user you may manage")
    def retrieve(self, request, *args, **kwargs):
        return Response(self._data(self.get_object()))

    # --- Writing ----------------------------------------------------------------

    @extend_schema(
        summary="Add an official with a temporary password (generated and shown once when left out)",
        request=UserCreateSerializer,
        responses={201: CREATED_RESPONSE},
    )
    def create(self, request, *args, **kwargs):
        data = UserCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        role, area = values["role"], values["area"]
        check_assignment_allowed(request.user, role, area)
        if User.objects.filter(username=values["username"]).exists():
            raise duplicate(_("This username is taken."), "username")
        if values["mobile"] and User.objects.filter(mobile=values["mobile"]).exists():
            raise duplicate(_("This mobile number already belongs to an official."), "mobile")
        user = User(
            username=values["username"],
            email=values["email"],
            mobile=values["mobile"],
            name=values["name"],
            name_te=values["name_te"],
            preferred_language=values["preferred_language"],
            created_by=request.user,
        )
        if values["password"]:
            check_new_password(values["password"], user)

        with transaction.atomic():
            user.set_unusable_password()
            user.save()
            generated = set_temporary_password(user, values["password"] or None)
            assignment = RoleAssignment.objects.create(
                user=user, group_id=role.group_id, area_level=area.level, area_id=area.id, assigned_by=request.user
            )
            record(
                "user.created", actor=request.user, target=user, target_label=user.name,
                area=assignment.area, via=via(request),
                data={"role": role.key, "password": "generated" if generated else "set by admin"},
            )
        return self._respond(user, status_code=status.HTTP_201_CREATED, temporary_password=generated, wrap=True)

    @extend_schema(summary="Change a user's name, language or contact details", request=UserUpdateSerializer, responses=UserSerializer)
    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        data = UserUpdateSerializer(user, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        record("user.updated", actor=request.user, target=user, target_label=user.name, via=via(request), data=dict(data.validated_data))
        return self._respond(user)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    @extend_schema(summary="Block a user and end all their sessions", request=BlockSerializer, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        user = self.get_object()
        require_action(request.user, "accounts.block_user", user, _("Your role cannot block this user."))
        reason = BlockSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        if user.is_active:
            with transaction.atomic():
                block_user(user)
                record(
                    "user.blocked", actor=request.user, target=user, target_label=user.name, via=via(request),
                    data={"reason": reason.validated_data.get("reason", "")},
                )
        return self._respond(user)

    @extend_schema(summary="Unblock a user", request=None, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        user = self.get_object()
        require_action(request.user, "accounts.block_user", user, _("Your role cannot unblock this user."))
        if not user.is_active:
            with transaction.atomic():
                unblock_user(user)
                record("user.unblocked", actor=request.user, target=user, target_label=user.name, via=via(request))
        return self._respond(user)

    @extend_schema(
        summary="Set a new temporary password (generated and shown once when left out); all sessions end",
        request=PasswordResetSerializer,
        responses=CREATED_RESPONSE,
    )
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        require_action(request.user, "accounts.reset_password", user, _("Your role cannot reset this password."))
        data = PasswordResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        chosen = data.validated_data["password"]
        if chosen:
            check_new_password(chosen, user)
        with transaction.atomic():
            generated = set_temporary_password(user, chosen or None)
            revoke_all_sessions(user)
            record(
                "user.password_reset", actor=request.user, target=user, target_label=user.name, via=via(request),
                data={"password": "generated" if generated else "set by admin"},
            )
        return self._respond(user, temporary_password=generated, wrap=True)

    @extend_schema(summary="Unlock a user locked out by failed logins", request=None, responses=UserSerializer)
    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        user = self.get_object()
        require_action(request.user, "accounts.unlock_user", user, _("Your role cannot unlock this user."))
        with transaction.atomic():
            cleared = lockout.unlock(user)
            record(
                "user.unlocked", actor=request.user, target=user, target_label=user.name, via=via(request),
                data={"attempts_cleared": cleared},
            )
        return self._respond(user)


class RoleAssignmentViewSet(viewsets.GenericViewSet):
    """Assignments of one user the actor may manage."""

    permission_classes = [IsAuthenticated, ManagesUsers]
    serializer_class = RoleAssignmentSerializer

    def get_target_user(self) -> User:
        queryset = authz.manageable_users(self.request.user).prefetch_related(assignments_prefetch())
        return get_object_or_404(queryset, pk=self.kwargs["user_pk"])

    def get_assignment(self, user) -> RoleAssignment:
        return get_object_or_404(RoleAssignment.objects.with_group(), pk=self.kwargs["pk"], user=user)

    def _respond(self, assignment, status_code=status.HTTP_200_OK):
        assignment = RoleAssignment.objects.with_group().get(pk=assignment.pk)
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
                    user=user, group_id=role.group_id, area_level=area.level, area_id=area.id, assigned_by=request.user
                )
                record(
                    "user.assignment_added", actor=request.user, target=user, target_label=user.name,
                    area=area, via=via(request), data={"role": role.key},
                )
        except IntegrityError:
            raise duplicate(_("The user already has this role in this area."), "role") from None
        return self._respond(assignment, status.HTTP_201_CREATED)

    @extend_schema(
        summary="Move an assignment to another area, or switch it off and on",
        request=RoleAssignmentUpdateSerializer,
        responses=RoleAssignmentSerializer,
    )
    def partial_update(self, request, user_pk=None, pk=None):
        user = self.get_target_user()
        assignment = self.get_assignment(user)
        data = RoleAssignmentUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        role = authz.role_info(assignment.group.role_group)
        fields = []
        audit = {"role": role.key}
        area = data.validated_data.get("area")
        if area is not None:
            check_assignment_allowed(request.user, role, area)
            require_action(request.user, "accounts.reassign_user", user, _("Your role cannot move this user."))
            if area != assignment.area:
                audit.update({"from": [assignment.area_level, assignment.area_id], "to": [area.level, area.id]})
                assignment.area_level, assignment.area_id = area.level, area.id
                fields += ["area_level", "area_id"]
        if "is_active" in data.validated_data and data.validated_data["is_active"] != assignment.is_active:
            assignment.is_active = data.validated_data["is_active"]
            audit["is_active"] = assignment.is_active
            fields.append("is_active")
        if fields:
            try:
                with transaction.atomic():
                    assignment.save(update_fields=fields)
                    action_name = "user.reassigned" if "area_id" in fields else "user.assignment_changed"
                    record(
                        action_name, actor=request.user, target=user, target_label=user.name,
                        area=assignment.area, via=via(request), data=audit,
                    )
            except IntegrityError:
                raise duplicate(_("The user already has this role in this area."), "area") from None
        return self._respond(assignment)

    @extend_schema(summary="Remove an assignment", responses={204: None})
    def destroy(self, request, user_pk=None, pk=None):
        user = self.get_target_user()
        assignment = self.get_assignment(user)
        role = authz.role_info(assignment.group.role_group)
        with transaction.atomic():
            assignment.delete()
            record(
                "user.assignment_removed", actor=request.user, target=user, target_label=user.name,
                area=assignment.area, via=via(request), data={"role": role.key},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
