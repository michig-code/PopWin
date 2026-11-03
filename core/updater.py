"""Sucht auf GitHub nach einer neueren Ausgabe und lädt das Setup dafür.

Gefragt wird nur die öffentliche Release-Liste, ohne Anmeldung und ohne Daten über den Nutzer. Installiert wird nie still:
Das Setup öffnet sich und zeigt selbst, was es tut.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.version import REPO, VERSION

log = logging.getLogger(__name__)
API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT = 15
SETUP_NAME = "PopWin Setup.exe"
PORTABLE_NAME = "PopWin Portable.zip"


def version_tuple(text: str) -> tuple[int, ...]:
    """"v3.10" -> (3, 10). Zahlen statt Text, sonst wäre 3.10 älter als 3.9."""
    return tuple(int(part) for part in re.findall(r"\d+", text)) or (0,)


def is_newer(remote: str, local: str = VERSION) -> bool:
    return version_tuple(remote) > version_tuple(local)


def read_release(data: dict) -> dict:
    """Das Nötige aus der Antwort von GitHub: Version, Änderungen und die beiden Dateien samt Prüfsumme."""
    assets = {asset.get("name", ""): asset for asset in data.get("assets", [])}

    def file(name: str) -> dict | None:
        asset = assets.get(name) or assets.get(name.replace(" ", "."))  # GitHub ersetzt Leerzeichen in Dateinamen
        if not asset:
            return None
        digest = asset.get("digest") or ""
        return {"url": asset.get("browser_download_url", ""), "size": asset.get("size", 0),
                "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""}

    return {"version": (data.get("tag_name") or "").lstrip("vV"), "notes": data.get("body") or "",
            "page": data.get("html_url", ""), "setup": file(SETUP_NAME), "portable": file(PORTABLE_NAME)}


def fetch_latest(repo: str = REPO) -> dict | None:
    if not repo:
        return None
    request = urllib.request.Request(API.format(repo=repo), headers={"User-Agent": "PopWin", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        return read_release(json.load(answer))


def download(asset: dict, progress=None) -> Path:
    """Lädt in einen eigenen Temp-Ordner und prüft Größe und, wenn GitHub sie nennt, die SHA-256-Summe."""
    target = Path(tempfile.mkdtemp(prefix="popwin_update_")) / SETUP_NAME
    digest = hashlib.sha256()
    done = 0
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "PopWin"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer, target.open("wb") as out:
        while chunk := answer.read(1 << 16):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, asset["size"])
    if asset["size"] and done != asset["size"]:
        raise OSError(f"Download unvollständig: {done} von {asset['size']} Bytes")
    if asset["sha256"] and digest.hexdigest() != asset["sha256"].lower():
        raise OSError("Prüfsumme stimmt nicht, die Datei wurde verworfen")
    return target


class UpdateChecker(QObject):
    """Fragt im Hintergrund; `found` kommt nur, wenn es wirklich etwas Neues gibt und es nicht übersprungen wurde."""
    found = Signal(dict)
    finished = Signal(str)  # Ergebnis für die Einstellungen: "", "neu", "aktuell" oder eine Fehlermeldung

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._running = False

    def check(self, manual: bool = False) -> None:
        if self._running or (not manual and not self.config.get("updates.auto_check")):
            return
        if not REPO:
            self.finished.emit("Für diese Ausgabe ist noch keine Update-Quelle eingetragen.")
            return
        self._running = True
        threading.Thread(target=self._run, args=(manual,), daemon=True).start()

    def _run(self, manual: bool) -> None:
        try:
            release = fetch_latest()
        except Exception as exc:  # offline oder GitHub antwortet nicht: beim nächsten Start wieder
            log.info("Update-Suche fehlgeschlagen: %s", exc)
            self._running = False
            self.finished.emit("Keine Verbindung zu GitHub.")
            return
        self._running = False
        if not release or not is_newer(release["version"]):
            self.finished.emit("aktuell")
            return
        if not manual and release["version"] == self.config.get("updates.skipped"):
            self.finished.emit("aktuell")
            return
        self.finished.emit("neu")
        self.found.emit(release)
