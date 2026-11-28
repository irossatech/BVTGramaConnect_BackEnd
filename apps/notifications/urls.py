"""Devices, inbox and notification texts. See docs/api-contract.md, section 7. Mounted under /api/v1/."""

from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("devices/", views.DeviceRegisterView.as_view(), name="devices"),
    path("devices/<uuid:pk>/", views.DeviceDetailView.as_view(), name="device"),
    path("notifications/", views.NotificationListView.as_view(), name="list"),
    path("notifications/read-all/", views.NotificationReadAllView.as_view(), name="read-all"),
    path("notifications/<uuid:pk>/read/", views.NotificationReadView.as_view(), name="read"),
    path("admin/notification-templates/", views.NotificationTemplateListView.as_view(), name="templates"),
    path("admin/notification-templates/<slug:key>/", views.NotificationTemplateDetailView.as_view(), name="template"),
]
