"""Console API: dashboard and reports (section 8), Telugu name suggestions (section 19). Under /api/v1/."""

from django.urls import path

from . import api

app_name = "web_admin_api"

urlpatterns = [
    path("admin/dashboard/", api.DashboardView.as_view(), name="dashboard"),
    path("admin/reports/summary/", api.ReportView.as_view(report="summary"), name="report-summary"),
    path("admin/reports/posts-by-area/", api.ReportView.as_view(report="posts-by-area"), name="report-posts-by-area"),
    path("admin/reports/review-time/", api.ReportView.as_view(report="review-time"), name="report-review-time"),
    path("admin/reports/admin-activity/", api.ReportView.as_view(report="admin-activity"), name="report-admin-activity"),
    path("admin/reports/<slug:report>/export/", api.ReportExportView.as_view(), name="report-export"),
    path("admin/transliterate/", api.TransliterateView.as_view(), name="transliterate"),
]
