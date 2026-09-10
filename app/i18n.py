"""Startup-owned Japanese/English UI translation; never translate user data.

Source strings are Japanese. Calls use Qt's translation boundary and format
named placeholders only after translation. The saved language is applied once
per application startup, not when pending settings are saved.
"""

from PySide6.QtCore import QCoreApplication, QTranslator

from .translations_en import ENGLISH, ENGLISH_DISAMBIGUATED


def normalize_ui_language(value: object) -> str:
    return value if isinstance(value, str) and value in {"ja", "en"} else "ja"


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
    translator = _EnglishTranslator(application) if language == "en" else None
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
