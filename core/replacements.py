"""Windows-Apps, die PopWin durch eine eigene Version ersetzt. Jede lässt sich einzeln abschalten.

Eine neue PopWin-App braucht nur einen Eintrag in REPLACEMENTS; Schalter und Startwege ergeben sich daraus.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core import win_api

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "apps"
EXPLORER_LINKS = ("file explorer", "datei-explorer", "explorer")
POPWIN_NAMES = {"explorer": "PopWin Explorer", "settings": "PopWin Einstellungen", "nova": "Nova",
                "editor": "PopWin Editor", "terminal": "PopWin Terminal", "studio": "PowerShell Studio", "widgets": "PopWin Widgets",
                "stream": "PopWin Stream"}
TERMINALS = ("cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "windowsterminal.exe")


def program_name(target: str) -> str:
    """Name der .exe hinter einem Ziel; leer, wenn eine Verknüpfung keins hat (wie die vom Explorer)."""
    if target.lower().endswith(".lnk"):
        target = win_api.shortcut_target(target)
    return os.path.basename(target).lower()


def is_explorer(target: str) -> bool:
    if os.path.isdir(target) or target.lower().endswith("\\microsoft.windows.explorer"):
        return True
    if target.lower().endswith(".lnk") and not program_name(target):
        return os.path.splitext(os.path.basename(target))[0].lower() in EXPLORER_LINKS
    return program_name(target) == "explorer.exe"


def is_editor(target: str) -> bool:
    return "microsoft.windowsnotepad" in target.lower() or program_name(target) == "notepad.exe"


def is_terminal(target: str) -> bool:
    return "microsoft.windowsterminal" in target.lower() or program_name(target) in TERMINALS


def is_studio(target: str) -> bool:
    return program_name(target) == "powershell_ise.exe"


def open_explorer(ctx, target: str) -> None:
    ctx.open_explorer(target if os.path.isdir(target) else "")


def open_plain(ctx, name: str) -> None:
    ctx.open_app(name)


@dataclass(frozen=True)
class Replacement:
    key: str
    label: str
    matches: Callable[[str], bool]
    open: Callable[[object, str], None]
    exe: str  # die Taskbar legt das Fenster auf den Platz dieses Programms


REPLACEMENTS = (
    Replacement("explorer", "PopWin Explorer statt Windows-Explorer", is_explorer, open_explorer, "explorer.exe"),
    Replacement("editor", "PopWin Editor statt Notepad", is_editor, lambda c, _t: open_plain(c, "editor"), "notepad.exe"),
    Replacement("terminal", "PopWin Terminal statt Terminal und Eingabeaufforderung", is_terminal,
                lambda c, _t: open_plain(c, "terminal"), "windowsterminal.exe"),
    Replacement("studio", "PowerShell Studio statt PowerShell ISE", is_studio, lambda c, _t: open_plain(c, "studio"),
                "powershell_ise.exe"),
)


def app_icon(key: str) -> str:
    return str(ASSETS / f"{key}.ico")


def taskbar_slot(ctx, app: str) -> tuple[str, dict]:
    """Wo das Fenster einer PopWin-App in der Taskbar landet: Ein Ersatz nimmt den Platz des Originals ein."""
    for replacement in REPLACEMENTS:
        if replacement.key == app and ctx.config.get(f"replacements.{app}"):
            return replacement.exe, {"target": replacement.exe, "name": POPWIN_NAMES[app].removeprefix("PopWin "),
                                     "windows": []}
    return f"popwin:{app}", {"target": "", "name": POPWIN_NAMES[app], "icon": app_icon(app), "windows": []}


def launch(ctx, target: str) -> bool:
    """Startet die PopWin-Version, falls es eine gibt und sie eingeschaltet ist, sonst das Original."""
    for replacement in REPLACEMENTS:
        if ctx.config.get(f"replacements.{replacement.key}") and replacement.matches(target):
            replacement.open(ctx, target)
            return True
    return win_api.shell_open(target)
