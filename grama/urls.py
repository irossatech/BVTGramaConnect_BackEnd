"""URL layout.

/api/v1/...    REST API (docs/api-contract.md)
/api/schema/   OpenAPI schema; /api/docs/ shows it in Swagger UI
/django-admin/ Django's built-in admin, for developers only
/console/      Super Admin console (web_admin app)
/media/        uploaded files, while DEBUG is on (Nginx on the server)
/              public website (web_public app); must stay last
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import health

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
]

urlpatterns = [
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("django-admin/", admin.site.urls),
    path("console/", include("apps.web_admin.urls")),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
    path("", include("apps.web_public.urls")),
]
