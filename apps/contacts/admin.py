from django.contrib import admin

from .models import Contact, ContactType


@admin.register(ContactType)
class ContactTypeAdmin(admin.ModelAdmin):
    list_display = ("key", "section", "name_en", "name_te", "is_other", "sort_order", "is_active")
    list_filter = ("section", "is_active")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    """For developers. Officials use the API, which applies the area rules and the audit trail."""

    list_display = ("name", "type", "phone", "phone_kind", "area_level", "area_id", "is_active")
    list_filter = ("type", "area_level", "is_active", "phone_kind")
    search_fields = ("name", "name_te", "phone")
    readonly_fields = [field.name for field in Contact._meta.fields]

    def has_add_permission(self, request):
        return False
