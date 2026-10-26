"""Geography API. See docs/api-contract.md, section 4. Mounted under /api/v1/.

Reads are public. Writes, the console tree and the CSV import are for Super Admins.
"""

from django.urls import path

from . import views
from . import views_admin as admin

app_name = "geography"

urlpatterns = [
    path("geo/states/", views.StateListView.as_view(), name="states"),
    path("geo/districts/", admin.DistrictCollectionView.as_view(), name="districts"),
    path("geo/districts/<int:pk>/", admin.DistrictItemView.as_view(), name="district-detail"),
    path("geo/mandals/", admin.MandalCollectionView.as_view(), name="mandals"),
    path("geo/mandals/<int:pk>/", admin.MandalItemView.as_view(), name="mandal-detail"),
    path("geo/villages/", admin.VillageCollectionView.as_view(), name="villages"),
    path("geo/villages/import/", admin.VillageImportView.as_view(), name="village-import"),
    path("geo/villages/<int:pk>/", admin.VillageItemView.as_view(), name="village-detail"),
    path("geo/nearest/", views.NearestVillageView.as_view(), name="nearest"),
    path("geo/tree/", admin.GeoTreeView.as_view(), name="tree"),
    path("geo/admin/villages/", admin.GeoAdminVillagesView.as_view(), name="admin-villages"),
]
