"""Startup-owned UI translation; never translate user data.

Source strings are Japanese. Calls use Qt's translation boundary and format
named placeholders only after translation. The saved language is applied once
per application startup, not when pending settings are saved.
"""

import ctypes
import sys

from PySide6.QtCore import QCoreApplication, QTranslator

from .translations_en import ENGLISH, ENGLISH_DISAMBIGUATED
from .translations_zh import (
    SIMPLIFIED_CHINESE,
    SIMPLIFIED_CHINESE_DISAMBIGUATED,
    TRADITIONAL_CHINESE,
    TRADITIONAL_CHINESE_DISAMBIGUATED,
)

UI_LANGUAGES = ("ja", "en", "zh-Hans", "zh-Hant")
UI_LANGUAGE_LABELS = {
    "ja": "日本語",
    "en": "English",
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
}
UI_LANGUAGE_CHOICES = tuple(
    (UI_LANGUAGE_LABELS[language], language) for language in UI_LANGUAGES
)

_LANGUAGE_ALIASES = {
    "en_us": "en",
    "en-us": "en",
    "en_gb": "en",
    "en-gb": "en",
    "ja_jp": "ja",
    "ja-jp": "ja",
    "zh": "zh-Hans",
    "zh_cn": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh_sg": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh_hans": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh_hans_cn": "zh-Hans",
    "zh-hans-cn": "zh-Hans",
    "zh_tw": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh_hk": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh_mo": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh_hant": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh_hant_tw": "zh-Hant",
    "zh-hant-tw": "zh-Hant",
}


def normalize_ui_language(value: object) -> str:
    if not isinstance(value, str):
        return "ja"
    if value in UI_LANGUAGES:
        return value
    return _LANGUAGE_ALIASES.get(value.strip().lower(), "ja")


def detect_windows_ui_language() -> str:
    """Map the current user's Windows display language to a UI language.

    This intentionally uses GetUserDefaultUILanguage rather than locale APIs;
    date/number formatting preferences must not change the application's UI.
    Non-Windows hosts and unavailable APIs use English as the safe fallback.
    """
    if sys.platform != "win32":
        return "en"
    try:
        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except (AttributeError, OSError, TypeError, ValueError):
        return "en"
    primary = lang_id & 0x03FF
    if primary == 0x11:  # LANG_JAPANESE
        return "ja"
    if primary == 0x04:  # LANG_CHINESE; sublanguage selects script
        sublanguage = (lang_id >> 10) & 0x3F
        if sublanguage in {1, 3, 5}:  # Taiwan, Hong Kong, Macau
            return "zh-Hant"
        return "zh-Hans"
    return "en"


def active_ui_language() -> str:
    application = QCoreApplication.instance()
    return normalize_ui_language(application.property("nivis_ui_language")) if application else "ja"


class _EnglishTranslator(QTranslator):
    def isEmpty(self) -> bool:
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context == "NivisViewer":
            return ENGLISH_DISAMBIGUATED.get((sourceText, disambiguation), ENGLISH.get(sourceText))
        # None maps to a null QString (not found). An empty string is a valid
        # empty translation and would erase Qt's own dialog/button captions.
        return None


class _CatalogTranslator(QTranslator):
    def __init__(self, catalog, disambiguated, parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._disambiguated = disambiguated

    def isEmpty(self) -> bool:
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context == "NivisViewer":
            return self._disambiguated.get(
                (sourceText, disambiguation), self._catalog.get(sourceText)
            )
        return None


def install_ui_language(language: object) -> None:
    """Install before constructing UI (also usable by isolated Qt tests)."""
    application = QCoreApplication.instance()
    if application is None:
        return
    previous = getattr(application, "_nivis_translator", None)
    if previous is not None:
        application.removeTranslator(previous)
        previous.deleteLater()
    language = normalize_ui_language(language)
    if language == "en":
        translator = _EnglishTranslator(application)
    elif language == "zh-Hans":
        translator = _CatalogTranslator(
            SIMPLIFIED_CHINESE, SIMPLIFIED_CHINESE_DISAMBIGUATED, application
        )
    elif language == "zh-Hant":
        translator = _CatalogTranslator(
            TRADITIONAL_CHINESE, TRADITIONAL_CHINESE_DISAMBIGUATED, application
        )
    else:
        translator = None
    application._nivis_translator = translator
    if translator is not None:
        application.installTranslator(translator)
    application.setProperty("nivis_ui_language", language)


def initialize_ui_language(language: object) -> None:
    application = QCoreApplication.instance()
    if application is not None and application.property("nivis_ui_language") is None:
        install_ui_language(language)


def tr(source: str, *, disambiguation: str | None = None, **values: object) -> str:
    translated = QCoreApplication.translate("NivisViewer", source, disambiguation)
    return translated.format(**values) if values else translated
