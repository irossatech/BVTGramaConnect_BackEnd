"""CSV import (endpoint and commands), seed_helplines, and site-config help lines (contacts plan, section 11)."""

from io import StringIO

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command

from apps.audit.models import AuditLog
from apps.contacts.models import Contact
from apps.geography.models import Village

pytestmark = pytest.mark.django_db
IMPORT_URL = "/api/v1/admin/contacts/import/"
HEADER = "type,custom_type,name,name_te,phone,email,district,mandal,village,sort_order,consent\n"
GOOD = HEADER + (
    "vro,,K. Srinivasa Rao,కె. శ్రీనివాసరావు,9000010001,,Guntur,Tenali,Kolakaluru,,yes\n"
    "revenue,,\"Tahsildar Office, Tenali\",,08644222333,mro@example.in,Guntur,Tenali,,10,\n"
    "other,Anganwadi,Anganwadi Kolakaluru,,08644222334,,Guntur,Tenali,Kolakaluru,,\n"
    "health,,Ambulance,అంబులెన్స్,108,,,,,,\n"
)


def upload(client, text, dry_run=False, encoding="utf-8"):
    url = IMPORT_URL + ("?dry_run=1" if dry_run else "")
    return client.post(url, {"file": SimpleUploadedFile("n.csv", text.encode(encoding), "text/csv")}, format="multipart")


@pytest.fixture
def admin(world, api):
    return api(world.users["super"])


def test_dry_run_reports_and_writes_nothing(world, admin):
    response = upload(admin, GOOD, dry_run=True)
    assert response.status_code == 200, response.json()
    assert response.json() == {"dry_run": True, "created": 4, "updated": 0, "skipped": 0, "errors": []}
    assert Contact.objects.count() == 0


def test_real_run_creates_with_the_api_rules_and_is_audited_with_counts_only(world, admin):
    assert upload(admin, GOOD).json()["created"] == 4
    vro = Contact.objects.get(phone="9000010001")
    assert (vro.area, vro.name_te, vro.consent_recorded, vro.verified_at is not None) == (
        world.ref("kolakaluru"), "కె. శ్రీనివాసరావు", True, True,
    )
    assert Contact.objects.get(phone="108").area == world.ref("ap")
    assert Contact.objects.get(phone="08644222334").custom_type == "Anganwadi"
    entry = AuditLog.objects.get(action="contacts.imported")
    assert entry.data == {"created": 4, "updated": 0, "skipped": 0}


def test_one_bad_row_rolls_back_the_whole_file(world, admin):
    bad = GOOD + "vro,,No consent,,9000010002,,Guntur,Tenali,Kolakaluru,,\n"
    response = upload(admin, bad)
    assert response.status_code == 400
    report = response.json()["error"]["report"]
    assert report["errors"] == [{"row": 6, "field": "consent_recorded", "message": report["errors"][0]["message"]}]
    assert Contact.objects.count() == 0


def test_reimport_updates_or_skips_never_duplicates(world, admin):
    upload(admin, GOOD)
    changed = GOOD.replace("Anganwadi Kolakaluru", "Anganwadi Centre Kolakaluru")
    report = upload(admin, changed).json()
    assert (report["created"], report["updated"], report["skipped"]) == (0, 1, 3)
    assert Contact.objects.count() == 4
    assert Contact.objects.filter(name="Anganwadi Centre Kolakaluru").exists()


def test_reimport_with_blank_optional_cells_keeps_the_values(world, admin):
    upload(admin, GOOD)
    tahsildar = Contact.objects.get(phone="08644222333")
    assert (tahsildar.email, tahsildar.sort_order) == ("mro@example.in", 10)
    blank = HEADER + (
        "vro,,K. Srinivasa Rao,,9000010001,,Guntur,Tenali,Kolakaluru,,yes\n"
        "revenue,,\"Tahsildar Office, Tenali\",,08644222333,,Guntur,Tenali,,,\n"
    )
    report = upload(admin, blank).json()
    assert (report["created"], report["updated"], report["skipped"]) == (0, 0, 2)  # nothing to change
    assert Contact.objects.get(phone="9000010001").name_te == "కె. శ్రీనివాసరావు"
    tahsildar.refresh_from_db()
    assert (tahsildar.email, tahsildar.sort_order) == ("mro@example.in", 10)

    # Columns left out of the file entirely are kept too; a filled cell still changes the value.
    renamed = "type,name,phone,mandal,district\nrevenue,Tahsildar Office,08644222333,Tenali,Guntur\n"
    assert upload(admin, renamed).json()["updated"] == 1
    tahsildar.refresh_from_db()
    assert (tahsildar.name, tahsildar.email, tahsildar.sort_order) == ("Tahsildar Office", "mro@example.in", 10)


