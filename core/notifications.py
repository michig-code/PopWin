"""Liest die Benachrichtigungen aus der Datenbank der Windows-Benachrichtigungszentrale.

Die offizielle Schnittstelle (UserNotificationListener) verlangt eine verpackte App und meldet hier
"Nicht implementiert". Deshalb liest PopWin eine Kopie der Datenbank; die Datei selbst bleibt unberührt.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

DATABASE = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Notifications/wpndatabase.db"
_FILETIME_START = datetime(1601, 1, 1, tzinfo=timezone.utc)
QUERY = ('SELECT n."Order", h.PrimaryId, n.Payload, n.ArrivalTime FROM Notification n '
         "JOIN NotificationHandler h ON n.HandlerId = h.RecordId "
         "WHERE n.Type = 'toast' AND n.ExpiryTime > ? ORDER BY n.ArrivalTime DESC")


@dataclass(frozen=True)
class Notice:
    id: int
    app_id: str
    title: str
    body: str
    arrival: datetime
    image: str = ""
    logo: str = ""

    @property
    def target(self) -> str:
        return "shell:AppsFolder\\" + self.app_id


def from_filetime(value: int) -> datetime:
    return (_FILETIME_START + timedelta(microseconds=value // 10)).astimezone()


def to_filetime(moment: datetime) -> int:
    return (moment - _FILETIME_START) // timedelta(microseconds=1) * 10


def local_image(source: str) -> str:
    """Pfad eines Bildes auf diesem PC. Bilder aus dem Netz lädt PopWin nicht: Das würde dem Absender verraten,
    wann die Nachricht angesehen wurde."""
    path = source
    if source.lower().startswith("file:"):
        path = unquote(urlparse(source).path).lstrip("/")
    path = os.path.normpath(path) if re.match(r"^[A-Za-z]:[\\/]", path) else ""
    return path if path and os.path.isfile(path) else ""


def parse_payload(payload: bytes) -> tuple[str, str, str, str]:
    """Titel, Text, großes Bild und kleines Logo; die erste Textzeile einer Benachrichtigung ist ihr Titel."""
    try:
        root = ET.fromstring(payload.decode("utf-8", "replace").rstrip("\0"))
    except ET.ParseError:
        return "", "", "", ""
    lines = [item.text.strip() for item in root.iter("text")
             if item.text and item.text.strip() and item.get("placement") != "attribution"]
    picture = logo = ""
    for item in root.iter("image"):
        path = local_image(item.get("src", ""))
        if item.get("placement") == "appLogoOverride":
            logo = logo or path
        else:
            picture = picture or path
    return (lines[0], "\n".join(lines[1:]), picture, logo) if lines else ("", "", "", "")


def read_notices(database: Path = DATABASE) -> list[Notice]:
    now = to_filetime(datetime.now(timezone.utc))
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "notifications.db"
        shutil.copy(database, copy)
        for suffix in ("-wal", "-shm"):  # neue Einträge stehen oft noch im Write-Ahead-Log
            companion = database.with_name(database.name + suffix)
            if companion.exists():
                shutil.copy(companion, copy.with_name(copy.name + suffix))
        connection = sqlite3.connect(copy)
        try:
            rows = connection.execute(QUERY, (now,)).fetchall()
        finally:
            connection.close()
    notices = []
    for order, app_id, payload, arrival in rows:
        title, body, picture, logo = parse_payload(payload)
        if title:
            notices.append(Notice(order, app_id.strip().replace("/", "\\"), title, body, from_filetime(arrival),
                                  picture, logo))
    return notices


def visible_notices(notices: list[Notice], hidden: list[int]) -> list[Notice]:
    return [notice for notice in notices if notice.id not in set(hidden)]


def app_name(app_id: str, apps: list) -> str:
    """Der Name aus dem Startmenü, sonst der lesbare Teil der App-Kennung."""
    target = ("shell:appsfolder\\" + app_id).lower()
    for app in apps:
        if app.target.lower() == target:
            return app.name
    tail = app_id.split("!")[0].split("_")[0].rsplit("\\", 1)[-1].removesuffix(".exe")
    return tail.rsplit(".", 1)[-1] or app_id


def app_tail(app_id: str) -> str:
    """Lesbarer Kern einer App-Kennung: „com.squirrel.Discord.Discord“ -> „discord“."""
    tail = app_id.split("!")[0].split("_")[0].rsplit("\\", 1)[-1].lower().removesuffix(".exe")
    return tail.rsplit(".", 1)[-1]


def notice_labels(notices: list[Notice], apps: list) -> dict[str, str]:
    """App-Namen der Benachrichtigungen, einmal nachgeschlagen statt für jeden Knopf neu."""
    return {notice.app_id: app_name(notice.app_id, apps).lower() for notice in notices}


def notice_belongs(app_id: str, key: str, target: str, name: str, label: str = "") -> bool:
    """Gehört eine Benachrichtigung zu dem Taskbar-Knopf mit dieser Kennung, diesem Ziel und Namen?"""
    if target and target.lower() == ("shell:appsfolder\\" + app_id).lower():
        return True
    stem = os.path.basename(key.lower()).removesuffix(".exe")
    if stem and stem == app_tail(app_id):
        return True
    return bool(name) and bool(label) and label == name.lower()


def badge_count(notices: list[Notice], key: str, target: str, name: str, labels: dict[str, str]) -> int:
    """Zahl für den roten Punkt am App-Icon: offene Benachrichtigungen dieser App."""
    return sum(notice_belongs(n.app_id, key, target, name, labels.get(n.app_id, "")) for n in notices)


def badge_text(count: int) -> str:
    return "" if count <= 0 else ("9+" if count > 9 else str(count))


def format_age(moment: datetime, now: datetime) -> str:
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "Gerade eben"
    if minutes < 60:
        return f"vor {minutes} Min."
    if minutes < 12 * 60:
        return f"vor {minutes // 60} Std."
    if moment.date() == now.date():
        return f"Heute {moment:%H:%M}"
    if moment.date() == (now - timedelta(days=1)).date():
        return f"Gestern {moment:%H:%M}"
    return f"{moment:%d.%m. %H:%M}"


class NotificationWatcher(QThread):
    """Schaut alle paar Sekunden, ob sich die Datenbank geändert hat, und liest sie nur dann neu."""
    updated = Signal()
    INTERVAL_MS = 3000

    def __init__(self, parent=None, database: Path = DATABASE):
        super().__init__(parent)
        self.database = database
        self.notices: list[Notice] = []
        self.available = True
        self._stop = False

    def _signature(self) -> tuple:
        files = (self.database, self.database.with_name(self.database.name + "-wal"))
        return tuple(file.stat().st_mtime_ns if file.exists() else 0 for file in files)

    def run(self) -> None:
        last = None
        while not self._stop:
            try:
                signature = self._signature()
                if signature != last:
                    last = signature
                    self.notices = read_notices(self.database)
                    self.available = True
                    self.updated.emit()
            except (OSError, sqlite3.Error) as exc:
                log.warning("Benachrichtigungen nicht lesbar: %s", exc)
                if self.available:
                    self.available = False
                    self.updated.emit()
            for _ in range(self.INTERVAL_MS // 100):
                if self._stop:
                    return
                self.msleep(100)

    def stop(self) -> None:
        self._stop = True
        self.wait(2000)
