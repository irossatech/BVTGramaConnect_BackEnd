from django.urls import path, re_path

from . import views

app_name = "web_admin"

urlpatterns = [
    path("", views.console, name="index"),
    re_path(r"^(?P<path>.+)$", views.console, name="file"),
]
