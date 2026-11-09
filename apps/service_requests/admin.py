from django.contrib import admin

from .models import Service, ServiceRequest


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("key", "name_en", "is_active", "order")
    readonly_fields = ("key", "created_at", "updated_at")


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    """Read only: a request holds a citizen's name, phone and address, and every change to it
    belongs in its history and the audit log (services.update_request), not in Django admin."""

    list_display = ("id", "service", "status", "village", "assigned_to", "created_at")
    list_filter = ("service", "status")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
