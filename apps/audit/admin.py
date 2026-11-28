from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only. Entries are written by the code, never by hand."""

    list_display = ("created_at", "actor_name", "actor_role", "action", "target_label", "via")
    list_filter = ("type", "via")
    search_fields = ("actor_name", "action", "target_label")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
