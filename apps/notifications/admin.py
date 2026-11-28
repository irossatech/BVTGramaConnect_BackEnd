from django.contrib import admin

from .models import Device, Notification, NotificationTemplate


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "language", "village_id", "user", "last_seen")
    list_filter = ("platform", "language")
    raw_id_fields = ("user",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "user", "created_at", "read_at")
    list_filter = ("kind",)
    raw_id_fields = ("user", "post")


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name_en", "channel", "recipient", "editable", "updated_at")
    readonly_fields = ("updated_by", "updated_at")
