"""No full email, mobile or typed secret in the audit log, and Django admin cannot get around it
(audit BE_01: BE-SEC-001, BE-SEC-007, BE-SEC-008)."""

from django.test import Client

from apps.accounts.models import RoleAssignment, User
from apps.audit.masking import mask_email, mask_phone, mask_text
from apps.audit.models import AuditLog
from apps.contacts.models import Contact
from apps.service_requests.models import ServiceRequest
from apps.service_requests.tests.conftest import request_from  # noqa: F401
from apps.citizens.tests.conftest import phone  # noqa: F401
from conftest import TEST_PASSWORD

from .test_auth import login


def test_masks():
    assert mask_phone("9000010001") == "******0001"
    assert mask_email("ravi.k@example.in") == "r***@example.in"
    assert mask_email("no-at-sign") == "n***"
    assert mask_text("hunter2") == "h***"
    assert mask_text("") == mask_phone("") == mask_email(None) == ""


# --- BE-SEC-001: editing a user ----------------------------------------------------------------------


def test_user_edit_audits_what_changed_with_contact_details_masked(world, api):
    victim = world.users["pm_kaza"]
    old_email, old_mobile = victim.email, victim.mobile
    response = api(world.users["va_kaza"]).patch(
        f"/api/v1/users/{victim.id}/",
        {"email": "ravi.kumar@example.in", "mobile": "9876543210", "name_te": "రవి"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    entry = AuditLog.objects.get(action="user.updated", target_id=str(victim.id))
    assert entry.data == {
        "changed": {
            "email": {"from": mask_email(old_email), "to": "r***@example.in"},
            "mobile": {"from": mask_phone(old_mobile), "to": "******3210"},
            "name_te": {"from": "", "to": "రవి"},
        }
    }
    everything = str(entry.data) + entry.target_label
    for secret in ("ravi.kumar@example.in", "9876543210", old_email, old_mobile):
        assert secret not in everything
    # The Super Admin's audit list and export never show them either.
    listed = api(world.users["super"]).get("/api/v1/audit/", {"action": "user.updated"})
    assert "9876543210" not in listed.content.decode() and "ravi.kumar@" not in listed.content.decode()
    exported = api(world.users["super"]).get("/api/v1/audit/export/", {"action": "user.updated"})
    assert "9876543210" not in exported.content.decode("utf-8-sig")


def test_user_edit_without_a_change_writes_no_audit_entry(world, api):
    victim = world.users["pm_kaza"]
    response = api(world.users["va_kaza"]).patch(f"/api/v1/users/{victim.id}/", {"name": victim.name}, format="json")
    assert response.status_code == 200
    assert not AuditLog.objects.filter(action="user.updated").exists()


# --- BE-SEC-007: failed logins -------------------------------------------------------------------------


def test_failed_login_for_an_unknown_name_keeps_one_character(world):
    login("Grama-Secret-Pass1", "anything")  # a password typed into the username box
    entry = AuditLog.objects.get(action="auth.login_failed")
    assert entry.data["username"] == "g***" and entry.target_label == "g***"
    assert "secret" not in str(entry.data).lower() and entry.target_id == ""


def test_lockout_of_an_unknown_name_keeps_one_character(world):
    for _ in range(6):
        login("someone@example.in", "wrong")
    entry = AuditLog.objects.get(action="auth.locked_out")
    assert entry.data["username"] == "s***" and "example.in" not in str(entry.data)


def test_failed_login_for_a_real_account_still_names_it(world):
    login("va_kolakaluru", "wrong")
    assert AuditLog.objects.get(action="auth.login_failed").data["username"] == "va_kolakaluru"


# --- BE-SEC-008: Django admin -----------------------------------------------------------------------------


def staff(user, superuser=False):
    """Django admin as `user`. Editing users and service requests there needs Django's own model
    permissions, which only developers' superuser accounts have (superuser=True)."""
    User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=superuser)
    browser = Client()
    browser.force_login(User.objects.get(pk=user.pk))
    return browser


def test_django_admin_cannot_set_a_password(world):
    target = world.users["pm_kaza"]
    browser = staff(world.users["super"], superuser=True)
    url = f"/django-admin/accounts/user/{target.pk}/password/"
    response = browser.post(url, {"password1": "New-Pass-2026x", "password2": "New-Pass-2026x", "usable_password": "true"})
    assert response.status_code == 302 and response["Location"].endswith(f"/accounts/user/{target.pk}/change/")
    target.refresh_from_db()
    assert target.check_password(TEST_PASSWORD)


def user_form(user, **overrides):
    values = {
        "username": user.username, "name": user.name, "name_te": user.name_te, "email": user.email or "",
        "mobile": user.mobile or "", "preferred_language": user.preferred_language,
        "must_change_password": "", "is_staff": "", "is_superuser": "",
        "role_assignments-TOTAL_FORMS": "0", "role_assignments-INITIAL_FORMS": "0",
        "role_assignments-MIN_NUM_FORMS": "0", "role_assignments-MAX_NUM_FORMS": "1000",
    }
    values.update(overrides)
    return values


def test_django_admin_user_edit_is_audited_and_cannot_block(world):
    target = world.users["pm_kaza"]
    browser = staff(world.users["super"], superuser=True)
    response = browser.post(
        f"/django-admin/accounts/user/{target.pk}/change/",
        user_form(target, mobile="9876543210", is_active=""),  # is_active is read-only: ignored
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    target.refresh_from_db()
    assert target.mobile == "9876543210" and target.is_active
    entry = AuditLog.objects.get(action="user.updated", target_id=str(target.pk))
    assert entry.via == "admin" and entry.actor == world.users["super"]
    assert entry.data["changed"]["mobile"]["to"] == "******3210"
    assert "9876543210" not in str(entry.data)


def test_django_admin_role_assignment_changes_are_audited(world):
    target = world.users["pm_kaza"]
    assignment = RoleAssignment.objects.get(user=target)
    browser = staff(world.users["super"])
    url = f"/django-admin/accounts/roleassignment/{assignment.pk}/change/"
    form = {
        "user": str(target.pk), "group": str(assignment.group_id),
        "area_level": assignment.area_level, "area_id": str(assignment.area_id), "is_active": "",
    }
    assert browser.post(url, form).status_code == 302
    entry = AuditLog.objects.get(action="user.assignment_changed", target_id=str(target.pk))
    assert entry.via == "admin" and entry.data["changed"] == {"is_active": {"from": True, "to": False}}
    assert entry.data["role"] == "PANCHAYAT_MEMBER"

    delete = browser.post(f"/django-admin/accounts/roleassignment/{assignment.pk}/delete/", {"post": "yes"})
    assert delete.status_code == 302
    assert AuditLog.objects.filter(action="user.assignment_removed", via="admin", target_id=str(target.pk)).exists()


def test_service_requests_are_read_only_in_django_admin(world, request_from):  # noqa: F811
    _citizen, request_id = request_from("kolakaluru")
    browser = staff(world.users["super"], superuser=True)
    url = f"/django-admin/service_requests/servicerequest/{request_id}/change/"
    assert browser.get(url).status_code == 200  # can look
    browser.post(url, {"name": "Changed", "phone": "9999999999"})
    assert ServiceRequest.objects.get(pk=request_id).name != "Changed"
    assert browser.get("/django-admin/service_requests/servicerequest/add/").status_code == 403


def test_contact_never_shows_its_full_number():
    assert str(Contact(name="VRO", phone="9876543210")) == "VRO ******3210"
