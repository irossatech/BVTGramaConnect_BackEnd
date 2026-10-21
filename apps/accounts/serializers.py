import re

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel

from . import authz
from .models import Language, RoleAssignment, RoleGroup, User, mobile_validator, normalize_login, username_validator


def normalize_mobile(value: str) -> str:
    """Accept "+91 98765 43210", "098765 43210" and similar; keep the 10 digits."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


class MobileField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 20)
        super().__init__(**kwargs)
        self.validators.append(mobile_validator)

    def to_internal_value(self, data):
        return normalize_mobile(super().to_internal_value(data))


@extend_schema_field(serializers.CharField(help_text="Role key, for example PANCHAYAT_MEMBER."))
class RoleField(serializers.Field):
    """A role key (DISTRICT_ADMIN, ...) in, the matching authz.RoleInfo out."""

    default_error_messages = {"unknown": _("Unknown role.")}

    def to_internal_value(self, data):
        if not isinstance(data, str) or len(data) > 20:
            self.fail("unknown")
        row = RoleGroup.objects.select_related("group").filter(key=data.strip().upper()).first()
        if row is None:
            self.fail("unknown")
        return authz.role_info(row)

    def to_representation(self, value):
        return getattr(value, "key", value)


# --- Areas ----------------------------------------------------------------------


class AreaInputSerializer(serializers.Serializer):
    level = serializers.ChoiceField(choices=AreaLevel.choices)
    id = serializers.IntegerField(min_value=1)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        return AreaRef(values["level"], values["id"])


class AreaOutputSerializer(serializers.Serializer):
    level = serializers.CharField()
    id = serializers.IntegerField()
    name = serializers.CharField()
    path = serializers.CharField()


def describer_from(context) -> AreaDescriber:
    describer = context.get("describer")
    if describer is None:
        describer = AreaDescriber()
        context["describer"] = describer
    return describer


class GroupBriefSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()


class RoleAssignmentSerializer(serializers.ModelSerializer):
    """One assignment. "role" is the group key, kept for older clients next to "group"."""

    role = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="assigned_at", read_only=True)

    class Meta:
        model = RoleAssignment
        fields = ["id", "role", "group", "area", "is_active", "assigned_at", "created_at"]

    def get_role(self, obj) -> str:
        return obj.group.role_group.key

    @extend_schema_field(GroupBriefSerializer)
    def get_group(self, obj) -> dict:
        return {"key": obj.group.role_group.key, "name": obj.group.name}

    @extend_schema_field(AreaOutputSerializer)
    def get_area(self, obj):
        return describer_from(self.context).describe(obj.area)


def prime_describer(context, users) -> None:
    """Load the names of every assignment area on a page in a few queries."""
    refs = [ra.area for user in users for ra in user.role_assignments.all()]
    describer_from(context).prime(refs)


# --- The logged-in official ---------------------------------------------------------


class CapabilitiesSerializer(serializers.Serializer):
    can_post = serializers.BooleanField()
    can_review = serializers.BooleanField()
    can_manage_users = serializers.BooleanField()
    manageable_roles = serializers.ListField(child=serializers.CharField())
    is_super_admin = serializers.BooleanField()


class RulesSerializer(serializers.Serializer):
    admin_posts_auto_publish = serializers.BooleanField()
    super_admin_can_post = serializers.BooleanField()
    max_photos_per_post = serializers.IntegerField()
    video_enabled = serializers.BooleanField()


class MeGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    level = serializers.CharField()
    rank = serializers.IntegerField()


class AreaPermissionsSerializer(serializers.Serializer):
    area = AreaOutputSerializer()
    permissions = serializers.ListField(child=serializers.CharField())


def capabilities(user) -> dict:
    """Deprecated summary for the mobile app; new code reads `permissions`."""
    user_grants = authz.grants(user, pending_change_ok=True)
    held = {perm for grant in user_grants for perm in grant.permissions}
    manageable = [
        role.key
        for role in authz.roles().values()
        if role.manage_permission
        and any(role.manage_permission in g.permissions and g.rank < role.rank for g in user_grants)
    ]
    return {
        "can_post": "posts.create_post" in held,
        "can_review": "posts.review_post" in held,
        "can_manage_users": bool(manageable),
        "manageable_roles": manageable,
        "is_super_admin": any(g.platform_wide for g in user_grants),
    }


class MeSerializer(serializers.ModelSerializer):
    """GET /me. groups, assignments and permissions only show or hide UI; the server checks every call."""

    groups = serializers.SerializerMethodField()
    assignments = serializers.SerializerMethodField()
    role_assignments = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    permissions_by_area = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    rules = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "name",
            "name_te",
            "mobile",
            "email",
            "preferred_language",
            "is_active",
            "status",
            "must_change_password",
            "groups",
            "assignments",
            "role_assignments",
            "permissions",
            "permissions_by_area",
            "capabilities",
            "rules",
        ]

    def _assignments(self, obj):
        rows = getattr(obj, "_me_assignments", None)
        if rows is None:
            rows = list(obj.role_assignments.select_related("group__role_group").order_by("assigned_at"))
            obj._me_assignments = rows
            describer_from(self.context).prime([row.area for row in rows])
        return rows

    @extend_schema_field(MeGroupSerializer(many=True))
    def get_groups(self, obj):
        seen = {}
        for grant in authz.grants(obj, pending_change_ok=True):
            seen.setdefault(grant.key, {"key": grant.key, "name": grant.name, "level": grant.level, "rank": grant.rank})
        return sorted(seen.values(), key=lambda row: row["rank"])

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_assignments(self, obj):
        describer = describer_from(self.context)
        return [
            {
                "id": str(row.id),
                "group": {"key": row.group.role_group.key, "name": row.group.name},
                "area": describer.describe(row.area),
                "is_active": row.is_active,
            }
            for row in self._assignments(obj)
            if getattr(row.group, "role_group", None) is not None
        ]

    @extend_schema_field(RoleAssignmentSerializer(many=True))
    def get_role_assignments(self, obj):
        rows = [row for row in self._assignments(obj) if row.is_active]
        return RoleAssignmentSerializer(rows, many=True, context=self.context).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissions(self, obj):
        return authz.permissions(obj, pending_change_ok=True)

    @extend_schema_field(AreaPermissionsSerializer(many=True))
    def get_permissions_by_area(self, obj):
        describer = describer_from(self.context)
        pairs = authz.permissions_by_area(obj, pending_change_ok=True)
        describer.prime([area for area, _perms in pairs])
        return [{"area": describer.describe(area), "permissions": perms} for area, perms in pairs]

    @extend_schema_field(CapabilitiesSerializer)
    def get_capabilities(self, obj):
        return capabilities(obj)

    @extend_schema_field(RulesSerializer)
    def get_rules(self, obj):
        return {
            "admin_posts_auto_publish": settings.ADMIN_POSTS_AUTO_PUBLISH,
            "super_admin_can_post": settings.SUPER_ADMIN_CAN_POST,
            "max_photos_per_post": settings.MAX_PHOTOS_PER_POST,
            "video_enabled": settings.VIDEO_POSTS_ENABLED,
        }


class MeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "name_te", "preferred_language"]


# --- Password login -------------------------------------------------------------------


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, help_text=_("Username; case does not matter."))
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})
    new_password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class LoginResponseSerializer(TokenPairSerializer):
    user = MeSerializer()


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False)
    all_devices = serializers.BooleanField(default=False)


# --- User management -------------------------------------------------------------------


class UserBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


#: allowed_actions: the permission each action needs on the user (None: managing them is enough).
USER_ACTIONS = [
    ("edit", None),
    ("block", "accounts.block_user"),
    ("reset_password", "accounts.reset_password"),
    ("unlock", "accounts.unlock_user"),
    ("add_assignment", None),
    ("reassign", "accounts.reassign_user"),
    ("remove_assignment", None),
]


def user_actions(actor, user, *, locked: bool, can_add_roles: bool) -> list[str]:
    actions = []
    for name, perm in USER_ACTIONS:
        if name == "unlock" and not locked:
            continue
        if name == "add_assignment" and not can_add_roles:
            continue
        if not authz.can_act_on(actor, perm, user):
            continue
        if name == "block":
            name = "block" if user.is_active else "unblock"
        actions.append(name)
    return actions


class UserSerializer(serializers.ModelSerializer):
    role_assignments = RoleAssignmentSerializer(many=True, read_only=True)
    last_login_at = serializers.DateTimeField(source="last_login", read_only=True)
    created_by = UserBriefSerializer(read_only=True)
    status = serializers.CharField(read_only=True)
    locked_out = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "name",
            "name_te",
            "mobile",
            "is_active",
            "status",
            "preferred_language",
            "must_change_password",
            "role_assignments",
            "last_login_at",
            "created_at",
            "created_by",
            "locked_out",
            "allowed_actions",
        ]

    def get_locked_out(self, obj) -> bool:
        return obj.username in self.context.get("locked_usernames", set())

    def get_allowed_actions(self, obj) -> list[str]:
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor is None:
            return []
        can_add = self.context.get("can_add_roles")
        if can_add is None:
            can_add = self.context["can_add_roles"] = bool(authz.assignable_roles(actor))
        return user_actions(actor, obj, locked=self.get_locked_out(obj), can_add_roles=can_add)


class UserCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    name_te = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    username = serializers.CharField(max_length=150, help_text=_("Login name; stored in lower case."))
    email = serializers.EmailField(max_length=254, required=False, allow_null=True, allow_blank=True, default=None)
    mobile = MobileField(required=False, allow_null=True, allow_blank=True, default=None)
    preferred_language = serializers.ChoiceField(choices=Language.choices, default=Language.TELUGU)
    role = RoleField()
    area = AreaInputSerializer()
    password = serializers.CharField(
        max_length=128, required=False, allow_blank=True, trim_whitespace=False, default="",
        help_text=_("Temporary password. Leave out to have one generated and shown once."),
    )

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Enter a name."))
        return value

    def validate_username(self, value):
        value = normalize_login(value)
        try:
            username_validator(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from None
        return value

    def validate_email(self, value):
        return normalize_login(value) or None

    def validate_mobile(self, value):
        return value or None


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "name_te", "preferred_language", "email", "mobile"]
        extra_kwargs = {"email": {"required": False}, "mobile": {"required": False}}

    def validate_email(self, value):
        return normalize_login(value) or None

    def validate_mobile(self, value):
        return value or None


class PasswordResetSerializer(serializers.Serializer):
    password = serializers.CharField(
        max_length=128, required=False, allow_blank=True, trim_whitespace=False, default="",
        help_text=_("New temporary password. Leave out to have one generated and shown once."),
    )


class BlockSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RoleAssignmentCreateSerializer(serializers.Serializer):
    role = RoleField()
    area = AreaInputSerializer()


class RoleAssignmentUpdateSerializer(serializers.Serializer):
    area = AreaInputSerializer(required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError({"area": [_("Give a new area or is_active.")]})
        return attrs


class UserCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    blocked = serializers.IntegerField()
    by_role = serializers.DictField(child=serializers.IntegerField())
