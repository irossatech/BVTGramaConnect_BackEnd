from django.contrib import admin

from .models import Bill


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ["order", "key", "name_en", "name_te", "icon", "color", "is_active"]
    list_filter = ["is_active"]
    readonly_fields = ["key", "created_at", "updated_at"]
    ordering = ["order", "id"]
