"""Public citizen feed, no login. See docs/api-contract.md, section 6. Mounted under /api/v1/."""

from django.urls import path

from . import views

app_name = "feed"

urlpatterns = [
    path("feed/", views.FeedView.as_view(), name="feed"),
    path("feed/posts/", views.FeedPostsByIdView.as_view(), name="posts-by-id"),
    path("feed/posts/<uuid:pk>/", views.FeedPostDetailView.as_view(), name="post"),
]
