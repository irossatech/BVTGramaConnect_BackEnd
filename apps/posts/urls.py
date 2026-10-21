"""Posts, review, categories, stats and settings. docs/api-contract.md, sections 5 and 10.

Mounted under /api/v1/.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

app_name = "posts"

router = SimpleRouter()
router.register("posts", views.PostViewSet, basename="post")
router.register("admin/categories", views.AdminCategoryViewSet, basename="admin-category")

urlpatterns = [
    path("categories/", views.CategoryListView.as_view(), name="categories"),
    path("review/queue/", views.ReviewQueueView.as_view(), name="review-queue"),
    path("stats/", views.StatsView.as_view(), name="stats"),
    path("admin/settings/", views.AdminSettingsView.as_view(), name="admin-settings"),
    *router.urls,
]
