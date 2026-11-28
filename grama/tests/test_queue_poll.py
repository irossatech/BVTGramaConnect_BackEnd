"""The background-job worker checks its database queue once a second while idle (audit finding BE-DB-017).

django-q2's own default, 0.2 s, would query the database five times a second all day.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def test_the_worker_polls_once_a_second_by_default():
    assert settings.Q_CLUSTER["poll"] == 1.0


def poll_with(env_value):
    """Q_CLUSTER["poll"] as a fresh Django process reads it with Q_POLL set (settings load once per process)."""
    env = {**os.environ, "Q_POLL": env_value, "DJANGO_SETTINGS_MODULE": "grama.settings"}
    code = "import django; django.setup(); from django.conf import settings; print(settings.Q_CLUSTER['poll'])"
    result = subprocess.run([sys.executable, "-c", code], cwd=BASE_DIR, env=env, capture_output=True, text=True, check=True)
    return float(result.stdout.strip().splitlines()[-1])


def test_q_poll_changes_it():
    assert poll_with("2.5") == 2.5
