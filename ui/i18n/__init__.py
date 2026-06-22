from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

_LOCALES_DIR = Path(__file__).resolve().parent
_DEFAULT_LOCALE = "ru"

_translations: Dict[str, str] = {}
_current_lang = _DEFAULT_LOCALE
_config_value = "auto"


def supported_languages() -> Tuple[str, ...]:
    return tuple(
        p.stem for p in sorted(_LOCALES_DIR.glob("*.json")) if p.stem != "manifest"
    )


def _detect_system_locale() -> str:
    for getter in (locale.getlocale, locale.getdefaultlocale):
        try:
            loc = getter()
            if loc and loc[0]:
                code = loc[0].split("_")[0].lower()
                if code in supported_languages():
                    return code
        except Exception:
            pass
    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_key, "")
        if val:
            code = val.split(".")[0].split("_")[0].lower()
            if code in supported_languages():
                return code
    return _DEFAULT_LOCALE


def resolve_language(config_value: str) -> str:
    if config_value == "auto":
        return _detect_system_locale()
    if config_value in supported_languages():
        return config_value
    return _DEFAULT_LOCALE


def _load_locale(lang: str) -> Dict[str, str]:
    path = _LOCALES_DIR / f"{lang}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_language(config_value: str) -> str:
    global _translations, _current_lang, _config_value
    _config_value = config_value
    lang = resolve_language(config_value)
    _current_lang = lang
    _translations = _load_locale(lang)
    refresh_language_option_maps()
    return lang


def get_language() -> str:
    return _current_lang


def get_config_language() -> str:
    return _config_value


def t(key: str, **kwargs: Any) -> str:
    text = _translations.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def language_option_labels() -> List[Tuple[str, str]]:
    """Config values and display labels for the language combobox."""
    return [
        ("auto", t("language.auto")),
        ("ru", t("language.ru")),
        ("en", t("language.en")),
    ]


def language_label_for_config(value: str) -> str:
    for cfg_val, label in language_option_labels():
        if cfg_val == value:
            return label
    return language_option_labels()[0][1]


_LANGUAGE_TO_LABEL: Dict[str, str] = {}
_LABEL_TO_LANGUAGE: Dict[str, str] = {}


def refresh_language_option_maps() -> None:
    global _LANGUAGE_TO_LABEL, _LABEL_TO_LANGUAGE
    _LANGUAGE_TO_LABEL = dict(language_option_labels())
    _LABEL_TO_LANGUAGE = {label: val for val, label in _LANGUAGE_TO_LABEL.items()}


def language_from_label(label: str) -> str:
    return _LABEL_TO_LANGUAGE.get(label, "auto")


def label_from_language(value: str) -> str:
    return _LANGUAGE_TO_LABEL.get(value, _LANGUAGE_TO_LABEL.get("auto", "Auto"))


set_language("auto")
refresh_language_option_maps()
