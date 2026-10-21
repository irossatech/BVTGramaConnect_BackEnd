from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "mobile", "village_text", "language", "handled")
    list_filter = ("handled", "language")
    search_fields = ("name", "mobile", "village_text", "message")
    readonly_fields = ("name", "mobile", "village_text", "message", "language", "ip_address", "created_at")
    fields = readonly_fields + ("handled", "handled_note")

    def has_add_permission(self, request):
        return False
