"""import_geography: validation, idempotency, missing-coordinates report."""

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.geography.importer import GeographyImporter
from apps.geography.models import District, Mandal, State, Village

pytestmark = pytest.mark.django_db

HEADER = "state,state_te,district,district_te,mandal,mandal_te,village,village_te,latitude,longitude,pin_code\n"
GOOD = HEADER + (
    "Andhra Pradesh,ఆంధ్రప్రదేశ్,Guntur,గుంటూరు,Tenali,తెనాలి,Kolakaluru,కొలకలూరు,16.2381,80.5924,522306\n"
    "Andhra Pradesh,ఆంధ్రప్రదేశ్,Guntur,గుంటూరు,Tenali,తెనాలి,Nandivelugu,నందివెలుగు,16.2176,80.6232,522306\n"
    "Andhra Pradesh,ఆంధ్రప్రదేశ్,Guntur,గుంటూరు,Mangalagiri,మంగళగిరి,Kaza,కాజా,,,522503\n"
    "Andhra Pradesh,ఆంధ్రప్రదేశ్,Krishna,కృష్ణా,Gannavaram,గన్నవరం,Nidamanuru,నిడమానూరు,16.5312,80.7124,521104\n"
)


def run(text: str, **kwargs):
    return GeographyImporter(**kwargs).run_text(text)


def totals():
    return (State.objects.count(), District.objects.count(), Mandal.objects.count(), Village.objects.count())


def test_import_creates_the_hierarchy_with_telugu_names():
    report = run(GOOD)
    assert report.ok, report.errors
    assert totals() == (1, 2, 3, 4)
    assert report.counts["village"].created == 4
    village = Village.objects.select_related("mandal__district__state").get(name_en="Kolakaluru")
    assert village.name_te == "కొలకలూరు"
    assert village.latitude == Decimal("16.238100")
    assert village.mandal.name_te == "తెనాలి"
    assert village.mandal.district.state.name_te == "ఆంధ్రప్రదేశ్"


def test_second_run_changes_nothing():
    run(GOOD)
    before = list(Village.objects.order_by("id").values())
    report = run(GOOD)
    assert report.ok
    assert totals() == (1, 2, 3, 4)
    for level in ("state", "district", "mandal", "village"):
        assert report.counts[level].created == 0
        assert report.counts[level].updated == 0
    assert report.counts["village"].unchanged == 4
    assert list(Village.objects.order_by("id").values()) == before


def test_reports_villages_missing_coordinates():
    report = run(GOOD)
    assert report.missing_coordinates == [("Andhra Pradesh", "Guntur", "Mangalagiri", "Kaza", "")]


def test_later_file_fills_in_coordinates_and_blank_cells_erase_nothing():
    run(GOOD)
    update = HEADER + (
        "Andhra Pradesh,,Guntur,,Mangalagiri,,Kaza,,16.4106,80.5490,\n"
        "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,,,\n"
    )
    report = run(update)
    assert report.ok, report.errors
    assert report.counts["village"].updated == 1
    assert report.counts["village"].unchanged == 1
    assert report.missing_coordinates == []
    kolakaluru = Village.objects.get(name_en="Kolakaluru")
    assert kolakaluru.latitude == Decimal("16.238100")
    assert kolakaluru.name_te == "కొలకలూరు"
    assert kolakaluru.pin_code == "522306"


def test_names_match_ignoring_case_and_spaces():
    run(GOOD)
    report = run(HEADER + "andhra  pradesh,,GUNTUR,,tenali,,kolakaluru ,,,,\n")
    assert report.ok
    assert totals() == (1, 2, 3, 4)


def test_lgd_code_matches_a_renamed_village():
    lgd = HEADER.strip() + ",village_lgd_code\n"
    run(lgd + "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,16.2381,80.5924,,594122\n")
    report = run(lgd + "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru Agraharam,,,,,594122\n")
    assert report.ok, report.errors
    assert Village.objects.count() == 1
    assert Village.objects.get().name_en == "Kolakaluru Agraharam"


def test_header_aliases_and_byte_order_mark(tmp_path):
    path = tmp_path / "villages.csv"
    path.write_text(
        "﻿State Name,District Name,Mandal Name,Village Name,Lat,Lng,Pincode\n"
        "Andhra Pradesh,Guntur,Tenali,Kolakaluru,16.2381,80.5924,522306\n",
        encoding="utf-8",
    )
    report = GeographyImporter().run_file(path)
    assert report.ok, report.errors
    assert Village.objects.get().pin_code == "522306"


