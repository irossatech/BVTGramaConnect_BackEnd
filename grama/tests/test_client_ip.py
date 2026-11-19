"""Security finding BE-SEC-101: X-Real-IP is believed only from the server's own network.

grama.http.client_ip. The expected answers come from the rule, worked out here: with TRUST_X_REAL_IP on,
the header counts only when the connection came from loopback, a unix socket (no address) or a private
network, and the header holds one valid IP. Anything else: the connection's own address."""

import pytest
from django.test import RequestFactory

from grama.http import client_ip

# Addresses used below. None of them is a real server's address.
PUBLIC_CALLER = "49.37.10.20"   # someone on the internet
FAKE = "8.8.4.4"                # what an attacker puts in X-Real-IP
USER = "106.208.1.2"            # the real user, as our nginx reports it


def ip(remote, real_ip=None):
    meta = {"REMOTE_ADDR": remote}
    if real_ip is not None:
        meta["HTTP_X_REAL_IP"] = real_ip
    return client_ip(RequestFactory().get("/", **meta))


@pytest.fixture
def trusting(settings):
    settings.TRUST_X_REAL_IP = True


# --- The attack in BE-SEC-101 ------------------------------------------------------------


def test_a_fake_header_from_the_internet_is_ignored(trusting):
    assert ip(PUBLIC_CALLER, FAKE) == PUBLIC_CALLER


def test_a_fake_header_from_a_public_ipv6_address_is_ignored(trusting):
    assert ip("2401:4900:1c2a::1", FAKE) == "2401:4900:1c2a::1"


def test_changing_the_fake_header_does_not_change_who_is_counted(trusting):
    seen = {ip(PUBLIC_CALLER, f"1.2.3.{n}") for n in range(1, 6)}
    assert seen == {PUBLIC_CALLER}


# --- Our own nginx, whatever the server's layout ----------------------------------------


@pytest.mark.parametrize("proxy", [
    "127.0.0.1",        # nginx and the app on one machine
    "::1",              # the same, over IPv6
    "",                 # a unix socket: Gunicorn reports no address
    "10.0.0.5",         # nginx on another machine in a private network
    "172.17.0.1",       # Docker
    "192.168.1.10",     # a private LAN
    "::ffff:10.0.0.5",  # a private IPv4 address written as IPv6
    "fd00::5",          # a private IPv6 network
])
def test_the_header_from_the_servers_own_network_is_used(trusting, proxy):
    assert ip(proxy, USER) == USER


def test_an_ipv6_user_is_reported_in_its_short_form(trusting):
    assert ip("127.0.0.1", "2401:4900:0000:0000:0000:0000:0000:0001") == "2401:4900::1"


# --- What stays exactly as before -------------------------------------------------------


def test_without_the_header_the_connection_address_is_used(trusting):
    assert ip("127.0.0.1") == "127.0.0.1"
    assert ip(PUBLIC_CALLER) == PUBLIC_CALLER


def test_a_unix_socket_without_the_header_has_no_address(trusting):
    assert ip("") is None


def test_with_trust_off_the_header_is_never_used(settings):
    settings.TRUST_X_REAL_IP = False
    assert ip("127.0.0.1", USER) == "127.0.0.1"
    assert ip(PUBLIC_CALLER, FAKE) == PUBLIC_CALLER


# --- Junk in the header or the connection -----------------------------------------------


@pytest.mark.parametrize("junk", ["", "   ", "unknown", "1.2.3", "1.2.3.4, 5.6.7.8", "1.2.3.4:80", "<script>"])
def test_junk_in_the_header_falls_back_to_the_connection_address(trusting, junk):
    assert ip("127.0.0.1", junk) == "127.0.0.1"


def test_spaces_around_a_valid_header_are_fine(trusting):
    assert ip("127.0.0.1", f"  {USER} ") == USER


def test_an_unreadable_connection_address_is_not_trusted(trusting):
    assert ip("not-an-ip", FAKE) == "not-an-ip"
