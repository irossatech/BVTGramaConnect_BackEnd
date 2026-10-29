"""Fixes after the statewide import (geography-data-fixes-plan.md): empty districts, census tags,
Telugu names twice in one mandal, and what still uses an area before it is switched off."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.geography.importer import GeographyImporter
from apps.geography.models import District, Mandal, Village

pytestmark = pytest.mark.django_db


def command(name, *args) -> str:
    out = StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


def village(world, mandal_key, name_en, name_te=""):
    return Village.objects.create(mandal=world.areas[mandal_key], name_en=name_en, name_te=name_te)


# --- 1. Empty districts ------------------------------------------------------------------------------


def test_a_district_without_mandals_is_not_offered(world, api):
    empty = District.objects.create(state=world.state, name_en="Nellore", name_te="నెల్లూరు")
    switched_off = District.objects.create(state=world.state, name_en="Old Krishna")
    Mandal.objects.create(district=switched_off, name_en="Gone", is_active=False)
    listed = {row["id"] for row in APIClient().get(f"/api/v1/geo/districts/?state={world.state.id}").json()}
    assert listed == {world.areas["guntur"].id, world.areas["krishna"].id}
    # The Super Admin still sees it in the console's tree, to switch it off or fill it.
    tree = api(world.users["super"]).get("/api/v1/geo/tree/").json()
    in_tree = {row["id"]: row["mandal_count"] for state in tree for row in state["districts"]}
    assert in_tree[empty.id] == 0


def test_area_usage_before_switching_an_area_off(world):
    empty = District.objects.create(state=world.state, name_en="Nellore")
    assert "Nothing uses it" in command("geo_area_usage", "DISTRICT", str(empty.id))
    output = command("geo_area_usage", "DISTRICT", str(world.areas["guntur"].id))
    assert "Still in use" in output
    lines = {line.rsplit(None, 1)[0].strip(): int(line.rsplit(None, 1)[1]) for line in output.splitlines()[:9]}
    assert lines["mandals below"] == 2 and lines["villages below"] == 4
    assert lines["role assignments"] == 9  # da_guntur, 2 mandal admins, 3 village admins, 3 members
    with pytest.raises(CommandError, match="No DISTRICT"):
        command("geo_area_usage", "DISTRICT", "999999")


# --- 3. Census tags ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, clean",
    [("Anantapur (Ct)", "Anantapur"), ("Gooty (Og)", "Gooty"), ("Chintareddipalem (Part)", "Chintareddipalem"),
     ("Nellore (R)", "Nellore Rural"), ("Nellore (Rural)", "Nellore Rural"), ("Nellore Rural (R)", "Nellore Rural"),
     ("Kothapalli (East)", "Kothapalli East"), ("Kothapalli (west)", "Kothapalli West"), ("Pedda (Near)", "Pedda Near"),
     ("Anantapur (Ct) (Part)", "Anantapur")],
)
def test_the_cleaning_rules(raw, clean):
    from apps.geography.management.commands.geo_clean_names import cleaned

    assert cleaned(raw) == clean


def test_cleaning_lists_first_and_renames_only_with_apply(world):
    town = village(world, "tenali", "Anantapur (Ct)")
    rural = village(world, "tenali", "Nellore (R)", "నెల్లూరు గ్రామీణ")
    part = village(world, "mangalagiri", "Chinta (Part)")
    village(world, "mangalagiri", "Chinta")  # the same name without the tag: a person must decide
    twin_a, twin_b = village(world, "gannavaram", "Ravi (Og)"), village(world, "gannavaram", "Ravi (Ct)")

    listing = command("geo_clean_names")
    assert "Anantapur (Ct) -> Anantapur" in listing and "2 village(s) would be renamed" in listing
    assert "3 village(s) need a person" in listing and f"id {part.id}" in listing
    assert Village.objects.get(pk=town.pk).name_en == "Anantapur (Ct)"  # nothing written yet

    assert "Renamed 2 village(s)" in command("geo_clean_names", "--apply")
    town.refresh_from_db(), rural.refresh_from_db()
    assert (town.name_en, town.census_name, town.name_te) == ("Anantapur", "Anantapur (Ct)", "Anantapur")
    assert (rural.name_en, rural.census_name, rural.name_te) == ("Nellore Rural", "Nellore (R)", "నెల్లూరు గ్రామీణ")
    for left in (part, twin_a, twin_b):
        assert Village.objects.get(pk=left.pk).census_name == ""
    entry = AuditLog.objects.filter(action="area.updated", target_id=str(town.pk)).get()
    assert entry.data["changes"]["name_en"] == ["Anantapur (Ct)", "Anantapur"]
    assert "0 village(s) would be renamed" in command("geo_clean_names")  # safe to run again


def test_a_later_census_file_finds_the_cleaned_village_and_keeps_its_name(world):
    town = village(world, "tenali", "Anantapur (Ct)")
    command("geo_clean_names", "--apply")
    report = GeographyImporter().run_text(
        "state,district,mandal,village,latitude,longitude\n"
        "Andhra Pradesh,Guntur,Tenali,Anantapur (Ct),16.240000,80.600000\n"
    )
    assert report.ok, report.errors
    assert report.counts["village"].created == 0 and report.counts["village"].updated == 1
    town.refresh_from_db()
    assert (town.name_en, str(town.latitude)) == ("Anantapur", "16.240000")


# --- 4. The same Telugu name twice in one mandal -----------------------------------------------------


HEADER = "state,district,mandal,village,village_te\n"


def test_the_import_refuses_one_telugu_name_twice_in_a_mandal(world):
    report = GeographyImporter().run_text(HEADER + "Andhra Pradesh,Guntur,Tenali,Mantripalem,మంత్రిపాలెం\n"
                                                   "Andhra Pradesh,Guntur,Tenali,Matripalem,మంత్రిపాలెం\n")
    assert not report.ok and "Line 3" in report.errors[0] and "Mantripalem" in report.errors[0]
    assert not Village.objects.filter(name_en__in=["Mantripalem", "Matripalem"]).exists()


def test_the_import_refuses_a_telugu_name_the_mandal_has_already(world):
    report = GeographyImporter().run_text(HEADER + "Andhra Pradesh,Guntur,Tenali,Kolakaluru2,కొలకలూరు\n")
    assert not report.ok and "already has" in report.errors[0]
    # Fine in another mandal, and fine when the file gives the other village a new Telugu name.
    assert GeographyImporter().run_text(HEADER + "Andhra Pradesh,Guntur,Mangalagiri,Kolakaluru2,కొలకలూరు\n").ok
    swap = GeographyImporter().run_text(HEADER + "Andhra Pradesh,Guntur,Tenali,Kolakaluru2,కొలకలూరు\n"
                                                 "Andhra Pradesh,Guntur,Tenali,Kolakaluru,కొలకలూరు పాత\n")
    assert swap.ok, swap.errors


def test_the_api_refuses_a_telugu_name_twice_but_lets_an_old_duplicate_be_edited(world, api):
    admin = api(world.users["super"])
    body = {"mandal_id": world.areas["tenali"].id, "name_en": "Matripalem", "name_te": "కొలకలూరు",
            "latitude": "16.200000", "longitude": "80.600000"}
    refused = admin.post("/api/v1/geo/villages/", body, format="json")
    assert refused.status_code == 409 and "name_te" in refused.json()["error"]["fields"]

    old = village(world, "tenali", "Matripalem")
    Village.objects.filter(pk=old.pk).update(name_te="కొలకలూరు")  # as on the QA server today
    assert "Village (Telugu) under mandal" in command("geo_duplicates")
    assert admin.patch(f"/api/v1/geo/villages/{old.pk}/", {"pin_code": "522306"}, format="json").status_code == 200
    fixed = admin.patch(f"/api/v1/geo/villages/{old.pk}/", {"name_te": "మాత్రిపాలెం"}, format="json")
    assert fixed.status_code == 200
    assert "No duplicates." in command("geo_duplicates")
