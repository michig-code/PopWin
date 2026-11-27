"""Sprachen für PopWin. Der Code bleibt deutsch; `assets/lang/<code>.json` ordnet jedem deutschen Text seine Übersetzung zu.

Texte werden an einer Stelle übersetzt: beim Setzen und wenn ein Widget zum ersten Mal erscheint. So braucht kein Fenster
etwas davon zu wissen, und ein neuer Text fällt ohne Übersetzung einfach auf Deutsch zurück. Für eine weitere Sprache genügt
eine neue JSON-Datei und ein Eintrag in LANGUAGES.
"""
from __future__ import annotations

import json
import logging
import re

from PySide6.QtCore import QEvent, QLocale, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QGroupBox, QHeaderView, QLabel, QLineEdit, QListWidget, QMenu, QPlainTextEdit,
    QTableWidget, QTabBar, QTreeWidget, QWidget,
)

from core.paths import ROOT

log = logging.getLogger(__name__)

BASE = "de"  # Der Code ist deutsch: Für Deutsch braucht es keine Datei
FALLBACK = "en"
AUTO = "system"  # Wert der Einstellung "language"; das frühere "auto" gilt weiter als Systemsprache
_LEGACY_AUTO = ("auto", "", None)
_NAMES = {"de": "Deutsch", "en": "English", "es": "Español", "fr": "Français", "it": "Italiano", "pt": "Português (Brasil)",
          "nl": "Nederlands", "pl": "Polski", "tr": "Türkçe", "ru": "Русский", "uk": "Українська", "ja": "日本語",
          "ko": "한국어", "zh": "中文（简体）", "ar": "العربية", "sv": "Svenska", "da": "Dansk", "nb": "Norsk", "cs": "Čeština",
          "hu": "Magyar", "el": "Ελληνικά", "hi": "हिन्दी", "ro": "Română", "fi": "Suomi"}
_table: dict[str, str] = {}
_patterns: list[tuple[re.Pattern, str]] = []
_FILTER: list = []


def _lang_dir():
    return ROOT / "assets" / "lang"


def available() -> list[str]:
    """Alle Sprachcodes: Deutsch (eingebaut) plus jede Datei in assets/lang. Neue Sprachen erscheinen so von selbst."""
    codes = {BASE}
    try:
        codes.update(path.stem for path in _lang_dir().glob("*.json"))
    except OSError:
        pass
    return sorted(codes, key=lambda code: (code != BASE, code != FALLBACK, code))


def language_name(code: str) -> str:
    if code in _NAMES:
        return _NAMES[code]
    name = QLocale(code).nativeLanguageName()
    return (name[:1].upper() + name[1:]) if name else code


def languages() -> dict[str, str]:
    """Sprachcode -> eigener Sprachname, für die Auswahl in den Einstellungen."""
    return {code: language_name(code) for code in available()}


def match_language(candidates, codes=None) -> str | None:
    """Bildet Windows-Sprachkennungen wie "de-AT" oder "pt_BR" auf einen vorhandenen Code ab (erst genau, dann ohne Region)."""
    codes = list(codes if codes is not None else available())
    lowered = {code.lower().replace("_", "-"): code for code in codes}
    for candidate in candidates:
        tag = str(candidate).strip().lower().replace("_", "-")
        while tag:
            if tag in lowered:
                return lowered[tag]
            tag = tag.rpartition("-")[0]
    return None


def system_language() -> str:
    try:
        locale = QLocale.system()
        tags = list(locale.uiLanguages()) + [locale.name()]
    except Exception:  # noqa: BLE001 - die Systemsprache ist nie wichtig genug zum Abstürzen
        tags = []
    return match_language(tags) or FALLBACK


def wanted_language(setting: str, system: str | None = None) -> str:
    """Die eingestellte Sprache; auf Systemsprache die von Windows, sonst Englisch."""
    codes = available()
    if setting in codes:
        return setting
    if setting in _LEGACY_AUTO or setting == AUTO:
        return (match_language([system], codes) or FALLBACK) if system else system_language()
    return FALLBACK


