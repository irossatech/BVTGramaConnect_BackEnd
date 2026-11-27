"""The caller's IP address, for the login lockout, the login throttle and the per-IP limits.

Security finding BE-SEC-101: X-Real-IP is a header anyone can send. It is believed only when it came
from our own reverse proxy, never from a caller on the internet.

Which connections count as our proxy is TRUSTED_PROXIES (audit BE-SEC-212): by default only this
machine (127.0.0.1 and ::1), plus a unix socket, which has no address. Before, any private network
address was believed too, so a neighbour on a shared network could fake its address. A proxy on
another machine is named in .env (grama/settings.py).
"""

import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _parse_ip(value: str):
    """The address in `value`, or None when it is not a single valid IPv4 or IPv6 address."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    # "::ffff:10.0.0.5" is the IPv4 address 10.0.0.5 written as IPv6.
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


_cache: tuple[tuple, list] = ((), [])


def _trusted_networks() -> list:
    """TRUSTED_PROXIES as networks; an entry that is not an address or a network is left out and logged."""
    global _cache
    configured = tuple(settings.TRUSTED_PROXIES)
    if _cache[0] != configured:
        networks = []
        for entry in configured:
            try:
                networks.append(ipaddress.ip_network(entry.strip(), strict=False))
            except ValueError:
                logger.warning("TRUSTED_PROXIES: %r is not an IP address or network; it is ignored.", entry)
        _cache = (configured, networks)
    return _cache[1]


def _from_trusted_proxy(remote: str) -> bool:
    """True when the connection itself came from one of our proxies, never from the internet.

    No address (a unix socket) counts as the server's own. An unreadable address does not."""
    if not remote:
        return True
    address = _parse_ip(remote)
    return address is not None and any(address in network for network in _trusted_networks()
                                       if network.version == address.version)


def client_ip(request) -> str | None:
    """The caller's IP.

    X-Real-IP is used only when TRUST_X_REAL_IP is on, the connection came from a trusted proxy (above),
    and the header holds one valid IP address. Otherwise the connection's own address (REMOTE_ADDR) is
    used, exactly as before."""
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    if settings.TRUST_X_REAL_IP and _from_trusted_proxy(remote):
        forwarded = _parse_ip(request.META.get("HTTP_X_REAL_IP") or "")
        if forwarded is not None:
            return str(forwarded)
    return remote or None
