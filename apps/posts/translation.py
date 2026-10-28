"""Machine translation of published posts, Telugu <-> English, through a pluggable provider.

TRANSLATION_PROVIDER picks it:
- "off" (the default): nothing is translated and every post's `translation` is null.
- "google": Google Cloud Translation (Basic, v2). Needs GOOGLE_TRANSLATE_KEY.
- "bhashini": Bhashini, the Government of India's service (ULCA pipeline). Needs
  BHASHINI_USER_ID, BHASHINI_API_KEY and BHASHINI_PIPELINE_ID.
- a dotted path to a Translator class; tests use LocmemTranslator.

Only the title and body of published posts are sent: text the village already
sees, never a name, phone number or device id. Errors never carry the text.
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger("grama.translation")

TELUGU = range(0x0C00, 0x0C80)
#: Lines per provider call; Google takes at most 128 segments a request.
MAX_SEGMENTS = 100


def detect_language(text: str, default: str) -> str:
    """The script most of the letters are in: an English post naming one Telugu village stays "en"."""
    telugu = sum(1 for ch in text if ord(ch) in TELUGU)
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if telugu > latin:
        return "te"
    if latin > telugu:
        return "en"
    return default


class Translator(Protocol):
    name: str

    def translate(self, texts: list[str], src: str, dst: str) -> list[str]:
        """One translation per text, in order, or raise."""


class LocmemTranslator:
    """Tests: marks each text with the target language. Put exceptions in `failures` to fail calls."""

    name = "locmem"
    calls: list[tuple[list[str], str, str]] = []
    failures: list[Exception] = []

    def translate(self, texts, src, dst):
        LocmemTranslator.calls.append((list(texts), src, dst))
        if LocmemTranslator.failures:
            raise LocmemTranslator.failures.pop(0)
        return [f"[{dst}] {text}" for text in texts]


def _check(response, service: str) -> dict:
    # The status only: an error body may echo the post's text.
    if response.status_code != 200:
        raise RuntimeError(f"{service} answered {response.status_code}")
    return response.json()


class GoogleTranslator:
    name = "google"
    URL = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self):
        if not settings.GOOGLE_TRANSLATE_KEY:
            raise RuntimeError("GOOGLE_TRANSLATE_KEY is not set.")

    def translate(self, texts, src, dst):
        response = requests.post(
            self.URL,
            json={"q": texts, "source": src, "target": dst, "format": "text"},
            # In a header, not the URL, so the key never shows in a logged URL.
            headers={"X-goog-api-key": settings.GOOGLE_TRANSLATE_KEY},
            timeout=settings.TRANSLATION_TIMEOUT_S,
        )
        return [row["translatedText"] for row in _check(response, "Google")["data"]["translations"]]


class BhashiniTranslator:
    """Two steps: ask the ULCA pipeline which service and endpoint to use, then translate there."""

    name = "bhashini"
    CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
    #: (src, dst) -> (endpoint, auth header name, auth header value, service id), for this process.
    _endpoints: dict[tuple[str, str], tuple[str, str, str, str]] = {}

    def __init__(self):
        if not (settings.BHASHINI_USER_ID and settings.BHASHINI_API_KEY and settings.BHASHINI_PIPELINE_ID):
            raise RuntimeError("BHASHINI_USER_ID, BHASHINI_API_KEY and BHASHINI_PIPELINE_ID must be set.")

    def _endpoint(self, src, dst):
        if (src, dst) not in self._endpoints:
            response = requests.post(
                self.CONFIG_URL,
                json={
                    "pipelineTasks": [
                        {"taskType": "translation", "config": {"language": {"sourceLanguage": src, "targetLanguage": dst}}}
                    ],
                    "pipelineRequestConfig": {"pipelineId": settings.BHASHINI_PIPELINE_ID},
                },
                headers={"userID": settings.BHASHINI_USER_ID, "ulcaApiKey": settings.BHASHINI_API_KEY},
                timeout=settings.TRANSLATION_TIMEOUT_S,
            )
            data = _check(response, "Bhashini (pipeline)")
            inference = data["pipelineInferenceAPIEndPoint"]
            service_id = data["pipelineResponseConfig"][0]["config"][0]["serviceId"]
            key = inference["inferenceApiKey"]
            self._endpoints[(src, dst)] = (inference["callbackUrl"], key["name"], key["value"], service_id)
        return self._endpoints[(src, dst)]

    def translate(self, texts, src, dst):
        url, header, value, service_id = self._endpoint(src, dst)
        response = requests.post(
            url,
            json={
                "pipelineTasks": [
                    {
                        "taskType": "translation",
                        "config": {"language": {"sourceLanguage": src, "targetLanguage": dst}, "serviceId": service_id},
                    }
                ],
                "inputData": {"input": [{"source": text} for text in texts]},
            },
            headers={header: value},
            timeout=settings.TRANSLATION_TIMEOUT_S,
        )
        return [row["target"] for row in _check(response, "Bhashini")["pipelineResponse"][0]["output"]]


PROVIDERS = {"google": GoogleTranslator, "bhashini": BhashiniTranslator}


def enabled() -> bool:
    return settings.TRANSLATION_PROVIDER not in ("", "off")


def get_translator() -> Translator | None:
    """The configured provider, or None while TRANSLATION_PROVIDER is off."""
    if not enabled():
        return None
    name = settings.TRANSLATION_PROVIDER
    return (PROVIDERS.get(name) or import_string(name))()


def translate_post_text(translator: Translator, title: str, body: str, src: str, dst: str) -> tuple[str, str]:
    """Title and body in `dst`. The body goes line by line, so its paragraphs and empty lines survive."""
    lines = body.split("\n")
    segments = [title] + [line for line in lines if line.strip()]
    out: list[str] = []
    for start in range(0, len(segments), MAX_SEGMENTS):
        chunk = segments[start:start + MAX_SEGMENTS]
        translated = translator.translate(chunk, src, dst)
        if len(translated) != len(chunk):
            raise RuntimeError(f"{translator.name} returned {len(translated)} texts for {len(chunk)}")
        out.extend(translated)
    title_out, rest = out[0], iter(out[1:])
    body_out = "\n".join(next(rest) if line.strip() else line for line in lines)
    return title_out.strip()[:200], body_out
