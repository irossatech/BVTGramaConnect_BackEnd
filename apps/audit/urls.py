"""Audit log API. See docs/api-contract.md, section 8. Mounted under /api/v1/."""

from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("audit/", views.AuditListView.as_view(), name="list"),
    path("audit/export/", views.AuditExportView.as_view(), name="export"),
]
