"""Django admin for developers (/django-admin/, office IP addresses only).

Groups, role groups and role assignments decide what officials may do, so
only users who hold accounts.manage_groups platform-wide (the Super Admin
group) may change them here, and every change to a group's permissions is
audit-logged. Group membership is not edited by hand: it follows the active
role assignments.

Passwords and blocking are not done here: "Reset password" and "Block" in the
console or the app end the user's sessions and are audited. Every other change
to a user or a role assignment made here is audited too, with email and mobile
masked.
"""

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from apps.audit.masking import changes
from apps.audit.services import record

from . import authz
from .models import RoleAssignment, RoleGroup, User
from .signals import editing_groups

MANAGE_GROUPS = "accounts.manage_groups"

#: User fields whose changes in Django admin are audited ("user.updated", via "admin").
AUDITED_USER_FIELDS = (
    "username", "name", "name_te", "email", "mobile", "preferred_language",
    "must_change_password", "is_staff", "is_superuser",
)


def may_edit_roles(request) -> bool:
    return authz.can(request.user, MANAGE_GROUPS, authz.PLATFORM)


def audit_assignment(request, assignment: RoleAssignment, action: str, data: dict | None = None) -> None:
    role_group = getattr(assignment.group, "role_group", None)
    record(
        action, actor=request.user, target=assignment.user, target_label=assignment.user.name,
        area=assignment.area, via="admin",
        data={"role": role_group.key if role_group else assignment.group.name, **(data or {})},
    )


def save_assignment(request, assignment: RoleAssignment, change: bool) -> None:
    """Save one assignment made in Django admin and audit it like the API does."""
    before = {}
    if change and assignment.pk:
        old = RoleAssignment.objects.filter(pk=assignment.pk).values("area_level", "area_id", "is_active").first()
        before = old or {}
    else:
        assignment.assigned_by = request.user
    assignment.save()
    if not change:
        audit_assignment(request, assignment, "user.assignment_added")
        return
    changed = changes(before, assignment)
    if changed:
        action = "user.reassigned" if {"area_level", "area_id"} & changed.keys() else "user.assignment_changed"
        audit_assignment(request, assignment, action, {"changed": changed})


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
    # is_active: blocking and unblocking go through the console or the app, which end the sessions.
    readonly_fields = (
        "is_active", "token_version", "password_changed_at", "last_login", "created_at", "updated_at", "groups",
    )
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

    def user_change_password(self, request, id, form_url=""):
        """Not here: a password set in Django admin would leave the user's tokens and sessions working."""
        messages.error(
            request,
            _("Set passwords with \"Reset password\" in the console or the app: it ends the user's sessions "
              "and is audited."),
        )
        return HttpResponseRedirect(reverse("admin:accounts_user_change", args=[id]))

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = User.objects.filter(pk=obj.pk).values(*AUDITED_USER_FIELDS).first()
            before = old or {}
        super().save_model(request, obj, form, change)
        if not change:
            record(
                "user.created", actor=request.user, target=obj, target_label=obj.name, via="admin",
                data={"password": "set by admin" if obj.has_usable_password() else "none"},
            )
            return
        changed = changes(before, obj)
        if changed:
            record("user.updated", actor=request.user, target=obj, target_label=obj.name, via="admin",
                   data={"changed": changed})

    def save_formset(self, request, form, formset, change):
        if formset.model is not RoleAssignment:
            return super().save_formset(request, form, formset, change)
        for assignment in formset.save(commit=False):
            save_assignment(request, assignment, change=not assignment._state.adding)
        for assignment in formset.deleted_objects:
            audit_assignment(request, assignment, "user.assignment_removed")
            assignment.delete()
        formset.save_m2m()


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(RoleEditingOnly, admin.ModelAdmin):
    list_display = ("user", "group", "area_level", "area_id", "is_active", "assigned_at")
    list_filter = ("group", "area_level", "is_active")
    search_fields = ("user__name", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("assigned_by", "assigned_at")

    def save_model(self, request, obj, form, change):
        save_assignment(request, obj, change)

    def delete_model(self, request, obj):
        audit_assignment(request, obj, "user.assignment_removed")
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for assignment in queryset.select_related("user", "group"):
            self.delete_model(request, assignment)


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
