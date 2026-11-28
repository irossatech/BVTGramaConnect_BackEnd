from django.urls import path

from . import views

app_name = "bills"

urlpatterns = [
    path("bills/", views.PublicBillsView.as_view(), name="bills"),
    path("admin/bills/", views.AdminBillsView.as_view(), name="admin-bills"),
    # Before the key route, so "order" is never read as a bill key.
    path("admin/bills/order/", views.AdminBillOrderView.as_view(), name="admin-bill-order"),
    path("admin/bills/<slug:key>/", views.AdminBillDetailView.as_view(), name="admin-bill"),
]
