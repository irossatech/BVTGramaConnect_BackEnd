"""Important numbers API. See docs/api-contract.md, section 14. Mounted under /api/v1/."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views_admin, views_public

app_name = "contacts"

router = SimpleRouter()
router.register("admin/contacts", views_admin.AdminContactViewSet, basename="admin-contact")
router.register("admin/contact-types", views_admin.AdminContactTypeViewSet, basename="admin-contact-type")

urlpatterns = [
    path("contacts/", views_public.CitizenContactsView.as_view(), name="contacts"),
    path("contact-types/", views_public.ContactTypeListView.as_view(), name="contact-types"),
    # Before the router, so "import" is not taken for a number's id.
    path("admin/contacts/import/", views_admin.ContactImportView.as_view(), name="contact-import"),
    *router.urls,
]
