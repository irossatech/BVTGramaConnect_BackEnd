from django.urls import path

from . import views

app_name = "service_requests"

urlpatterns = [
    path("services/", views.PublicServicesView.as_view(), name="services"),
    path("citizen/service-requests/", views.CitizenServiceRequestsView.as_view(), name="citizen"),
    path("service-requests/", views.ServiceRequestListView.as_view(), name="list"),
    path("service-requests/<int:pk>/", views.ServiceRequestDetailView.as_view(), name="detail"),
    path("admin/services/", views.AdminServicesView.as_view(), name="admin-services"),
    path("admin/services/order/", views.AdminServiceOrderView.as_view(), name="admin-services-order"),
    path("admin/services/<slug:key>/", views.AdminServiceDetailView.as_view(), name="admin-service"),
]
