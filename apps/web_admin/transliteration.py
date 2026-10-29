"""English → Telugu spelling of names, for the Telugu name boxes in the app and console.

TRANSLITERATION_PROVIDER picks it:
- "google_input_tools" (default): Google Input Tools, phonetic ("Ravi Kumar" → "రవి కుమార్"). No key.
- "off": always "" (the admins type Telugu themselves).
- a dotted path to a class with `transliterate(text) -> str`; tests use LocmemTransliterator.

It is the spelling, not a translation: "Water Bill" gives "వాటర్ బిల్", not "నీటి బిల్లు".
The text can be a person's name: it is never logged, errors never carry it, and the cache
key is a hash, so the cache never holds a name in clear.
"""

from __future__ import annotations

import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

logger = logging.getLogger("grama.transliteration")

CACHE_PREFIX = "translit:te:"
MAX_LENGTH = 150  # the longest name field, Contact.name


class GoogleInputTools:
    name = "google_input_tools"
    url = "https://inputtools.google.com/request"

    def transliterate(self, text: str) -> str:
        response = requests.get(
            self.url,
            params={"text": text, "itc": "te-t-i0-und", "num": 1},
            timeout=settings.TRANSLITERATION_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        if data[0] != "SUCCESS":
            raise RuntimeError(f"{self.name} returned {data[0]}")
        return str(data[1][0][1][0]).strip()


class LocmemTransliterator:
    """Tests: returns `result`; put exceptions in `failures` to fail calls."""

    name = "locmem"
    calls: list[str] = []
    failures: list[Exception] = []
    result = "రవి కుమార్"

    def transliterate(self, text):
        LocmemTransliterator.calls.append(text)
        if LocmemTransliterator.failures:
            raise LocmemTransliterator.failures.pop(0)
        return LocmemTransliterator.result


PROVIDERS = {"google_input_tools": GoogleInputTools}


def to_telugu(text: str) -> str:
    """The Telugu spelling of `text`, or "" when off or on any failure. Cached per provider."""
    name = settings.TRANSLITERATION_PROVIDER
    if name in ("", "off"):
        return ""
    # Hashed key: the cache never holds the name in clear. The provider is part of it, so
    # switching providers does not keep serving the old one's answers.
    key = CACHE_PREFIX + hashlib.sha256(f"{name}\n{text.lower()}".encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return hit
    provider = (PROVIDERS.get(name) or import_string(name))()
    try:
        out = provider.transliterate(text)[:MAX_LENGTH]
    except Exception as error:  # noqa: BLE001 (a suggestion: never fail the request)
        logger.warning("transliteration failed: %s %s", provider.name, type(error).__name__)
        return ""  # not cached, so the next try asks again
    cache.set(key, out, settings.TRANSLITERATION_CACHE_S)
    return out
