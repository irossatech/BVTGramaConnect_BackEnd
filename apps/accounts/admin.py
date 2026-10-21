"""Django admin for developers (/django-admin/, office IP addresses only).

Groups, role groups and role assignments decide what officials may do, so
only users who hold accounts.manage_groups platform-wide (the Super Admin
group) may change them here, and every change to a group's permissions is
audit-logged. Group membership is not edited by hand: it follows the active
role assignments.
"""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from . import authz
from .models import RoleAssignment, RoleGroup, User
from .signals import editing_groups

MANAGE_GROUPS = "accounts.manage_groups"


def may_edit_roles(request) -> bool:
    return authz.can(request.user, MANAGE_GROUPS, authz.PLATFORM)


class RoleEditingOnly:
    """View for staff with the model permission; add, change and delete for role editors only."""

    def has_add_permission(self, request, *args):
        return may_edit_roles(request)

    def has_change_permission(self, request, obj=None):
        return may_edit_roles(request)

    def has_delete_permission(self, request, obj=None):
        return may_edit_roles(request)

    def has_view_permission(self, request, obj=None):
        return may_edit_roles(request) or super().has_view_permission(request, obj)

    def has_module_permission(self, request):
        return may_edit_roles(request) or super().has_module_permission(request)


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("username", "name", "name_te", "email", "mobile")


class UserChangeForm(BaseUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


class RoleAssignmentInline(RoleEditingOnly, admin.TabularInline):
    model = RoleAssignment
    fk_name = "user"
    extra = 0
    fields = ("group", "area_level", "area_id", "is_active", "assigned_by", "assigned_at")
    readonly_fields = ("assigned_by", "assigned_at")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    inlines = [RoleAssignmentInline]
    ordering = ("name",)
    list_display = ("username", "name", "mobile", "is_active", "must_change_password", "is_staff", "last_login")
    list_filter = ("is_active", "must_change_password", "is_staff", "is_superuser", "preferred_language", "groups")
    search_fields = ("username", "email", "mobile", "name", "name_te")
    readonly_fields = ("token_version", "password_changed_at", "last_login", "created_at", "updated_at", "groups")
    fieldsets = (
        (None, {"fields": ("username", "password", "must_change_password", "password_changed_at")}),
        (_("Personal info"), {"fields": ("name", "name_te", "email", "mobile", "preferred_language")}),
        (_("Status"), {"fields": ("is_active", "token_version")}),
        (_("Roles (follow the role assignments below)"), {"fields": ("groups",)}),
        (_("Django admin access"), {"fields": ("is_staff", "is_superuser")}),
        (_("Important dates"), {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "name", "name_te", "email", "mobile", "usable_password", "password1", "password2"),
            },
        ),
    )


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(RoleEditingOnly, admin.ModelAdmin):
    list_display = ("user", "group", "area_level", "area_id", "is_active", "assigned_at")
    list_filter = ("group", "area_level", "is_active")
    search_fields = ("user__name", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("assigned_by", "assigned_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(RoleGroup)
class RoleGroupAdmin(RoleEditingOnly, admin.ModelAdmin):
    """Read here; change role_matrix.py and run sync_role_permissions instead."""

    list_display = ("group", "key", "level", "rank", "platform_wide", "manage_permission")
    readonly_fields = ("group", "key", "name_te", "level", "rank", "platform_wide", "manage_permission", "requires_setting")

    def has_add_permission(self, request, *args):
        return False


admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(RoleEditingOnly, BaseGroupAdmin):
    """Permission edits made here are audit-logged; sync_role_permissions puts the matrix back."""

    def changeform_view(self, request, *args, **kwargs):
        with editing_groups(request.user, "admin"):
            return super().changeform_view(request, *args, **kwargs)

    def delete_view(self, request, *args, **kwargs):
        with editing_groups(request.user, "admin"):
            return super().delete_view(request, *args, **kwargs)
