"""Villages without coordinates: "Use my location" can never find them. geo_coordinates and the import report."""

import csv
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command

from apps.geography.importer import GeographyImporter
from apps.geography.models import Mandal, Village

pytestmark = pytest.mark.django_db


def clear(world, *keys):
    Village.objects.filter(pk__in=[world.areas[key].id for key in keys]).update(latitude=None, longitude=None)


def command(*args) -> str:
    out = StringIO()
    call_command("geo_coordinates", *args, stdout=out)
    return out.getvalue()


def test_counts_active_villages_without_coordinates_per_district(world):
    clear(world, "kolakaluru", "nandivelugu", "kaza", "nidamanuru")
    Village.objects.filter(pk=world.areas["nandivelugu"].id).update(is_active=False)  # switched off: not counted
    output = command()
    total = Village.objects.count() - 1
    assert f"3 of {total} active village(s) have no coordinates." in output
    guntur, krishna = world.areas["guntur"].name_en, world.areas["krishna"].name_en
    lines = [line.split() for line in output.splitlines()[1:3]]
    assert lines == [[guntur, "2"], [krishna, "1"]]  # most first


def test_a_switched_off_mandal_hides_its_villages_from_the_count(world):
    clear(world, "kaza")
    Mandal.objects.filter(pk=world.areas["mangalagiri"].id).update(is_active=False)
    assert "0 of" in command()


def test_the_csv_lists_them_ready_to_fill_in_and_import(world, tmp_path):
    clear(world, "kolakaluru", "kaza")
    Village.objects.filter(pk=world.areas["kaza"].id).update(lgd_code="594122", name_te="కాజా")
    path = tmp_path / "missing.csv"
    assert "Wrote 2 village(s)" in command("--csv", str(path))
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel shows Telugu
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    assert list(rows[0]) == ["state", "district", "mandal", "village", "village_lgd_code", "pin_code", "latitude", "longitude"]
    kaza = next(row for row in rows if row["village"] == world.areas["kaza"].name_en)
    assert kaza["village_lgd_code"] == "594122" and kaza["pin_code"] == "522306"
    assert kaza["latitude"] == kaza["longitude"] == ""

    # Filled in and imported as it is: nothing else changes, and none are left.
    for row in rows:
        row["latitude"], row["longitude"] = "16.410600", "80.549000"
    filled = StringIO()
    writer = csv.DictWriter(filled, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    report = GeographyImporter().run_text(filled.getvalue())
    assert report.ok, report.errors
    assert report.counts["village"].updated == 2 and report.counts["village"].created == 0
    assert report.missing_coordinates_total == 0
    assert "Every active village has coordinates." in command("--fail-above", "0")


def test_the_release_check_fails_above_the_agreed_number(world):
    clear(world, "kolakaluru", "kaza")
    assert "2 of" in command("--fail-above", "2")
    with pytest.raises(CommandError, match="2 active village"):
        command("--fail-above", "1")


def test_the_import_reports_how_many_are_left_in_the_whole_database(world, api):
    clear(world, "kolakaluru", "kaza")
    kaza, mandal = world.areas["kaza"], world.areas["mangalagiri"]
    text = ("state,district,mandal,village,latitude,longitude\n"
            f"{world.state.name_en},{mandal.district.name_en},{mandal.name_en},{kaza.name_en},16.410600,80.549000\n")
    upload = SimpleUploadedFile("v.csv", text.encode("utf-8"), "text/csv")
    response = api(world.users["super"]).post("/api/v1/geo/villages/import/?dry_run=1", {"file": upload},
                                              format="multipart")
    data = response.json()
    assert response.status_code == 200, data
    # What the import would leave: Kolakaluru, which is not in the file. Only the file's are listed.
    assert (data["missing_coordinates_count"], data["missing_coordinates_total"]) == (0, 1)
    assert Village.objects.without_coordinates().count() == 2  # a dry run saves nothing


def test_the_import_command_points_to_the_list(world, tmp_path):
    clear(world, "kolakaluru")
    kaza, mandal = world.areas["kaza"], world.areas["mangalagiri"]
    source = tmp_path / "v.csv"
    source.write_text("state,district,mandal,village\n"
                      f"{world.state.name_en},{mandal.district.name_en},{mandal.name_en},{kaza.name_en}\n",
                      encoding="utf-8")
    out = StringIO()
    call_command("import_geography", str(source), stdout=out)
    assert "1 active village(s) in the database have no coordinates" in out.getvalue()
