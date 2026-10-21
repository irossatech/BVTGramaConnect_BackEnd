import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.geography.areas import AreaRef, area_exists
from apps.geography.models import AreaLevel

from .roles import ROLE_LEVEL, Role

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


class UserStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    BLOCKED = "BLOCKED", _("Blocked")


def normalize_login(value) -> str:
    """Usernames and emails are compared and stored in lower case, trimmed."""
    return (value or "").strip().lower()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(**{self.model.USERNAME_FIELD: normalize_login(username)})

    def _create_user(self, username, name, password, **extra_fields):
        username = normalize_login(username or extra_fields.get("email") or extra_fields.get("mobile"))
        if not username:
            raise ValueError("A username, email or mobile number is required.")
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

    def create_superuser(self, username=None, email=None, name="", password=None, **extra_fields):
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True
        return self._create_user(username, name, password, email=email, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """An official: Super Admin, District, Mandal or Village Admin, or Panchayat member.

    Citizens have no account. Officials log in with a username (or their email)
    and a password. An admin invites them; they get an email with their
    username and their role's default password, which they must change at
    first login when FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN is on.

    is_superuser and is_staff are Django admin flags for developers. They are
    not the SUPER_ADMIN platform role, which comes from role assignments.

    token_version is copied into every JWT. Blocking a user, changing the
    password or logging out on all devices increments it, so every token
    issued before stops working on the next request.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        validators=[username_validator],
        help_text=_("Stored in lower case. People may also log in with their email."),
    )
    email = models.EmailField(_("email"), max_length=254, unique=True, null=True, blank=True)
    mobile = models.CharField(
        _("mobile number"),
        max_length=10,
        unique=True,
        null=True,
        blank=True,
        validators=[mobile_validator],
        help_text=_("10 digits, without +91. Optional."),
    )
    name = models.CharField(_("name"), max_length=150)
    name_te = models.CharField(_("name in Telugu"), max_length=150, blank=True)
    status = models.CharField(
        _("status"), max_length=10, choices=UserStatus.choices, default=UserStatus.ACTIVE, db_index=True
    )
    preferred_language = models.CharField(
        _("preferred language"), max_length=2, choices=Language.choices, default=Language.TELUGU
    )
    must_change_password = models.BooleanField(
        _("must change password"),
        default=False,
        help_text=_("Set after an invite or a reset; cleared when the person picks their own password."),
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
    REQUIRED_FIELDS = ["email", "name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.username})"

    def save(self, *args, **kwargs):
        self.username = normalize_login(self.username)
        self.email = normalize_login(self.email) or None
        self.mobile = (self.mobile or "").strip() or None
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """Django and simplejwt read this. Status is the single source of truth."""
        return self.status == UserStatus.ACTIVE

    @property
    def is_blocked(self) -> bool:
        return self.status == UserStatus.BLOCKED


class RoleAssignment(models.Model):
    """One role in one area. A user may hold several.

    The area is a (level, id) pair rather than a foreign key, so one table
    covers all four levels. The level must match the role (ROLE_LEVEL).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    area_level = models.CharField(_("area level"), max_length=10, choices=AreaLevel.choices)
    area_id = models.PositiveIntegerField(_("area id"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("role assignment")
        verbose_name_plural = _("role assignments")
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "area_level", "area_id"], name="unique_role_assignment"
            ),
        ]
        indexes = [
            models.Index(fields=["area_level", "area_id"]),
            models.Index(fields=["role", "area_level", "area_id"]),
        ]

    def __str__(self):
        return f"{self.get_role_display()} · {self.area_level} {self.area_id}"

    @property
    def area(self) -> AreaRef:
        return AreaRef(self.area_level, self.area_id)

    def clean(self):
        expected = ROLE_LEVEL.get(self.role)
        if expected and self.area_level != expected:
            raise ValidationError({"area_level": _("This role is assigned at %(level)s level.") % {"level": expected}})
        if self.area_level and self.area_id and not area_exists(self.area):
            raise ValidationError({"area_id": _("No such area.")})


class RoleDefaultPassword(models.Model):
    """The temporary password new and reset officials of one role receive by email.

    The Super Admin can change it. It is kept in plain text because it has to
    be emailed; it only works until the person sets their own password.
    A role without a row gets a random password on first use.
    """

    role = models.CharField(_("role"), max_length=20, choices=Role.choices, unique=True)
    password = models.CharField(_("default password"), max_length=128)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = _("default password for a role")
        verbose_name_plural = _("default passwords for roles")

    def __str__(self):
        return self.get_role_display()


class LoginAttempt(models.Model):
    """One login try, kept to rate-limit password guessing per username and per IP."""

    username = models.CharField(max_length=254, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["username", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
        ]

    def __str__(self):
        return f"{self.username} {'ok' if self.success else 'failed'} {self.created_at:%Y-%m-%d %H:%M}"