def test_a_new_number_with_blank_optional_cells_gets_the_defaults(world, admin):
    text = "type,name,name_te,phone,sort_order,village\nrevenue,Office,,08644222390,,Kolakaluru\n"
    assert upload(admin, text).json()["created"] == 1
    contact = Contact.objects.get(phone="08644222390")
    assert (contact.name_te, contact.email, contact.sort_order) == ("", "", 100)


def test_bom_and_lgd_codes(world, admin):
    Village.objects.filter(pk=world.ref("kolakaluru").id).update(lgd_code="590123")
    text = "type,name,phone,village_lgd,consent\nvro,K. Srinivasa Rao,9000010001,590123,yes\n"
    response = upload(admin, "﻿" + text)
    assert response.status_code == 200, response.json()
    assert Contact.objects.get().area == world.ref("kolakaluru")


@pytest.mark.parametrize(
    "text, field",
    [
        ("name,phone\nX,108\n", "header"),
        ("type,name,phone,pizza\nvro,X,08644222333,1\n", "header"),
        ("type,name,phone,village\nofficial,X,08644222333,Atlantis\n", "village"),
        ("type,name,phone\nofficial,X,08644222333\n", "area"),
        ("type,name,phone,district\nhealth,X,108,Guntur\n", "area"),
        ("type,name,phone,village\nofficial,X,12,Kolakaluru\n", "phone"),
        ("type,name,phone,village,sort_order\nofficial,X,08644222333,Kolakaluru,lots\n", "sort_order"),
        ("type,name,phone,village\nofficial,X,08644222333,Kolakaluru\nofficial,Y,0864-4222333,Kolakaluru\n", "phone"),
    ],
)
def test_errors_name_the_row_and_field(world, admin, text, field):
    response = upload(admin, text)
    assert response.status_code == 400
    assert field in {e["field"] for e in response.json()["error"]["report"]["errors"]}
    assert Contact.objects.count() == 0


def test_limits(world, admin, settings):
    settings.CONTACTS_IMPORT_MAX_ROWS = 2
    many = "type,name,phone,village\n" + "".join(f"official,X{i},0864422233{i},Kolakaluru\n" for i in range(3))
    assert upload(admin, many).status_code == 400
    settings.CONTACTS_IMPORT_MAX_BYTES = 10
    assert upload(admin, GOOD).status_code == 413
    settings.CONTACTS_IMPORT_MAX_BYTES = 2 * 1024 * 1024
    assert admin.post(IMPORT_URL, {}, format="multipart").status_code == 400
    assert upload(admin, GOOD, encoding="utf-16").status_code == 400


@pytest.mark.parametrize("actor", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_import_is_for_the_super_admin(world, api, actor):
    response = upload(api(world.users[actor]), GOOD)
    assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


def test_import_contacts_command(world, tmp_path):
    path = tmp_path / "numbers.csv"
    path.write_text(GOOD, encoding="utf-8-sig")
    out = StringIO()
    call_command("import_contacts", str(path), "--dry-run", stdout=out)
    assert "Would create 4" in out.getvalue() and Contact.objects.count() == 0
    call_command("import_contacts", str(path), stdout=StringIO())
    assert Contact.objects.count() == 4
    path.write_text("type,name,phone\nofficial,X,12\n", encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("import_contacts", str(path), stdout=StringIO(), stderr=StringIO())


# --- seed_helplines and site-config ----------------------------------------------------------------


def test_site_config_falls_back_to_settings_then_reads_the_table(world, api):
    before = api().get("/api/v1/public/site-config/").json()["helplines"]
    assert before == django_settings.SITE_HELPLINES
    out = StringIO()
    call_command("seed_helplines", stdout=out)
    assert "6 created" in out.getvalue()
    call_command("seed_helplines", stdout=out)
    assert Contact.objects.filter(area_level="STATE").count() == 6
    after = api().get("/api/v1/public/site-config/").json()["helplines"]
    assert after == django_settings.SITE_HELPLINES  # the same list, now from the table
    Contact.objects.filter(phone="1930").update(name="Cyber crime", name_te="సైబర్ నేరం")
    labels = {h["number"]: (h["label_en"], h["label_te"]) for h in api().get("/api/v1/public/site-config/").json()["helplines"]}
    assert labels["1930"] == ("Cyber crime", "సైబర్ నేరం")


def test_removed_helplines_leave_site_config(world, api):
    call_command("seed_helplines", stdout=StringIO())
    Contact.objects.filter(phone="1912").update(is_active=False)
    numbers = [h["number"] for h in api().get("/api/v1/public/site-config/").json()["helplines"]]
    assert numbers == ["112", "108", "1930", "181", "1098"]
