"""URL layout.

/api/v1/...    REST API (docs/api-contract.md)
/api/schema/   OpenAPI schema; /api/docs/ shows it in Swagger UI
/django-admin/ Django's built-in admin, for developers only
/console/      Super Admin console (web_admin app)
/media/        uploaded files, /static/ static files: while SERVE_FILES is on (Nginx on the server)
/              public website (web_public app); must stay last

Unknown addresses under /api/ answer the contract's JSON 404 once DEBUG is off.
"""

from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .sitefiles import serve_media, serve_static
from .views import health

handler404 = "grama.views.not_found"
handler500 = "grama.views.server_error"

api_v1 = [
    path("health/", health, name="health"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.geography.urls")),
    path("", include("apps.posts.urls")),
    path("", include("apps.feed.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.web_public.api_urls")),
    path("", include("apps.web_admin.api_urls")),
    path("", include("apps.contacts.urls")),
    path("", include("apps.citizens.urls")),
    path("", include("apps.service_requests.urls")),
    path("", include("apps.ads.urls")),
    path("", include("apps.bills.urls")),
]

urlpatterns = [
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("django-admin/", admin.site.urls),
    path("console/", include("apps.web_admin.urls")),
    re_path(r"^media/(?P<path>.*)$", serve_media, name="media"),
    re_path(r"^static/(?P<path>.*)$", serve_static, name="static"),
    path("", include("apps.web_public.urls")),
]
