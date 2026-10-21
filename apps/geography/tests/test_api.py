"""Public geography endpoints: cascading lists, village detail, caching."""

import pytest

from apps.geography.models import Mandal, Village

pytestmark = pytest.mark.django_db


def names(response):
    return [row["name"] for row in response.json()]


def test_states_are_public_and_telugu_by_default(api, geo):
    response = api().get("/api/v1/geo/states/")
    assert response.status_code == 200
    assert names(response) == ["ఆంధ్రప్రదేశ్"]


def test_english_names_with_accept_language(api, geo):
    response = api().get(f"/api/v1/geo/districts/?state={geo.state.id}", HTTP_ACCEPT_LANGUAGE="en")
    assert names(response) == ["Guntur", "Krishna"]
    assert response["Content-Language"] == "en"


def test_cascade_district_to_mandal_to_village(api, geo):
    client = api()
    guntur = geo.areas["guntur"]
    mandals = client.get(f"/api/v1/geo/mandals/?district={guntur.id}", HTTP_ACCEPT_LANGUAGE="en")
    assert names(mandals) == ["Mangalagiri", "Tenali"]
    assert all(row["district_id"] == guntur.id for row in mandals.json())

    tenali = geo.areas["tenali"]
    villages = client.get(f"/api/v1/geo/villages/?mandal={tenali.id}", HTTP_ACCEPT_LANGUAGE="en")
    assert names(villages) == ["Kolakaluru", "Nandivelugu"]
    assert villages.json()[0]["pin_code"] == "522306"


def test_mandals_need_a_district(api, geo):
    response = api().get("/api/v1/geo/mandals/")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_villages_need_a_mandal_or_a_search(api, geo):
    assert api().get("/api/v1/geo/villages/").status_code == 400
    assert api().get("/api/v1/geo/villages/?q=k").status_code == 400


def test_village_search_matches_english_and_telugu(api, geo):
    client = api()
    english = client.get("/api/v1/geo/villages/?q=kola", HTTP_ACCEPT_LANGUAGE="en")
    assert names(english) == ["Kolakaluru"]
    telugu = client.get("/api/v1/geo/villages/?q=కొలక")
    assert names(telugu) == ["కొలకలూరు"]


def test_inactive_areas_and_everything_below_them_are_hidden(api, geo):
    Village.objects.filter(pk=geo.areas["kolakaluru"].pk).update(is_active=False)
    Mandal.objects.filter(pk=geo.areas["mangalagiri"].pk).update(is_active=False)
    client = api()

    tenali = client.get(f"/api/v1/geo/villages/?mandal={geo.areas['tenali'].id}", HTTP_ACCEPT_LANGUAGE="en")
    assert names(tenali) == ["Nandivelugu"]
    hidden_mandal = client.get(f"/api/v1/geo/villages/?mandal={geo.areas['mangalagiri'].id}")
    assert hidden_mandal.json() == []
    mandals = client.get(f"/api/v1/geo/mandals/?district={geo.areas['guntur'].id}", HTTP_ACCEPT_LANGUAGE="en")
    assert names(mandals) == ["Tenali"]
    assert client.get(f"/api/v1/geo/villages/{geo.areas['kaza'].id}/").status_code == 404


def test_village_detail_has_parents_and_path(api, geo):
    village = geo.areas["kolakaluru"]
    data = api().get(f"/api/v1/geo/villages/{village.id}/").json()
    assert data["name"] == "కొలకలూరు"
    assert data["latitude"] == "16.238100"
    assert data["mandal"] == {"id": geo.areas["tenali"].id, "name": "తెనాలి"}
    assert data["district"]["name"] == "గుంటూరు"
    assert data["state"]["name"] == "ఆంధ్రప్రదేశ్"
    assert data["path"] == "కొలకలూరు · తెనాలి · గుంటూరు"


def test_lists_are_cacheable_and_answer_304_when_unchanged(api, geo):
    client = api()
    first = client.get("/api/v1/geo/states/")
    assert "public" in first["Cache-Control"] and "max-age=3600" in first["Cache-Control"]
    assert "Accept-Language" in first["Vary"]
    etag = first["ETag"]
    again = client.get("/api/v1/geo/states/", HTTP_IF_NONE_MATCH=etag)
    assert again.status_code == 304
    assert again.content == b""


def test_a_stale_token_does_not_break_public_lists(api, geo):
    client = api()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert client.get("/api/v1/geo/states/").status_code == 200


def test_missing_telugu_name_falls_back_to_english(geo):
    village = Village.objects.create(mandal=geo.areas["tenali"], name_en="  New   Village ")
    assert village.name_en == "New Village"
    assert village.name_te == "New Village"
    assert village.name_key == "new village"
