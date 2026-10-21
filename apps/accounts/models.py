import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, Group, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.geography.areas import AreaRef, area_exists
from apps.geography.models import AreaLevel

mobile_validator = RegexValidator(
    regex=r"^[6-9]\d{9}$",
    message=_("Enter a 10-digit mobile number starting with 6, 7, 8 or 9."),
)

username_validator = RegexValidator(
    regex=r"^[a-z0-9@._+-]{3,150}$",
    message=_("Use 3 to 150 letters, digits or @ . _ + - characters."),
)


class Language(models.TextChoices):
    TELUGU = "te", "తెలుగు"
    ENGLISH = "en", "English"


def normalize_login(value) -> str:
    """Usernames (and emails) are compared and stored in lower case, trimmed."""
    return (value or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD: normalize_login(username)})

    def _create_user(self, username, name, password, **extra_fields):
        username = normalize_login(username)
        if not username:
            raise ValueError("A username is required.")
        user = self.model(username=username, name=name, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, username=None, name="", password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, name, password, **extra_fields)

    def create_superuser(self, username=None, name="", password=None, **extra_fields):
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True
        return self._create_user(username, name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """An official: Super Admin, District, Mandal or Village Admin, or Panchayat member.

    Citizens have no account. Officials log in with their username and a
    password; the username is unique and compared without regard to case.
    Mobile and email are contact details only, never login names.

    The admin who adds someone, or resets their password, sets a temporary
    password (or lets the server generate one, shown once). must_change_password
    then blocks everything except choosing a new password.

    What an official may do comes from their RoleAssignments (a group in an
    area), checked by apps/accounts/authz.py. is_superuser and is_staff are
    Django admin flags for developers; they grant nothing in the API.

    token_version is copied into every JWT and every console session. Blocking,
    a password change or reset, or "log out everywhere" increments it, which
    ends every session at once.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        validators=[username_validator],
        help_text=_("Stored in lower case; login ignores case."),
    )
    email = models.EmailField(_("email"), max_length=254, null=True, blank=True, help_text=_("Contact only."))
    mobile = models.CharField(
        _("mobile number"),
        max_length=10,
        unique=True,
        null=True,
        blank=True,
        validators=[mobile_validator],
        help_text=_("10 digits, without +91. Contact only, optional."),
    )
    name = models.CharField(_("name"), max_length=150)
    name_te = models.CharField(_("name in Telugu"), max_length=150, blank=True)
    is_active = models.BooleanField(
        _("active"),
        default=True,
        db_index=True,
        help_text=_("Untick to block: every session and token stops working at once."),
    )
    preferred_language = models.CharField(
        _("preferred language"), max_length=2, choices=Language.choices, default=Language.TELUGU
    )
    must_change_password = models.BooleanField(
        _("must change password"),
        default=False,
        help_text=_("Set for a new account or after a reset; cleared when the person picks their own password."),
    )
    password_changed_at = models.DateTimeField(_("password changed"), null=True, blank=True)
    token_version = models.PositiveIntegerField(default=0, editable=False)
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Can log in to Django admin."),
    )
    created_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", editable=False
    )
    created_at = models.DateTimeField(_("created"), default=timezone.now, editable=False)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["name"]
        permissions = [
            ("manage_district_admin", "Give, move and remove the District Admin role"),
            ("manage_mandal_admin", "Give, move and remove the Mandal Admin role"),
            ("manage_village_admin", "Give, move and remove the Village Admin role"),
            ("manage_panchayat_member", "Give, move and remove the Panchayat Member role"),
            ("block_user", "Block and unblock officials"),
            ("reset_password", "Reset the password of an official"),
            ("reassign_user", "Move the role of an official to another area"),
            ("unlock_user", "Unlock an official locked out by failed logins"),
            ("manage_groups", "Edit groups and role assignments in Django admin"),
        ]

    def __str__(self):
        return f"{self.name} ({self.username})"

    def save(self, *args, **kwargs):
        self.username = normalize_login(self.username)
        self.email = normalize_login(self.email) or None
        self.mobile = (self.mobile or "").strip() or None
        super().save(*args, **kwargs)

    @property
    def is_blocked(self) -> bool:
        return not self.is_active

    @property
    def status(self) -> str:
        """ACTIVE or BLOCKED, kept in API output for older clients."""
        return "ACTIVE" if self.is_active else "BLOCKED"


class RoleGroup(models.Model):
    """What the code needs to know about a group, so it never tests group names.

    Filled from role_matrix.ROLE_GROUPS by sync_role_permissions.
    """

    group = models.OneToOneField(Group, on_delete=models.CASCADE, primary_key=True, related_name="role_group")
    key = models.CharField(_("key"), max_length=20, unique=True, help_text=_("Sent to clients as the role."))
    name_te = models.CharField(_("name in Telugu"), max_length=60, blank=True)
    level = models.CharField(_("area level"), max_length=10, choices=AreaLevel.choices)
    rank = models.PositiveSmallIntegerField(_("rank"), unique=True, help_text=_("0 is the highest."))
    platform_wide = models.BooleanField(_("platform-wide"), default=False)
    manage_permission = models.CharField(
        _("permission needed to give this role"),
        max_length=100,
        blank=True,
        help_text=_("Empty: nobody can give it through the API."),
    )
    requires_setting = models.JSONField(
        _("permissions that need a setting"),
        default=dict,
        blank=True,
        help_text=_("For example {\"posts.create_post\": \"SUPER_ADMIN_CAN_POST\"}."),
    )

    class Meta:
        verbose_name = _("role group")
        verbose_name_plural = _("role groups")
        ordering = ["rank"]

    def __str__(self):
        return self.group.name

    def label(self, language: str = "te") -> str:
        return self.name_te if (language or "te").startswith("te") and self.name_te else self.group.name


class RoleAssignmentQuerySet(models.QuerySet):
    def with_group(self):
        return self.select_related("group__role_group")

    def of_role(self, key: str):
        return self.filter(group__role_group__key=key)


class RoleAssignmentManager(models.Manager.from_queryset(RoleAssignmentQuerySet)):
    def assign(self, user, role: str, area: AreaRef, *, assigned_by=None, is_active=True):
        """Create an assignment from a role key: assign(user, key, AreaRef(level, area_id)) with a key such as VILLAGE_ADMIN."""
        group = RoleGroup.objects.select_related("group").get(key=role).group
        return self.create(
            user=user,
            group=group,
            area_level=area.level,
            area_id=area.id,
            assigned_by=assigned_by,
            is_active=is_active,
        )


class RoleAssignment(models.Model):
    """One group (a role) in one area. A user may hold several.

    The area is a (level, id) pair rather than a foreign key, so one table
    covers all four levels. The level must match the group's RoleGroup.level.
    Only active assignments grant permissions. The user's Django group
    membership follows their active assignments (accounts/signals.py).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_assignments")
    group = models.ForeignKey(
        Group, on_delete=models.PROTECT, related_name="role_assignments", verbose_name=_("role")
    )
    area_level = models.CharField(_("area level"), max_length=10, choices=AreaLevel.choices)
    area_id = models.PositiveIntegerField(_("area id"))
    is_active = models.BooleanField(_("active"), default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    assigned_at = models.DateTimeField(_("assigned"), default=timezone.now)

    objects = RoleAssignmentManager()

    class Meta:
        verbose_name = _("role assignment")
        verbose_name_plural = _("role assignments")
        ordering = ["assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group", "area_level", "area_id"], name="unique_group_assignment"
            ),
        ]
        indexes = [
            models.Index(fields=["area_level", "area_id"], name="accounts_ra_area_idx"),
            models.Index(fields=["group", "area_level", "area_id"], name="accounts_ra_group_area_idx"),
        ]

    def __str__(self):
        return f"{self.group.name} · {self.area_level} {self.area_id}"

    @property
    def area(self) -> AreaRef:
        return AreaRef(self.area_level, self.area_id)

    @property
    def role(self) -> str:
        """The key of the group, for display and records."""
        return self.group.role_group.key

    def clean(self):
        role_group = RoleGroup.objects.filter(group_id=self.group_id).first() if self.group_id else None
        if self.group_id and role_group is None:
            raise ValidationError({"group": _("This group is not a role.")})
        if role_group and self.area_level and self.area_level != role_group.level:
            raise ValidationError(
                {"area_level": _("This role is assigned at %(level)s level.") % {"level": role_group.level}}
            )
        if self.area_level and self.area_id and not area_exists(self.area):
            raise ValidationError({"area_id": _("No such area.")})
