"""Optionale Erweiterungen. Falls ein privates Zusatzpaket vorhanden ist, hängt es sich hier beim Start ein.

Fehlt das Zusatzpaket, ist is_dev() False und alle Funktionen hier geben harmlose Standardwerte zurück.
"""
from __future__ import annotations

import importlib
import logging

from core.paths import FROZEN, ROOT

log = logging.getLogger(__name__)

PRIVATE_PACKAGE = "_private.devmenu"
PRIVATE_DIR = ROOT / "_private" / "devmenu"
# Nur der optionale Zusatz-Build legt diese Markierung ins Paket; ohne sie bleibt die Erweiterung aus.
BUILD_MARKER = "privater-build.txt"


def _available() -> bool:
    if FROZEN:
        return (PRIVATE_DIR / BUILD_MARKER).is_file()
    return (PRIVATE_DIR / "__init__.py").is_file()


def _load():
    """Lädt das optionale Zusatzmodul einmal beim Import, falls vorhanden."""
    if not _available():
        return None
    try:
        module = importlib.import_module(PRIVATE_PACKAGE)
        module.install()
    except Exception as exc:  # noqa: BLE001 - ein fehlerhaftes Zusatzmodul darf den Start nicht verhindern (F-10)
        log.exception("Zusatzmodul nicht geladen, PopWin läuft ohne Erweiterung weiter: %s", exc)
        return None
    return module


_PRIVATE = _load()


def is_dev() -> bool:
    return _PRIVATE is not None


def dev_label(key: str) -> str:
    """Textbaustein aus dem optionalen Zusatzmodul oder leer."""
    return _PRIVATE.label(key) if is_dev() else ""


def extra_nova_tools(tool_cls, ask: str, always: str, optional_flag: str) -> tuple:
    """Zusätzliche Werkzeuge aus dem optionalen Zusatzmodul oder leeres Tupel."""
    return _PRIVATE.extra_nova_tools(tool_cls, ask, always, optional_flag) if is_dev() else ()


def dev_menu_wanted(query: str) -> bool:
    """Ob eine Suche das optionale Zusatzfenster meint. Ohne Zusatzmodul immer False."""
    return is_dev() and _PRIVATE.wants_menu(query)


def open_dev_menu(ctx) -> None:
    """Öffnet das optionale Zusatzfenster; Fehler bleiben gekapselt."""
    if not is_dev():
        return
    try:
        _PRIVATE.open_menu(ctx)
    except Exception:  # noqa: BLE001 - Schutz vor Absturz der Oberfläche, siehe popwin-devmenu-analyse.md (R-3)
        log.exception("Zusatzfenster konnte nicht geöffnet werden")


def open_dev_window():
    """Das offene Zusatzfenster (für die Taskbar), sonst None."""
    return _PRIVATE.current_window() if is_dev() else None


def dev_qss() -> str:
    """Optionales zusätzliches Stylesheet aus dem Zusatzmodul, sonst leer."""
    return _PRIVATE.dev_qss() if is_dev() else ""


def make_claude_button(ctx):
    """Optionaler Zusatz-Button aus dem Zusatzmodul oder None."""
    if not is_dev():
        return None
    from _private.devmenu.claude_button import ClaudeButton
    return ClaudeButton(ctx)


def is_claude_window(window) -> bool:
    if not is_dev():
        return False
    from _private.devmenu.claude_button import is_claude_window as check
    return check(window)
