"""API routes owned by web_public: site config and contact messages.

See docs/api-contract.md, section 9. Mounted under /api/v1/.
"""

from django.urls import path

from . import api

app_name = "web_public_api"

urlpatterns = [
    path("public/site-config/", api.SiteConfigView.as_view(), name="site-config"),
    path("public/contact-messages/", api.ContactMessageView.as_view(), name="contact-messages"),
]
