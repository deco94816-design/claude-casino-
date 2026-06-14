# -*- coding: utf-8 -*-
"""Minimal language helpers for casino bot (de/fr/zh file-based + detect)."""

from __future__ import annotations

import re

# Callback / command checks use membership
SUPPORTED_LANGS = frozenset({"en", "ru", "de", "fr", "zh"})

# Optional extended strings for translate_text() substitution (en → target), used by translate_text()
LANG_STRINGS: dict[str, dict[str, str]] = {
    "en": {},
    "de": {},
    "fr": {},
    "zh": {},
}


def detect_lang(language_code: str | None) -> str:
    """Map Telegram language_code to one of SUPPORTED_LANGS (default en)."""
    if not language_code:
        return "en"
    code = language_code.lower().strip()
    base = code.split("-")[0]
    if base == "ru" or code.startswith("ru"):
        return "ru"
    if base == "de":
        return "de"
    if base == "fr":
        return "fr"
    if base.startswith("zh"):
        return "zh"
    return "en"


def get_lang_string(key: str, lang: str) -> str:
    """Return translation for key in lang, or key if missing (matches t() fallback)."""
    if lang in LANG_STRINGS:
        return LANG_STRINGS[lang].get(key, key)
    return key
