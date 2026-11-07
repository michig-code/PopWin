"""Windows entlasten: Hintergrund-Programme von Windows, die PopWin ersetzt oder die viele nicht brauchen,
werden beendet, solange PopWin läuft. Jedes einzeln einschaltbar, ab Werk ist alles aus.

Nur genau diese Programmnamen und nur in der eigenen Windows-Sitzung. Windows startet sie bei Bedarf selbst
wieder (z. B. Win+W für die Widgets); PopWin schaut deshalb jede Minute erneut nach."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)
CHECK_MS = 60_000


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    processes: tuple[str, ...]
    hint: str


CANDIDATES = (
    Candidate("widgets", "Windows-Widgets", ("widgets.exe", "widgetservice.exe"),
              "Die Widget-Leiste von Windows (Win+W). PopWin hat eigene Widgets."),
    Candidate("search", "Oberfläche der Windows-Suche", ("searchhost.exe",),
              "Nur das Suchfenster, nicht der Datei-Index. PopWin hat eine eigene Suche."),
    Candidate("gamebar", "Xbox Game Bar im Hintergrund", ("gamebar.exe", "gamebarftserver.exe"),
              "Win+G startet sie bei Bedarf wieder. Aus lassen, wenn du damit aufnimmst."),
    Candidate("phone", "Smartphone-Link im Hintergrund", ("phoneexperiencehost.exe",),
              "Nur einschalten, wenn du dein Handy nicht mit dem PC verbindest."),
    Candidate("copilot", "Copilot im Hintergrund", ("copilot.exe",),
              "Die Copilot-App von Microsoft. Nova bleibt davon unberührt."),
)
# Was PopWin nie anfasst, egal was in den Einstellungen steht
PROTECTED = {"explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "svchost.exe", "lsass.exe", "services.exe",
             "searchindexer.exe", "python.exe", "pythonw.exe", "popwin.exe"}


def wanted_names(enabled: dict) -> set[str]:
    """Programmnamen, die laut Einstellungen beendet werden sollen; Geschütztes fällt immer heraus."""
    names = {name for candidate in CANDIDATES if enabled.get(candidate.key) for name in candidate.processes}
    return names - PROTECTED


def matching(processes, names: set[str], session: int, own_pid: int) -> list:
    """Laufende Prozesse mit genau diesen Namen in der eigenen Sitzung, nie PopWin selbst."""
    found = []
    for process in processes:
        try:
            info = process.info
            if process.pid == own_pid or (info.get("name") or "").lower() not in names:
                continue
            if info.get("session", session) != session:
                continue
            found.append(process)
        except Exception:  # Prozess ist inzwischen weg
            continue
    return found


def _session_of(pid: int) -> int:
    import ctypes
    session = ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session))
    return session.value


def trim(enabled: dict) -> list[str]:
    """Beendet die gewählten Programme einmal; gibt zurück, was beendet wurde."""
    import psutil
    names = wanted_names(enabled)
    if not names:
        return []
    session = _session_of(os.getpid())
    processes = []
    for process in psutil.process_iter(["name"]):
        try:
            process.info["session"] = _session_of(process.pid) if (process.info.get("name") or "").lower() in names else session
        except Exception:
            continue
        processes.append(process)
    stopped = []
    for process in matching(processes, names, session, os.getpid()):
        try:
            process.terminate()
            stopped.append(process.info.get("name", ""))
        except Exception as exc:
            log.debug("Nicht beendet: %s", exc)
    if stopped:
        log.info("Windows entlastet: %s", ", ".join(stopped))
    return stopped


class ProcessTrimmer(QObject):
    """Schaut jede Minute nach, aber nur, solange mindestens ein Haken gesetzt ist."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.timer = QTimer(self, interval=CHECK_MS)
        self.timer.timeout.connect(self.run)
        config.changed.connect(lambda key: key.startswith("windows_trim") and self.sync())

    def enabled(self) -> dict:
        return self.config.get("windows_trim") or {}

    def sync(self) -> None:
        if any(self.enabled().values()):
            self.timer.start()
            QTimer.singleShot(2000, self.run)
        else:
            self.timer.stop()

    def run(self) -> list[str]:
        return trim(self.enabled())
