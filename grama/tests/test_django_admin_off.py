"""Django admin is off on every server (lead's decision of 2026-11-11; audit BE_02, BE-SEC-101/003).

The address exists only where DJANGO_ADMIN_ENABLED is on: laptops by default. On a server
(DEBUG off) it answers 404, so its login cannot be reached at all.
"""

import importlib

import pytest
from django.test import Client
from django.urls import clear_url_caches

import grama.urls

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_switch(settings):
    """admin_switch(False) rebuilds the URLs with Django admin off; put back afterwards."""

    def switch(enabled: bool):
        settings.DJANGO_ADMIN_ENABLED = enabled
        importlib.reload(grama.urls)
        clear_url_caches()

    yield switch
    settings.DJANGO_ADMIN_ENABLED = True
    importlib.reload(grama.urls)
    clear_url_caches()


@pytest.mark.parametrize("path", ["/django-admin/", "/django-admin/login/", "/django-admin/accounts/user/"])
def test_off_the_address_does_not_exist(admin_switch, settings, path):
    settings.DEBUG = False  # a server
    admin_switch(False)
    assert Client().get(path).status_code == 404


def test_off_nobody_can_even_try_a_password(admin_switch, settings, world):
    settings.DEBUG = False
    admin_switch(False)
    response = Client().post("/django-admin/login/", {"username": "super", "password": "anything"})
    assert response.status_code == 404


def test_off_the_rest_of_the_site_still_works(admin_switch, settings):
    settings.DEBUG = False
    admin_switch(False)
    client = Client()
    assert client.get("/api/v1/health/").status_code == 200
    assert client.get("/api/v1/categories/").status_code == 200


def test_on_for_laptops(admin_switch):
    admin_switch(True)
    response = Client().get("/django-admin/login/")
    assert response.status_code == 200


def test_servers_default_to_off():
    """DEBUG=False (qa and production) leaves it off unless the .env says DJANGO_ADMIN_ENABLED=True.

    Production settings need their env to import, so read the file (as test_prod_settings_turn_integrity_on does).
    """
    from pathlib import Path

    source = (Path(grama.urls.__file__).parent / "settings.py").read_text(encoding="utf-8")
    assert 'DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=DEBUG)' in source
