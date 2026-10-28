from django.contrib import admin

from .models import Ad


@admin.register(Ad)
class AdAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "area_level", "area_id", "starts_on", "ends_on", "is_active")
    list_filter = ("is_active", "area_level")
