"""Index aller installierten Apps (klassisch und Store) für Launcher und Startmenü."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class App:
    name: str
    target: str


def _powershell_apps() -> list[App]:
    command = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(result.stdout.decode("utf-8", "replace") or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    return [App(r["Name"], f"shell:AppsFolder\\{r['AppID']}") for r in rows if r.get("Name") and r.get("AppID")]


def _shortcut_apps() -> list[App]:
    """Verknüpfungen aus Startmenü und Desktop; auch die Rückfallebene, falls Get-StartApps nicht läuft."""
    roots = [Path(os.environ.get(v, "")) / "Microsoft/Windows/Start Menu/Programs" for v in ("PROGRAMDATA", "APPDATA")]
    roots += [Path.home() / "Desktop", Path(os.environ.get("PUBLIC", "")) / "Desktop"]
    apps = []
    for root in roots:
        if root.is_dir():
            apps += [App(p.stem, str(p)) for p in root.rglob("*.lnk") if "uninstall" not in p.stem.lower()]
    return apps


class AppIndex(QThread):
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.apps: list[App] = []
        self.shortcuts: list[App] = []

    def run(self) -> None:
        try:
            apps = _powershell_apps()
        except Exception as exc:
            log.warning("Get-StartApps fehlgeschlagen: %s", exc)
            apps = []
        self.shortcuts = _shortcut_apps()
        apps = apps or self.shortcuts
        unique = {a.name.lower(): a for a in apps}
        self.apps = sorted(unique.values(), key=lambda a: a.name.lower())
        self.ready.emit()

    def pickable(self) -> list[App]:
        """Alles zum Anheften. Die Verknüpfung gewinnt: nur sie verrät, welches Programm dahintersteckt."""
        unique = {a.name.lower(): a for a in self.apps + self.shortcuts}
        return sorted(unique.values(), key=lambda a: a.name.lower())

    def search(self, text: str, limit: int = 6, pool: list[App] | None = None) -> list[App]:
        needle = text.lower().strip()
        if not needle:
            return []
        pool = self.apps if pool is None else pool
        starts = [a for a in pool if a.name.lower().startswith(needle)]
        words = [a for a in pool if a not in starts and any(w.startswith(needle) for w in a.name.lower().split())]
        inner = [a for a in pool if a not in starts and a not in words and needle in a.name.lower()]
        return (starts + words + inner)[:limit]
