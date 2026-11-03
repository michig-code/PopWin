"""Wo PopWin liegt und wie seine Teile gestartet werden: als Skripte beim Entwickeln, als PopWin.exe installiert."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(__file__).resolve().parent.parent
PORTABLE = FROZEN and Path(sys.executable).with_name("portable.txt").exists()
# Installiert darf nichts neben dem Programm liegen: Einstellungen überleben so jedes Update.
# Portabel bleibt alles im eigenen Ordner, damit PopWin auf dem USB-Stick mitkommt.
if PORTABLE:
    DATA_DIR = Path(sys.executable).with_name("data")
elif FROZEN:
    DATA_DIR = Path(os.environ.get("APPDATA", str(ROOT))) / "PopWin"
else:
    DATA_DIR = ROOT / "config"
LOG_DIR = DATA_DIR if FROZEN else ROOT
SCRIPTS = {
    "main": "main.py",
    "wallpaper": "wallpaper/app.py",
    "cleanup": "core/cleanup.py",
    "explorer": "explorer/app.py",
    "editor": "editor/app.py",
    "terminal": "terminal/app.py",
    "studio": "studio/app.py",
    "stream": "stream/app.py",
    "watchdog": "core/taskbar_watchdog.py",
    "nova_tool": "core/nova_tool_runner.py",
}
APP_FLAG = "--app"


def pythonw_path() -> str:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate if candidate.exists() else sys.executable)


def app_command(name: str, *arguments: str) -> list[str]:
    """Die Befehlszeile, die einen Teil von PopWin startet."""
    if FROZEN:
        return [sys.executable, *(() if name == "main" else (APP_FLAG, name)), *arguments]
    return [pythonw_path(), str(ROOT / SCRIPTS[name]), *arguments]


def app_shortcut(name: str) -> tuple[str, str]:
    """(Programm, Argumente) für Verknüpfungen und Registry-Einträge."""
    command = app_command(name)
    return command[0], " ".join(f'"{part}"' for part in command[1:])


def app_from_arguments(arguments: list[str]) -> tuple[str, list[str]]:
    """Welcher Teil der installierten PopWin.exe gestartet wurde, und seine eigenen Argumente."""
    if arguments[:1] == [APP_FLAG] and len(arguments) > 1:
        return arguments[1], arguments[2:]
    return "main", arguments
