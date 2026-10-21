"""Geography API. See docs/api-contract.md, section 4. Mounted under /api/v1/.

Reads are public. Writes, the console tree and the CSV import are for Super Admins.
"""

from django.urls import path

from . import views, views_admin
from .views_admin import create_view, dispatch_by_method, update_view

app_name = "geography"

urlpatterns = [
    path("geo/states/", views.StateListView.as_view(), name="states"),
    path(
        "geo/districts/",
        dispatch_by_method(get=views.DistrictListView, post=create_view("districts")),
        name="districts",
    ),
    path("geo/districts/<int:pk>/", update_view("districts").as_view(), name="district-detail"),
    path(
        "geo/mandals/",
        dispatch_by_method(get=views.MandalListView, post=create_view("mandals")),
        name="mandals",
    ),
    path("geo/mandals/<int:pk>/", update_view("mandals").as_view(), name="mandal-detail"),
    path(
        "geo/villages/",
        dispatch_by_method(get=views.VillageListView, post=create_view("villages")),
        name="villages",
    ),
    path(
        "geo/villages/<int:pk>/",
        dispatch_by_method(get=views.VillageDetailView, patch=update_view("villages")),
        name="village-detail",
    ),
    path("geo/villages/import/", views_admin.VillageImportView.as_view(), name="village-import"),
    path("geo/nearest/", views.NearestVillageView.as_view(), name="nearest"),
    path("geo/tree/", views_admin.GeoTreeView.as_view(), name="tree"),
    path("geo/admin/villages/", views_admin.GeoAdminVillagesView.as_view(), name="admin-villages"),
]
