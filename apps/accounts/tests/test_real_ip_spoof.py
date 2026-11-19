"""Security finding BE-SEC-101, end to end: changing X-Real-IP on every try no longer gets round the limits.

The attacker calls the app server directly from the internet (on qa, the open port) and sends a different
fake X-Real-IP each time. Before the fix each try looked like a new person, so the lockout never came.
Our own nginx, on the same machine, still reports each real user separately.

grama/http.py has the rule; grama/tests/test_client_ip.py tests it case by case."""

import pytest
from rest_framework.test import APIClient

from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CONTACT = "/api/v1/public/contact-messages/"
ATTACKER = "49.37.10.20"  # a public internet address
NGINX = "127.0.0.1"       # nginx on the same machine as the app


@pytest.fixture(autouse=True)
def trusting(settings):
    settings.TRUST_X_REAL_IP = True  # as on every server


def login(remote, real_ip, username="va_kolakaluru", password="wrong"):
    client = APIClient(REMOTE_ADDR=remote, HTTP_X_REAL_IP=real_ip)
    return client.post(LOGIN, {"username": username, "password": password}, format="json")


def code(response):
    return response.json()["error"]["code"]


def test_a_new_fake_ip_on_every_try_still_locks_the_username(world):
    answers = [code(login(ATTACKER, f"203.0.113.{n}")) for n in range(1, 6)]
    assert answers == ["invalid_credentials"] * 4 + ["locked_out"]  # the 5th wrong try locks
    # Even the right password now waits: the five tries were counted against the attacker's real address.
    response = login(ATTACKER, "203.0.113.6", password=TEST_PASSWORD)
    assert response.status_code == 429 and code(response) == "locked_out"


def test_through_our_nginx_each_real_user_is_counted_on_their_own(world):
    # One person mistypes five times and is locked out; another person, through the same nginx, is not.
    for _ in range(5):
        login(NGINX, "106.208.1.2")
    assert code(login(NGINX, "106.208.1.2", password=TEST_PASSWORD)) == "locked_out"
    assert login(NGINX, "117.200.3.4", password=TEST_PASSWORD).status_code == 200


def test_the_login_throttle_counts_the_attackers_real_address(world, settings):
    settings.LOGIN_THROTTLE_RATE = "3/hour"
    for n in range(1, 4):
        login(ATTACKER, f"203.0.113.{n}", username=f"nobody{n}")
    assert code(login(ATTACKER, "203.0.113.9", username="nobody9")) == "throttled"


def test_the_contact_form_limit_counts_the_attackers_real_address(settings):
    settings.CONTACT_MESSAGES_PER_IP_PER_HOUR = 2
    body = {"name": "రవి", "mobile": "9876543210", "village_text": "కొలకలూరు", "message": "పరీక్ష", "language": "te"}
    answers = [
        APIClient(REMOTE_ADDR=ATTACKER, HTTP_X_REAL_IP=f"203.0.113.{n}").post(CONTACT, body, format="json")
        for n in range(1, 4)
    ]
    assert [a.status_code for a in answers[:2]] == [201, 201]
    assert answers[2].status_code == 429 and code(answers[2]) == "throttled"
