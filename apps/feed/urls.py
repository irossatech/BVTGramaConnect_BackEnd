"""Public citizen feed, no login. See docs/api-contract.md, section 6. Mounted under /api/v1/."""

from django.urls import path, re_path

from . import views

app_name = "feed"

urlpatterns = [
    path("feed/", views.FeedView.as_view(), name="feed"),
    path("feed/posts/", views.FeedPostsByIdView.as_view(), name="posts-by-id"),
    path("feed/upcoming/", views.UpcomingView.as_view(), name="upcoming"),
    path("feed/urgent/", views.UrgentView.as_view(), name="urgent"),
    path("feed/posts/<uuid:pk>/", views.FeedPostDetailView.as_view(), name="post"),
    re_path(r"^feed/share/(?P<code>[A-Za-z0-9]{1,10})/$", views.FeedShareView.as_view(), name="share"),
]
