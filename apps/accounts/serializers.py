import re

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel

from .models import Language, RoleAssignment, User, UserStatus, mobile_validator, normalize_login, username_validator
from .roles import Role
from .scope import capabilities


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


class RoleAssignmentSerializer(serializers.ModelSerializer):
    area = serializers.SerializerMethodField()

    class Meta:
        model = RoleAssignment
        fields = ["id", "role", "area", "created_at"]

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
    manageable_roles = serializers.ListField(child=serializers.ChoiceField(choices=Role.choices))
    is_super_admin = serializers.BooleanField()


class RulesSerializer(serializers.Serializer):
    admin_posts_auto_publish = serializers.BooleanField()
    super_admin_can_post = serializers.BooleanField()
    max_photos_per_post = serializers.IntegerField()
    video_enabled = serializers.BooleanField()


class MeSerializer(serializers.ModelSerializer):
    role_assignments = RoleAssignmentSerializer(many=True, read_only=True)
    capabilities = serializers.SerializerMethodField()
    rules = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "name",
            "name_te",
            "mobile",
            "preferred_language",
            "status",
            "must_change_password",
            "role_assignments",
            "capabilities",
            "rules",
        ]

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


CLIENTS = [("app", "Mobile app"), ("web_admin", "Super Admin console")]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=254, help_text=_("Username or email."))
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})
    client = serializers.ChoiceField(choices=CLIENTS, default="app")


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})
    new_password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})
    client = serializers.ChoiceField(choices=CLIENTS, required=False)


class RolePasswordSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)
    password = serializers.CharField()
    updated_at = serializers.DateTimeField()
    updated_by = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_updated_by(self, obj):
        user = obj.updated_by
        return {"id": str(user.id), "name": user.name} if user else None


class RolePasswordUpdateSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(required=False, help_text=_("Omitted for the console, which gets a cookie."))


class LoginResponseSerializer(TokenPairSerializer):
    user = MeSerializer()


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, help_text=_("Leave out to use the console's cookie."))


class LogoutSerializer(RefreshSerializer):
    all_devices = serializers.BooleanField(default=False)


# --- User management -------------------------------------------------------------------


class UserBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    role_assignments = RoleAssignmentSerializer(many=True, read_only=True)
    last_login_at = serializers.DateTimeField(source="last_login", read_only=True)
    created_by = UserBriefSerializer(read_only=True)
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
            "status",
            "preferred_language",
            "must_change_password",
            "role_assignments",
            "last_login_at",
            "created_at",
            "created_by",
            "allowed_actions",
        ]

    def get_allowed_actions(self, obj) -> list[str]:
        # Every user this serializer sees came from the actor's manageable set.
        toggle = "unblock" if obj.status == UserStatus.BLOCKED else "block"
        return ["edit", toggle, "reset_password", "add_assignment", "reassign", "remove_assignment"]


class UserCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    name_te = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(max_length=254)
    mobile = MobileField(required=False, allow_null=True, allow_blank=True, default=None)
    username = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default="",
        help_text=_("Leave empty to use the email."),
    )
    preferred_language = serializers.ChoiceField(choices=Language.choices, default=Language.TELUGU)
    role = serializers.ChoiceField(choices=Role.choices)
    area = AreaInputSerializer()

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Enter a name."))
        return value

    def validate_email(self, value):
        return normalize_login(value)

    def validate_mobile(self, value):
        return value or None

    def validate(self, attrs):
        username = normalize_login(attrs.get("username")) or attrs["email"]
        try:
            username_validator(username)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"username": list(exc.messages)}) from None
        attrs["username"] = username
        return attrs


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "name_te", "preferred_language", "email", "mobile"]
        extra_kwargs = {"email": {"required": False}, "mobile": {"required": False}}

    def validate_email(self, value):
        value = normalize_login(value) or None
        if value and User.objects.filter(email=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError(_("This email already belongs to an official."))
        return value

    def validate_mobile(self, value):
        return value or None


class BlockSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RoleAssignmentCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)
    area = AreaInputSerializer()


class RoleAssignmentUpdateSerializer(serializers.Serializer):
    area = AreaInputSerializer()


class UserCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    blocked = serializers.IntegerField()
    by_role = serializers.DictField(child=serializers.IntegerField())
