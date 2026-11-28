from django.contrib import admin

from .models import CitizenDevice


@admin.register(CitizenDevice)
class CitizenDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "village_id", "integrity_checked", "created_at", "blocked_at")
    list_filter = ("platform", "integrity_checked")
    # Blocks are given and lifted only by the Super Admin through the API, which audits them and
    # reaches every install of the phone (citizens.services.block_citizen).
    readonly_fields = (
        "token_hash", "registered_ip_hash", "android_id_hash", "nonce", "integrity_checked",
        "created_at", "last_seen", "blocked_at", "blocked_until", "blocked_by", "block_reason",
    )
