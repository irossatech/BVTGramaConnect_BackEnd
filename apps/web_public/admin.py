from django.contrib import admin
from django.utils import timezone

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    """The console's Contact messages page is the main place to read these (owner decision Q16)."""

    list_display = ("created_at", "name", "mobile", "village_text", "language", "handled", "handled_at")
    list_filter = ("handled", "language")
    search_fields = ("name", "mobile", "village_text", "message")
    readonly_fields = (
        "name", "mobile", "village_text", "message", "language", "ip_address", "created_at",
        "handled_at", "handled_by",
    )
    fields = readonly_fields + ("handled", "handled_note")

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        if "handled" in form.changed_data:
            obj.handled_at = timezone.now() if obj.handled else None
            obj.handled_by = request.user if obj.handled else None
        super().save_model(request, obj, form, change)
