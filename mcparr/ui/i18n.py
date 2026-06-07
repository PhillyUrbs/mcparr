"""Internationalisation for the config UI.

Scope is UI strings only - tool names and descriptions stay English because they
are LLM-facing. English source strings are the gettext message ids, so the ``en``
locale is the identity translation and needs no catalog. Other locales load a
compiled ``.mo`` if present and fall back to English otherwise, so the app runs
before catalogs are compiled.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from babel.support import NullTranslations, Translations

SUPPORTED_LOCALES = ("en", "es")
DEFAULT_LOCALE = "en"
LOCALE_COOKIE = "mcparr_lang"

# Maps machine status/error codes (raised by services and core logic) to English
# source strings. The UI translates the English string via gettext, so codes stay
# out of templates and English remains the single source language for catalogs.
CODE_MESSAGES: dict[str, str] = {
    "ok": "OK",
    "ok.connected": "Connected",
    "error.config": "Configuration error",
    "error.service_unreachable": "Service unreachable",
    "error.auth_failed": "Authentication failed (check the API key)",
    "error.upstream": "The service returned an error",
    "error.not_found": "Not found",
    "error.timeout": "The service timed out",
    "error.validation": "Invalid input",
    "error.invalid_url": "Invalid service URL",
    "error.unknown_service_type": "Unknown service type",
    "error.bad_password": "Incorrect password",
    "error.password_mismatch": "Passwords must match and be at least 8 characters",
    "error.locked_out": "Too many attempts - try again shortly",
    "error.create_failed": "Could not create the service (is the slug unique?)",
}


def message_for(code: str | None) -> str:
    """Return the English source string for a status/error code."""
    if not code:
        return ""
    return CODE_MESSAGES.get(code, code)


_LOCALES_DIR = Path(__file__).parent / "locales"
_cache: dict[str, NullTranslations] = {}

Translator = Callable[[str], str]


def _load(locale: str) -> NullTranslations:
    if locale in _cache:
        return _cache[locale]
    translations: NullTranslations
    if locale == DEFAULT_LOCALE:
        translations = NullTranslations()
    else:
        try:
            translations = Translations.load(_LOCALES_DIR, [locale], domain="messages")
        except Exception:  # noqa: BLE001 - missing catalog is non-fatal
            translations = NullTranslations()
    _cache[locale] = translations
    return translations


def normalize_locale(value: str | None) -> str | None:
    if not value:
        return None
    short = value.split("-")[0].split("_")[0].lower().strip()
    return short if short in SUPPORTED_LOCALES else None


def resolve_locale(
    *,
    query: str | None,
    cookie: str | None,
    accept_language: str | None,
    default: str,
) -> str:
    """Resolve the active locale: query > cookie > Accept-Language > default."""
    for candidate in (query, cookie):
        normalized = normalize_locale(candidate)
        if normalized:
            return normalized
    if accept_language:
        for part in accept_language.split(","):
            normalized = normalize_locale(part.split(";")[0])
            if normalized:
                return normalized
    return normalize_locale(default) or DEFAULT_LOCALE


def get_translator(locale: str) -> Translator:
    """Return a ``_`` callable that translates a source string for ``locale``."""
    translations = _load(locale)
    return translations.gettext
