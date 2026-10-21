from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import LoginAttempt, RoleAssignment, RoleDefaultPassword, User


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("username", "email", "name", "name_te", "mobile")


class UserChangeForm(BaseUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


class RoleAssignmentInline(admin.TabularInline):
    model = RoleAssignment
    fk_name = "user"
    extra = 0
    fields = ("role", "area_level", "area_id", "created_at")
    readonly_fields = ("created_at",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    inlines = [RoleAssignmentInline]
    ordering = ("name",)
    list_display = ("username", "name", "email", "mobile", "status", "must_change_password", "is_staff", "last_login")
    list_filter = ("status", "must_change_password", "is_staff", "is_superuser", "preferred_language")
    search_fields = ("username", "email", "mobile", "name", "name_te")
    readonly_fields = ("token_version", "password_changed_at", "last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("username", "password", "must_change_password", "password_changed_at")}),
        (_("Personal info"), {"fields": ("name", "name_te", "email", "mobile", "preferred_language")}),
        (_("Status"), {"fields": ("status", "token_version")}),
        (_("Django admin access"), {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "name", "name_te", "mobile", "usable_password", "password1", "password2"),
            },
        ),
    )


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "area_level", "area_id", "created_at")
    list_filter = ("role", "area_level")
    search_fields = ("user__name", "user__username", "user__email")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_by", "created_at")


@admin.register(RoleDefaultPassword)
class RoleDefaultPasswordAdmin(admin.ModelAdmin):
    """The temporary password each role's new officials receive. Super Admins
    normally change it in the console (Settings)."""

    list_display = ("role", "updated_at", "updated_by")
    readonly_fields = ("updated_at", "updated_by")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    """Read-only log of login tries, used for rate limiting."""

    list_display = ("username", "success", "ip_address", "created_at")
    list_filter = ("success",)
    search_fields = ("username",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
