from django.contrib import admin

from .models import Service, ServiceRequest


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("key", "name_en", "is_active", "order")
    readonly_fields = ("key", "created_at", "updated_at")


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "service", "status", "village", "assigned_to", "created_at")
    list_filter = ("service", "status")
    readonly_fields = ("device", "created_at", "updated_at")
