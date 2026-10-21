"""Public website API: site config and contact messages."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.web_public.models import ContactMessage

pytestmark = pytest.mark.django_db

CONTACT = "/api/v1/public/contact-messages/"


def send(api, **overrides):
    body = {"name": "రవి", "mobile": "9876543210", "village_text": "కొలకలూరు, తెనాలి", "message": "మా గ్రామాన్ని చేర్చండి", "language": "te"}
    body.update(overrides)
    return api().post(CONTACT, body, format="json", REMOTE_ADDR=overrides.pop("ip", "10.9.9.9"))


def test_site_config_has_every_field_and_the_prototype_defaults(api):
    response = api().get("/api/v1/public/site-config/")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "office_address_te", "office_address_en", "phone", "email",
        "hours_te", "hours_en", "play_store_url", "app_store_url", "website_url", "helplines",
    }
    assert data["phone"] == "+91 863 223 4567"
    assert data["email"] == "help@bvtgramaconnect.in"
    assert data["hours_te"] == "సోమ–శని 10:00–18:00"
    assert data["office_address_en"].split("\n") == [
        "BVT Grama Connect, 2nd Floor, Collectorate Road,",
        "Guntur – 522004, Andhra Pradesh",
    ]
    assert data["play_store_url"] == ""
    assert data["website_url"] == "https://example.in/"
    assert [line["number"] for line in data["helplines"]] == ["112", "108", "1930", "181", "1098", "1912"]
    assert data["helplines"][0] == {"number": "112", "label_te": "అత్యవసరం", "label_en": "Emergency"}
    assert data["helplines"][3]["label_te"] == "మహిళా హెల్ప్‌లైన్"
    assert "public" in response["Cache-Control"]


def test_website_link_and_help_lines_come_from_settings(api, settings):
    settings.SITE_WEBSITE_URL = "https://gramaconnect.example.in/"
    settings.SITE_HELPLINES = [{"number": "100", "label_te": "పోలీస్", "label_en": "Police"}]
    data = api().get("/api/v1/public/site-config/").json()
    assert data["website_url"] == "https://gramaconnect.example.in/"
    assert data["helplines"] == [{"number": "100", "label_te": "పోలీస్", "label_en": "Police"}]


def test_store_links_come_from_settings(api, settings):
    settings.PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=in.bvt.gramaconnect"
    assert api().get("/api/v1/public/site-config/").json()["play_store_url"].startswith("https://play.google.com/")


def test_contact_message_is_stored_with_telugu_intact(api):
    response = send(api)
    assert response.status_code == 201
    message = ContactMessage.objects.get(pk=response.json()["id"])
    assert message.name == "రవి"
    assert message.village_text == "కొలకలూరు, తెనాలి"
    assert message.message == "మా గ్రామాన్ని చేర్చండి"
    assert message.ip_address == "10.9.9.9"


def test_village_is_optional(api):
    assert send(api, village_text="").status_code == 201


@pytest.mark.parametrize(
    "field, value",
    [("name", "   "), ("message", "  "), ("mobile", "12345"), ("mobile", ""), ("language", "hi")],
)
def test_same_validation_as_the_page(api, field, value):
    response = send(api, **{field: value})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert field in response.json()["error"]["fields"]
    assert ContactMessage.objects.count() == 0


def test_mobile_with_country_code_is_accepted(api):
    assert send(api, mobile="+91 98765 43210").status_code == 201
    assert ContactMessage.objects.get().mobile == "9876543210"


def test_honeypot_pretends_success_and_stores_nothing(api):
    response = send(api, website="http://spam.example")
    assert response.status_code == 201
    assert ContactMessage.objects.count() == 0


def test_five_messages_per_ip_per_hour(api):
    for _ in range(5):
        assert send(api).status_code == 201
    blocked = send(api)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "throttled"
    assert int(blocked["Retry-After"]) > 0
    assert send(api, ip="10.9.9.10").status_code == 201
    ContactMessage.objects.update(created_at=timezone.now() - timedelta(hours=2))
    assert send(api).status_code == 201
