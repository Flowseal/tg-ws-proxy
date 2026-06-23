from __future__ import annotations

import json
import locale
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

LocaleInput = Union[str, "LocaleEnum"]


class LocaleEnum(str, Enum):
    russian = "ru"
    english = "en"

    @classmethod
    def parse(cls, value: LocaleInput) -> LocaleEnum:
        if isinstance(value, cls):
            return value
        
        try:
            return cls(value)
        except ValueError:
            return _DEFAULT_LOCALE


_LOCALES_DIR = Path(__file__).resolve().parent
_DEFAULT_LOCALE = LocaleEnum.english

_translations: Dict[str, str] = {}
_current_lang: LocaleEnum = _DEFAULT_LOCALE
_config_value: LocaleEnum = _DEFAULT_LOCALE

_LANGUAGE_TO_LABEL: Dict[LocaleEnum, str] = {}
_LABEL_TO_LANGUAGE: Dict[str, LocaleEnum] = {}


def _locale_json_files() -> Tuple[str, ...]:
    return tuple(
        p.stem for p in sorted(_LOCALES_DIR.glob("*.json")) if p.stem != "manifest"
    )


def supported_languages() -> Tuple[str, ...]:
    """Locale codes that have a JSON catalog on disk (e.g. ru, en)."""
    return _locale_json_files()


def content_locales() -> Tuple[LocaleEnum, ...]:
    return tuple(
        LocaleEnum(stem)
        for stem in _locale_json_files()
        if stem in LocaleEnum._value2member_map_
    )


def detect_system_language() -> LocaleEnum:
    """Pick the best locale from available catalogs, else Russian."""
    available = content_locales()
    if not available:
        return _DEFAULT_LOCALE

    for getter in (locale.getlocale, locale.getdefaultlocale):
        try:
            loc = getter()
            if loc and loc[0]:
                code = loc[0].split("_")[0].lower()
                try:
                    candidate = LocaleEnum(code)
                    if candidate in available:
                        return candidate
                except ValueError:
                    pass
        except Exception:
            pass
    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_key, "")
        if val:
            code = val.split(".")[0].split("_")[0].lower()
            try:
                candidate = LocaleEnum(code)
                if candidate in available:
                    return candidate
            except ValueError:
                pass
    return _DEFAULT_LOCALE


def resolve_language(config_value: LocaleInput) -> LocaleEnum:
    loc = LocaleEnum.parse(config_value)
    if loc.value in supported_languages():
        return loc
    return _DEFAULT_LOCALE


def _load_locale(lang: LocaleEnum) -> Dict[str, str]:
    path = _LOCALES_DIR / f"{lang.value}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_language(config_value: LocaleInput) -> LocaleEnum:
    global _translations, _current_lang, _config_value
    _config_value = LocaleEnum.parse(config_value)
    _current_lang = resolve_language(_config_value)
    _translations = _load_locale(_current_lang)
    refresh_language_option_maps()
    return _current_lang


def get_language() -> LocaleEnum:
    return _current_lang


def get_config_language() -> LocaleEnum:
    return _config_value


def t(key: str, **kwargs: Any) -> str:
    text = _translations.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def language_option_labels() -> List[Tuple[LocaleEnum, str]]:
    """Config values and display labels for the language combobox."""
    return [
        (loc, t(f"language.{loc.value}"))
        for loc in content_locales()
    ]


def language_label_for_config(value: LocaleInput) -> str:
    loc = LocaleEnum.parse(value)
    labels = language_option_labels()
    for cfg_val, label in labels:
        if cfg_val == loc:
            return label
    return labels[0][1] if labels else _DEFAULT_LOCALE.value


def refresh_language_option_maps() -> None:
    global _LANGUAGE_TO_LABEL, _LABEL_TO_LANGUAGE
    _LANGUAGE_TO_LABEL = dict(language_option_labels())
    _LABEL_TO_LANGUAGE = {label: val for val, label in _LANGUAGE_TO_LABEL.items()}


def language_from_label(label: str) -> LocaleEnum:
    return _LABEL_TO_LANGUAGE.get(label, _DEFAULT_LOCALE)


def label_from_language(value: LocaleInput) -> str:
    loc = LocaleEnum.parse(value)
    return _LANGUAGE_TO_LABEL.get(
        loc,
        _LANGUAGE_TO_LABEL.get(_DEFAULT_LOCALE, _DEFAULT_LOCALE.value),
    )


set_language(detect_system_language())
