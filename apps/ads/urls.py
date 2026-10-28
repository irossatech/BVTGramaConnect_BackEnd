from django.urls import path

from . import views

app_name = "ads"

admin_list = views.AdminAdViewSet.as_view({"get": "list", "post": "create"})
admin_item = views.AdminAdViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"})

urlpatterns = [
    path("ads/", views.PublicAdsView.as_view(), name="public"),
    path("admin/ads/", admin_list, name="admin-list"),
    path("admin/ads/<int:pk>/", admin_item, name="admin-item"),
]