@pytest.mark.parametrize(
    "rows, message",
    [
        ("Andhra Pradesh,,Guntur,,Tenali,,,,,,\n", "empty village"),
        ("Andhra Pradesh,,Guntur,,Tenali,,Pedaravuru,,16.2,,\n", "both latitude and longitude"),
        ("Andhra Pradesh,,Guntur,,Tenali,,Pedaravuru,,abc,80.5,\n", "not a number"),
        ("Andhra Pradesh,,Guntur,,Tenali,,Pedaravuru,,95,80.5,\n", "out of range"),
        ("Andhra Pradesh,,Guntur,,Tenali,,Pedaravuru,,,,5223\n", "6 digits"),
        (
            "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,,,\nAndhra Pradesh,,Guntur,,Tenali,,kolakaluru,,,,\n",
            "listed again",
        ),
        (
            "Andhra Pradesh,,Guntur,గుంటూరు,Tenali,,A,,,,\nAndhra Pradesh,,Guntur,గుంటూర్,Tenali,,B,,,,\n",
            "Telugu name",
        ),
    ],
)
def test_errors_stop_the_whole_import(rows, message):
    report = run(GOOD + rows)
    assert not report.ok
    assert any(message in error for error in report.errors), report.errors
    assert totals() == (0, 0, 0, 0)


def test_missing_required_column():
    report = run("state,district,village\nAndhra Pradesh,Guntur,Kolakaluru\n")
    assert report.errors and "mandal" in report.errors[0]


def test_one_lgd_code_for_two_villages_is_an_error():
    text = HEADER.strip() + ",village_lgd_code\n" + (
        "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,,,,594122\n"
        "Andhra Pradesh,,Guntur,,Tenali,,Nandivelugu,,,,,594122\n"
    )
    report = run(text)
    assert any("594122" in error for error in report.errors)


def test_lgd_code_under_another_parent_is_refused():
    lgd = HEADER.strip() + ",village_lgd_code\n"
    run(lgd + "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,,,,594122\n")
    report = run(lgd + "Andhra Pradesh,,Guntur,,Mangalagiri,,Kolakaluru,,,,,594122\n")
    assert any("Moving areas is not supported" in error for error in report.errors)
    assert Village.objects.get().mandal.name_en == "Tenali"


def test_swapped_coordinates_are_flagged():
    report = run(HEADER + "Andhra Pradesh,,Guntur,,Tenali,,Kolakaluru,,80.5924,16.2381,\n")
    assert report.ok
    assert any("look swapped" in warning for warning in report.warnings)


def test_dry_run_reports_but_saves_nothing():
    report = run(GOOD, dry_run=True)
    assert report.ok
    assert report.counts["village"].created == 4
    assert totals() == (0, 0, 0, 0)


def test_missing_telugu_names_are_counted():
    report = run("state,district,mandal,village\nAndhra Pradesh,Guntur,Tenali,Kolakaluru\n")
    assert report.missing_telugu == 4
    assert Village.objects.get().name_te == "Kolakaluru"


# --- The management command ------------------------------------------------------------


def test_command_imports_and_writes_the_missing_coordinates_report(tmp_path):
    source = tmp_path / "villages.csv"
    source.write_text(GOOD, encoding="utf-8")
    missing = tmp_path / "missing.csv"
    out = StringIO()
    call_command("import_geography", str(source), "--missing-report", str(missing), stdout=out)
    output = out.getvalue()
    assert "villages   created      4" in output
    assert "1 village(s) have no coordinates" in output
    assert "Guntur > Mangalagiri > Kaza" in output
    assert missing.read_text(encoding="utf-8-sig").splitlines()[1] == "Andhra Pradesh,Guntur,Mangalagiri,Kaza,"


def test_command_fails_without_writing_on_errors(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text(GOOD + "Andhra Pradesh,,Guntur,,Tenali,,,,,,\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Nothing was imported"):
        call_command("import_geography", str(source), stdout=StringIO(), stderr=StringIO())
    assert totals() == (0, 0, 0, 0)


def test_command_rejects_a_missing_file():
    with pytest.raises(CommandError, match="File not found"):
        call_command("import_geography", "no-such-file.csv")
