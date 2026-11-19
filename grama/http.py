"""The caller's IP address, for the login lockout, the login throttle and the per-IP limits.

Security finding BE-SEC-101: X-Real-IP is a header anyone can send. It is believed only when it came
from our own reverse proxy, never from a caller on the internet.

No proxy address is written here or in the settings, because each server is set up differently.
Instead the rule is: a connection from the same machine (loopback), a unix socket (no address) or a
private network can only come from the server's own nginx or its neighbours; a connection from a
public internet address cannot be the proxy, so its X-Real-IP is ignored and its real address counts.
"""

import ipaddress

from django.conf import settings


def _parse_ip(value: str):
    """The address in `value`, or None when it is not a single valid IPv4 or IPv6 address."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    # "::ffff:10.0.0.5" is the IPv4 address 10.0.0.5 written as IPv6.
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _from_own_network(remote: str) -> bool:
    """True when the connection itself came from the server or its private network, not the internet.

    No address (a unix socket) counts as the server's own. An unreadable address does not."""
    if not remote:
        return True
    address = _parse_ip(remote)
    return address is not None and not address.is_global


def client_ip(request) -> str | None:
    """The caller's IP.

    X-Real-IP is used only when TRUST_X_REAL_IP is on, the connection came from the server's own
    network (above), and the header holds one valid IP address. Otherwise the connection's own
    address (REMOTE_ADDR) is used, exactly as before."""
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    if settings.TRUST_X_REAL_IP and _from_own_network(remote):
        forwarded = _parse_ip(request.META.get("HTTP_X_REAL_IP") or "")
        if forwarded is not None:
            return str(forwarded)
    return remote or None
