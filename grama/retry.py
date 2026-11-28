"""Bounded retries with exponential backoff, for email and push delivery.

    with_retries(lambda: gateway.send(message))

Tries DELIVERY_RETRY_ATTEMPTS times (default 3). Waits base, base*4, base*16 …
seconds between tries (1 s, 4 s with the defaults). Errors marked permanent
(PermanentDeliveryError) are raised at once; there is no point retrying them.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from django.conf import settings

T = TypeVar("T")


class PermanentDeliveryError(Exception):
    """A failure that retrying cannot fix, such as an unregistered device token."""


def backoff_delays(attempts: int, base: float) -> list[float]:
    """Waits between tries: [base, base*4, …], one fewer than attempts."""
    return [base * (4**index) for index in range(max(attempts - 1, 0))]


def with_retries(
    func: Callable[[], T],
    *,
    attempts: int | None = None,
    base_delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempts = attempts or settings.DELIVERY_RETRY_ATTEMPTS
    base = settings.DELIVERY_RETRY_BASE_SECONDS if base_delay is None else base_delay
    delays = backoff_delays(attempts, base)
    for attempt in range(attempts):
        try:
            return func()
        except PermanentDeliveryError:
            raise
        except Exception:
            if attempt == attempts - 1:
                raise
            if delays[attempt]:
                sleep(delays[attempt])
    raise AssertionError("unreachable")
