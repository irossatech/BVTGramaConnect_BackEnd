from django.contrib import admin

from .models import CitizenDevice


@admin.register(CitizenDevice)
class CitizenDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "village_id", "integrity_checked", "created_at", "blocked_at")
    list_filter = ("platform", "integrity_checked")
    readonly_fields = ("token_hash", "registered_ip_hash", "nonce", "created_at", "last_seen")
