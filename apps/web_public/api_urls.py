"""API routes owned by web_public: site config, showcase and contact messages.

See docs/api-contract.md, section 9. Mounted under /api/v1/.
"""

from django.urls import path

from . import api

app_name = "web_public_api"

urlpatterns = [
    path("public/site-config/", api.SiteConfigView.as_view(), name="site-config"),
    path("public/contact-messages/", api.ContactMessageView.as_view(), name="contact-messages"),
    path("public/showcase/", api.ShowcaseView.as_view(), name="showcase"),
    path("admin/contact-messages/", api.ContactMessageListView.as_view(), name="admin-contact-messages"),
    path("admin/contact-messages/<int:pk>/", api.ContactMessageDetailView.as_view(), name="admin-contact-message"),
]
