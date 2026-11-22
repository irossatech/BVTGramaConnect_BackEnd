from django.urls import path

from . import views, views_admin

app_name = "citizens"

urlpatterns = [
    path("citizen/challenge/", views.ChallengeView.as_view(), name="challenge"),
    path("citizen/register/", views.RegisterView.as_view(), name="register"),
    path("citizen/me/", views.MeView.as_view(), name="me"),
    path("citizen/posts/", views.PostsView.as_view(), name="posts"),
    # The Super Admin's blocked citizens (owner's request of 2026-11-21).
    path("admin/citizens/blocked/", views_admin.BlockedCitizensView.as_view(), name="admin-blocked"),
    path("admin/citizens/<uuid:pk>/blocks/", views_admin.CitizenBlocksView.as_view(), name="admin-blocks"),
    path("admin/citizens/<uuid:pk>/unblock/", views_admin.UnblockCitizenView.as_view(), name="admin-unblock"),
]
