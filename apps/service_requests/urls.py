from django.urls import path

from . import views

app_name = "service_requests"

urlpatterns = [
    path("citizen/service-requests/", views.CitizenServiceRequestsView.as_view(), name="citizen"),
    path("service-requests/", views.ServiceRequestListView.as_view(), name="list"),
    path("service-requests/<int:pk>/", views.ServiceRequestDetailView.as_view(), name="detail"),
]
