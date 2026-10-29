"""Deleting a contact type: only when no active number uses it, as a soft delete (contact-type-delete plan)."""

from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditLog
from apps.contacts import services
from apps.contacts.models import Contact, ContactType
from grama.exceptions import ApiError

from .conftest import ADMIN_URL, area_body

pytestmark = pytest.mark.django_db
TYPES_URL = "/api/v1/admin/contact-types/"
PUBLIC_TYPES_URL = "/api/v1/contact-types/"
IMPORT_URL = "/api/v1/admin/contacts/import/"


@pytest.fixture
def admin(world, api):
    return api(world.users["super"])


@pytest.fixture
def library(admin):
    """A type added by mistake, nobody uses it yet."""
    body = {"key": "libary", "section": "AREA", "name_en": "Libary", "name_te": "గ్రంథాలయం"}
    assert admin.post(TYPES_URL, body, format="json").status_code == 201
    return ContactType.objects.get(key="libary")


def url(contact_type) -> str:
    return f"{TYPES_URL}{contact_type.id}/"


def keys(response) -> set:
    return {row["key"] for row in response.json()}


def test_an_unused_type_is_deleted_and_hidden_everywhere(world, admin, api, library):
    response = admin.delete(url(library))
    assert response.status_code == 204
    library.refresh_from_db()
    assert library.deleted_at is not None and library.deleted_by == world.users["super"]
    assert "libary" not in keys(api().get(PUBLIC_TYPES_URL))
    assert "libary" not in keys(admin.get(TYPES_URL))
    entry = AuditLog.objects.get(action="contact_type.deleted")
    assert entry.data == {"key": "libary", "section": "AREA", "name_en": "Libary"}
    assert entry.actor == world.users["super"] and entry.target_label == "libary"


def test_a_type_with_active_numbers_is_409_and_unchanged(world, admin, number):
    official = ContactType.objects.get(key="official")
    number("kolakaluru", "official", phone="08644222301")
    number("tenali", "official", phone="08644222302")
    response = admin.delete(url(official))
    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["count"]) == ("type_in_use", 2)
    assert "2 numbers use this type" in error["message"]
    official.refresh_from_db()
    assert official.deleted_at is None and official.is_active
    assert not AuditLog.objects.filter(action="contact_type.deleted").exists()


def test_a_switched_off_type_in_use_is_still_409(admin, number):
    official = ContactType.objects.get(key="official")
    number("kolakaluru", "official")
    official.is_active = False
    official.save()
    assert admin.delete(url(official)).json()["error"]["code"] == "type_in_use"


def test_a_type_whose_numbers_were_all_removed_is_deleted(world, admin, api, library):
    created = admin.post(ADMIN_URL, area_body(world, "kolakaluru", type="libary"), format="json")
    assert created.status_code == 201
    contact_id = created.json()["id"]
    assert admin.delete(f"{ADMIN_URL}{contact_id}/").status_code == 204
    assert admin.delete(url(library)).status_code == 204  # soft delete: no IntegrityError from PROTECT
    removed = Contact.objects.select_related("type").get(id=contact_id)
    assert removed.type == library and removed.is_active is False
    audit = admin.get("/api/v1/audit/", {"q": "contact"})
    assert audit.status_code == 200
    assert {"contact.created", "contact.removed"} <= set(
        AuditLog.objects.filter(target_id=str(contact_id)).values_list("action", flat=True)
    )


def test_the_other_type_is_protected(admin):
    other = ContactType.objects.get(key="other")
    response = admin.delete(url(other))
    assert response.status_code == 409 and response.json()["error"]["code"] == "protected"
    other.refresh_from_db()
    assert other.deleted_at is None


def test_a_deleted_type_cannot_be_used_or_its_key_reused(world, admin, library):
    assert admin.delete(url(library)).status_code == 204
    new_number = admin.post(ADMIN_URL, area_body(world, "kolakaluru", type="libary"), format="json")
    assert new_number.status_code == 400 and "type" in new_number.json()["error"]["fields"]

    contact = admin.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").json()
    moved = admin.patch(f"{ADMIN_URL}{contact['id']}/", {"type": "libary"}, format="json")
    assert moved.status_code == 400 and "type" in moved.json()["error"]["fields"]

    csv = "type,name,phone,district,mandal,village\nlibary,Library,08644222399,Guntur,Tenali,Kolakaluru\n"
    imported = admin.post(IMPORT_URL, {"file": SimpleUploadedFile("n.csv", csv.encode(), "text/csv")}, format="multipart")
    assert imported.status_code == 400
    assert imported.json()["error"]["report"]["errors"][0]["field"] == "type"

    body = {"key": "libary", "section": "AREA", "name_en": "Library", "name_te": "గ్రంథాలయం"}
    again = admin.post(TYPES_URL, body, format="json")
    assert again.status_code == 409
    assert (again.json()["error"]["code"], again.json()["error"]["message"]) == (
        "duplicate", "This key was used by a deleted type.",
    )
    assert admin.patch(url(library), {"name_en": "Library"}, format="json").status_code == 404
    assert not Contact.objects.filter(type=library).exists()


@pytest.mark.parametrize("actor", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_deletes_types(world, api, library, actor):
    assert api(world.users[actor]).delete(url(library)).status_code == 403
    library.refresh_from_db()
    assert library.deleted_at is None


def test_unknown_and_twice_deleted_types_are_404(admin, library):
    assert admin.delete(f"{TYPES_URL}999999/").status_code == 404
    assert admin.delete(url(library)).status_code == 204
    second = admin.delete(url(library))
    assert second.status_code == 404 and second.json()["error"]["code"] == "not_found"
    assert AuditLog.objects.filter(action="contact_type.deleted").count() == 1


# --- A number added while the type is being deleted ------------------------------------------------------
# Real databases serialise the two through the row lock. SQLite has no row locks, so these tests run the
# other side in the middle of one write and check that the second guard still keeps every number on a live type.


def test_a_delete_in_the_middle_of_adding_a_number_never_leaves_it_on_a_deleted_type(world, library):
    real_check = services.check_capacity

    def delete_meanwhile(*args, **kwargs):
        ContactType.objects.filter(pk=library.pk).update(deleted_at=library.created_at)
        return real_check(*args, **kwargs)

    with mock.patch.object(services, "check_capacity", delete_meanwhile), pytest.raises(ApiError) as error:
        services.create_contact(world.users["super"], {**area_body(world, "kolakaluru", type="libary"),
                                                       "area": world.ref("kolakaluru")})
    assert error.value.fields == {"type": ["Unknown contact type."]}
    assert not Contact.objects.filter(type=library).exists()


def test_a_number_added_in_the_middle_of_a_delete_stops_the_delete(world, library, number):
    real_count = services._active_count
    calls = []

    def add_meanwhile(contact_type):
        count = real_count(contact_type)
        if not calls:
            calls.append(contact_type)
            number("kolakaluru", "libary")  # lands right after the first count
        return count

    with mock.patch.object(services, "_active_count", add_meanwhile), pytest.raises(ApiError) as error:
        services.delete_type(world.users["super"], library.pk)
    assert (error.value.error_code, error.value.extra) == ("type_in_use", {"count": 1})
    library.refresh_from_db()
    assert library.deleted_at is None
    assert not Contact.objects.filter(type__deleted_at__isnull=False, is_active=True).exists()
