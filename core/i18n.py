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

LANGUAGES = {"de": "Deutsch", "en": "English", "es": "Español", "fr": "Français"}
AUTO = "auto"
_table: dict[str, str] = {}
_patterns: list[tuple[re.Pattern, str]] = []
_FILTER: list = []


def wanted_language(setting: str, system: str | None = None) -> str:
    """Die eingestellte Sprache; auf Automatisch die von Windows, oder Englisch, wenn es sie hier nicht gibt."""
    if setting in LANGUAGES:
        return setting
    code = (system or QLocale.system().name())[:2]
    return code if code in LANGUAGES else "en"


def load(code: str) -> None:
    _table.clear()
    _patterns.clear()
    if code == "de":
        return
    try:
        table = json.loads((ROOT / "assets" / "lang" / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Sprache %s nicht lesbar: %s", code, exc)
        return
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
