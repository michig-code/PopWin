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

    # --- [Antigravity Start: Such-Ranking nach Nutzungshäufigkeit (Rest: Claude Code)] ---
    def search(self, text: str, limit: int = 6, pool: list[App] | None = None,
               usage: dict[str, int | dict] | None = None) -> list[App]:
        needle = text.lower().strip()
        if not needle:
            return []
        pool = self.apps if pool is None else pool

        if usage is None:
            usage = getattr(self, "usage", None)
        if usage is None and hasattr(self, "parent") and self.parent() is not None:
            ctx = self.parent()
            if hasattr(ctx, "config") and hasattr(ctx.config, "get_app_usage"):
                usage = ctx.config.get_app_usage()

        def _usage_count(app: App) -> int:
            if not usage:
                return 0
            def _extract(val) -> int:
                if isinstance(val, dict):
                    return int(val.get("count", 0))
                if isinstance(val, (int, float)):
                    return int(val)
                return 0
            k1 = app.name.lower().strip()
            k2 = app.target.lower().strip()
            return max(_extract(usage.get(k1)), _extract(usage.get(k2)))

        matches: list[tuple[int, int, str, App]] = []
        for app in pool:
            name_lower = app.name.lower()
            if needle not in name_lower:
                continue

            if name_lower == needle:
                tier_score = 100
            elif name_lower.startswith(needle):
                tier_score = 80
            elif any(w.startswith(needle) for w in name_lower.split()):
                tier_score = 70
            else:
                tier_score = 20

            u = _usage_count(app)
            # Häufiger genutzte Apps überholen selbst exakte Treffer (z.B. "Minecraft Launcher" > "Minecraft")
            score = u * 50 + tier_score
            matches.append((-score, len(app.name), name_lower, app))

        matches.sort()
        results: list[App] = []
        for item in matches:
            app = item[3]
            if app not in results:
                results.append(app)
            if len(results) >= limit:
                break
        return results
    # --- [Antigravity Ende: Such-Ranking nach Nutzungshäufigkeit] ---
