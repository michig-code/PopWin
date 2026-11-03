"""Sucht auf GitHub nach einer neueren Ausgabe und lädt das Setup dafür.

Gefragt wird nur die öffentliche Release-Liste, ohne Anmeldung, ohne Token und ohne Daten über den Nutzer. Installiert
wird nie still: Das Setup öffnet sich und zeigt selbst, was es tut.

Schlägt die Suche fehl, sagt PopWin, warum: kein Netz, keine Update-Quelle, noch kein Release, zu viele Anfragen oder
ein Fehler bei GitHub. Früher hieß alles „Keine Verbindung“, auch wenn es schlicht noch kein Release gab.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.version import REPO, VERSION

log = logging.getLogger(__name__)
API = "https://api.github.com/repos/{repo}/releases/latest"
REPO_API = "https://api.github.com/repos/{repo}"
TIMEOUT = 15
HEADERS = {"User-Agent": "PopWin", "Accept": "application/vnd.github+json"}  # bewusst ohne Authorization
NO_RELEASE = "keins"  # Ergebnis, wenn das Repository da ist, aber noch nichts veröffentlicht hat

# Was der Nutzer liest; die Einzelheiten stehen im Log.
MESSAGES = {
    "offline": "Keine Verbindung zu GitHub. Prüfe die Internetverbindung.",
    "not_found": "Die Update-Quelle gibt es nicht oder sie ist privat.",
    "rate_limit": "GitHub bremst gerade zu viele Anfragen. Später noch einmal versuchen.",
    "auth": "GitHub verweigert den Zugriff auf die Update-Quelle.",
    "server": "GitHub hat gerade ein Problem. Später noch einmal versuchen.",
    "invalid": "GitHub hat eine unerwartete Antwort geschickt.",
}


class UpdateError(Exception):
    """Ein Fehler bei der Update-Suche mit seiner Art; `kind` ist ein Schlüssel aus MESSAGES."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail

    @property
    def message(self) -> str:
        return MESSAGES.get(self.kind, MESSAGES["invalid"])


def classify(error: Exception) -> UpdateError:
    """Ordnet einen Fehler von urllib einer Art zu. 404 wird erst in fetch_latest genauer unterschieden."""
    if isinstance(error, UpdateError):
        return error
    if isinstance(error, urllib.error.HTTPError):
        code = error.code
        remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
        if code == 404:
            return UpdateError("not_found", f"HTTP 404 {error.url}")
        if code == 429 or (code == 403 and remaining == "0"):
            return UpdateError("rate_limit", f"HTTP {code}, Limit erreicht")
        if code in (401, 403):
            return UpdateError("auth", f"HTTP {code}")
        if code >= 500:
            return UpdateError("server", f"HTTP {code}")
        return UpdateError("invalid", f"HTTP {code}")
    if isinstance(error, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        return UpdateError("offline", str(getattr(error, "reason", error)))
    if isinstance(error, (ValueError, KeyError)):  # kaputtes JSON
        return UpdateError("invalid", str(error))
    return UpdateError("offline", str(error))


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            return json.load(answer)
    except Exception as exc:
        raise classify(exc) from exc
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
    """Das neueste Release, None ohne Update-Quelle. Ein Repository ohne Release ist kein Fehler: Dann kommt
    {"version": ""} zurück, und PopWin gilt als aktuell."""
    if not repo:
        return None
    try:
        return read_release(_get_json(API.format(repo=repo)))
    except UpdateError as error:
        if error.kind != "not_found":
            raise
    # 404 heißt bei /releases/latest beides: Repository fehlt, oder es hat noch kein Release. Einmal nachsehen.
    _get_json(REPO_API.format(repo=repo))  # wirft not_found, wenn es das Repository wirklich nicht gibt
    return {"version": "", "notes": "", "page": "", "setup": None, "portable": None}


def download(asset: dict, progress=None) -> Path:
    """Lädt in einen eigenen Temp-Ordner und prüft Größe und, wenn GitHub sie nennt, die SHA-256-Summe."""
    target = Path(tempfile.mkdtemp(prefix="popwin_update_")) / SETUP_NAME
    digest = hashlib.sha256()
    done = 0
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "PopWin"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer, target.open("wb") as out:
            while chunk := answer.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, asset["size"])
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:  # HTTPError gehört dazu
        raise classify(exc) from exc
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
        except Exception as exc:  # beim nächsten Start wieder; Nutzer und Log erfahren, woran es lag
            error = classify(exc)
            log.info("Update-Suche fehlgeschlagen (%s): %s", error.kind, error.detail)
            self._running = False
            self.finished.emit(error.message)
            return
        self._running = False
        if release is not None and not release["version"]:
            log.info("Update-Suche: %s hat noch kein Release", REPO)
            self.finished.emit(NO_RELEASE)
            return
        if not release or not is_newer(release["version"]):
            self.finished.emit("aktuell")
            return
        if not manual and release["version"] == self.config.get("updates.skipped"):
            self.finished.emit("aktuell")
            return
        self.finished.emit("neu")
        self.found.emit(release)
