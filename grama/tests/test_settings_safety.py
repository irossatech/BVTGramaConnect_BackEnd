"""The settings fail safe (audit BE_01: BE-SEC-009, BE-SEC-010, BE-TEST-001).

Each case imports grama/settings.py in a fresh Python, as a server process would.
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def settings_in_subprocess(code: str, env: dict) -> subprocess.CompletedProcess:
    clean = {name: value for name, value in os.environ.items()
             if name not in {"DEBUG", "ALLOWED_HOSTS", "DATABASE_URL", "MEDIA_ROOT", "STATIC_ROOT", "PYTEST_VERSION",
                             "DEBUG_ON_PUBLIC_HOST", "SECRET_KEY"}}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env={**clean, **env},
        capture_output=True, text=True, timeout=60,
    )


# --- BE-SEC-009: a server whose .env lost DEBUG=False does not start in development mode ------------


def test_development_mode_refuses_a_public_host_name():
    result = settings_in_subprocess("import grama.settings", {"ALLOWED_HOSTS": "www.gramaconnect.com"})
    assert result.returncode != 0
    assert "DEBUG is on" in result.stderr and "www.gramaconnect.com" in result.stderr


@pytest.mark.parametrize("hosts", ["localhost,127.0.0.1,[::1],10.0.2.2,192.168.1.20", "my-pc.local", ""])
def test_development_mode_is_fine_on_a_laptop(hosts):
    env = {"ALLOWED_HOSTS": hosts} if hosts else {}
    result = settings_in_subprocess("import grama.settings as s; print(s.DEBUG)", env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_development_on_a_public_name_only_when_asked():
    result = settings_in_subprocess(
        "import grama.settings as s; print(s.DEBUG)",
        {"ALLOWED_HOSTS": "dev.example.in", "DEBUG_ON_PUBLIC_HOST": "True"},
    )
    assert result.returncode == 0, result.stderr


# --- BE-SEC-010: importing pytest alone does not switch on the test settings -----------------------


def test_a_server_process_that_imports_pytest_keeps_real_hashing():
    result = settings_in_subprocess(
        "import pytest, grama.settings as s; print(s.PASSWORD_HASHERS[0].rsplit('.', 1)[-1], s.PUSH_GATEWAY)",
        {},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["Argon2PasswordHasher", "apps.notifications.push.FcmGateway"]


# --- BE-TEST-001: tests never use a server's database or media, even with DEBUG=False ----------------


def test_tests_use_sqlite_and_test_media_even_in_production_mode():
    result = settings_in_subprocess(
        "import pytest, grama.settings as s; "
        "print(s.DATABASES['default']['ENGINE'], s.MEDIA_ROOT.name, s.PASSWORD_HASHERS[0].rsplit('.', 1)[-1])",
        {
            "PYTEST_VERSION": "9", "DEBUG": "False", "SECRET_KEY": secrets.token_urlsafe(50),
            "ALLOWED_HOSTS": "www.gramaconnect.com", "DATABASE_URL": "mysql://grama:x@db.example.in:3306/grama",
            "MEDIA_ROOT": "/var/media", "STATIC_ROOT": "/var/static",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["django.db.backends.sqlite3", ".test-media", "MD5PasswordHasher"]
