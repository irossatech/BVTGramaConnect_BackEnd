from django.contrib import admin

from .models import ServiceRequest


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "service", "status", "village", "assigned_to", "created_at")
    list_filter = ("service", "status")
    readonly_fields = ("device", "created_at", "updated_at")
