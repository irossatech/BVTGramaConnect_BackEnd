from django.urls import path

from . import views

app_name = "citizens"

urlpatterns = [
    path("citizen/challenge/", views.ChallengeView.as_view(), name="challenge"),
    path("citizen/register/", views.RegisterView.as_view(), name="register"),
    path("citizen/me/", views.MeView.as_view(), name="me"),
    path("citizen/posts/", views.PostsView.as_view(), name="posts"),
]