def _read(code: str) -> dict[str, str]:
    try:
        return json.loads((_lang_dir() / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Sprache %s nicht lesbar: %s", code, exc)
        return {}


def load(code: str) -> None:
    """Ersatzkette: gewählte Sprache -> Englisch -> der deutsche Schlüssel selbst."""
    _table.clear()
    _patterns.clear()
    if code == BASE:
        return
    table = _read(FALLBACK) if code != FALLBACK else {}
    table.update(_read(code))
    _table.update(table)
    templates = [key for key in table if "{}" in key]
    for key in sorted(templates, key=len, reverse=True):  # längere zuerst, sie sind genauer
        _patterns.append((re.compile("^" + re.escape(key).replace(r"\{\}", "(.+?)") + "$", re.S), table[key]))


def translate(text: str) -> str:
    if not _table or not text:
        return text
    direct = _table.get(text)
    if direct is not None:
        return direct
    for pattern, target in _patterns:
        match = pattern.match(text)
        if match:
            values = iter(translate(value) for value in match.groups())
            return re.sub(r"\{\}", lambda _found: next(values), target)
    return text


def _wrap(owner, name: str, position: int = 0) -> None:
    original = getattr(owner, name)

    def patched(self, *arguments):
        arguments = list(arguments)
        if len(arguments) > position and isinstance(arguments[position], str):
            arguments[position] = translate(arguments[position])
        return original(self, *arguments)
    setattr(owner, name, patched)


def _translate_widget(widget: QWidget) -> None:
    if isinstance(widget, (QLabel, QAbstractButton)):
        text = widget.text()
        if translate(text) != text:
            widget.setText(text)  # das gepatchte setText übersetzt
    if isinstance(widget, (QLineEdit, QPlainTextEdit)):
        widget.setPlaceholderText(widget.placeholderText())
    if isinstance(widget, QGroupBox):
        widget.setTitle(widget.title())
    if isinstance(widget, QComboBox):
        for index in range(widget.count()):
            widget.setItemText(index, translate(widget.itemText(index)))
    if isinstance(widget, QTabBar):
        for index in range(widget.count()):
            widget.setTabText(index, translate(widget.tabText(index)))
    if isinstance(widget, QListWidget):
        for index in range(widget.count()):
            widget.item(index).setText(translate(widget.item(index).text()))
    if isinstance(widget, QTreeWidget):
        header = widget.headerItem()
        for column in range(widget.columnCount()):
            header.setText(column, translate(header.text(column)))
    if isinstance(widget, QTableWidget):
        for column in range(widget.columnCount()):
            item = widget.horizontalHeaderItem(column)
            if item:
                item.setText(translate(item.text()))
    if isinstance(widget, QHeaderView) and widget.model() is not None:
        pass  # die Spaltenköpfe des Explorers kommen aus dem Modell, siehe headerData
    if isinstance(widget, QMenu):
        widget.setTitle(widget.title())
        for action in widget.actions():
            action.setText(translate(action.text()))
    if widget.isWindow() and widget.windowTitle():
        widget.setWindowTitle(widget.windowTitle())
    if widget.toolTip():
        widget.setToolTip(widget.toolTip())


class _Translator(QObject):
    """Fängt alles, was im Konstruktor gesetzt wurde: Das läuft an den Setzern vorbei."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.Polish, QEvent.Type.Show) and isinstance(watched, QWidget):
            _translate_widget(watched)
        return False


def install(app: QApplication, setting: str) -> str:
    code = wanted_language(setting)
    QLocale.setDefault(QLocale(code))  # Monatsnamen und Wochentage
    load(code)
    if code == "de" or _FILTER:
        return code
    for owner, name in ((QLabel, "setText"), (QAbstractButton, "setText"), (QWidget, "setToolTip"), (QWidget, "setWindowTitle"),
                        (QLineEdit, "setPlaceholderText"), (QPlainTextEdit, "setPlaceholderText"), (QGroupBox, "setTitle"),
                        (QAction, "setText"), (QAction, "setToolTip"), (QMenu, "setTitle")):
        _wrap(owner, name)
    _FILTER.append(_Translator(app))
    app.installEventFilter(_FILTER[0])
    return code
