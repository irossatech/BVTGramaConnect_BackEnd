from django.urls import path, re_path

from . import views

app_name = "web_public"

urlpatterns = [
    path("", views.site, name="index"),
    # Everything else at the root is a website file, except the prefixes other
    # parts own. Leaving those out, with or without a trailing slash, lets
    # APPEND_SLASH send /console and /django-admin to their real pages.
    re_path(
        r"^(?!(?:api|django-admin|console|static|media)(?:/|$))(?P<path>.+)$",
        views.site,
        name="file",
    ),
]
