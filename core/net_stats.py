"""Speedtest, sein Verlauf und was PopWin selbst an Arbeitsspeicher braucht. Gemessen wird nur auf Wunsch oder wenn man es einschaltet."""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime

import psutil
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from core.paths import DATA_DIR, FROZEN, ROOT

log = logging.getLogger(__name__)

HISTORY_FILE = "network_stats.json"
MAX_ENTRIES = 500
SERVER = "speed.cloudflare.com"
DOWN_URL = f"https://{SERVER}/__down?bytes=50000000"
UP_URL = f"https://{SERVER}/__up"
UP_BYTES = 8_000_000
CHUNK = 64 * 1024
DOWN_SECONDS = 8
TIMEOUT = 15
HOUR_MS = 3_600_000
PERFORMANCE_POINTS = 720  # bei 5 Sekunden Abstand die letzte Stunde
APP_NAMES = {"": "PopWin", "explorer": "Explorer", "editor": "Editor", "terminal": "Terminal", "studio": "PowerShell Studio",
             "stream": "Stream", "wallpaper": "Hintergrund", "watchdog": "Wächter"}


def megabits(size: int, seconds: float) -> float:
    return round(size * 8 / max(seconds, 0.001) / 1_000_000, 1)


def measure_ping() -> float:
    start = time.perf_counter()
    socket.create_connection((SERVER, 443), timeout=TIMEOUT).close()
    return round((time.perf_counter() - start) * 1000, 1)


def measure_down() -> float:
    request = urllib.request.Request(DOWN_URL, headers={"User-Agent": "PopWin"})
    size = 0
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        while time.perf_counter() - start < DOWN_SECONDS:
            chunk = answer.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
    return megabits(size, time.perf_counter() - start)


def measure_up() -> float:
    request = urllib.request.Request(UP_URL, data=bytes(UP_BYTES), method="POST", headers={"User-Agent": "PopWin"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        answer.read()
    return megabits(UP_BYTES, time.perf_counter() - start)


class SpeedTest(QThread):
    stage = Signal(str)
    finished_with = Signal(dict, str)

    def run(self) -> None:
        try:
            self.stage.emit("Ping …")
            ping = measure_ping()
            self.stage.emit("Download …")
            down = measure_down()
            self.stage.emit("Upload …")
            up = measure_up()
        except (OSError, urllib.error.URLError) as exc:
            self.finished_with.emit({}, str(exc))
            return
        self.finished_with.emit({"time": datetime.now().isoformat(timespec="seconds"), "down": down, "up": up, "ping": ping}, "")


class SpeedHistory(QObject):
    """Bleibt nur auf diesem PC. Der Timer misst nur, wenn man das stündliche Messen einschaltet."""
    changed = Signal()
    stage = Signal(str)
    failed = Signal(str)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.path = DATA_DIR / HISTORY_FILE
        self.job: SpeedTest | None = None
        self.entries: list[dict] = self._read()
        self.timer = QTimer(self, interval=HOUR_MS)
        self.timer.timeout.connect(self._hourly)
        self.timer.start()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [entry for entry in data if isinstance(entry, dict) and "down" in entry] if isinstance(data, list) else []

    def _hourly(self) -> None:
        if self.ctx.config.get("network.hourly_test"):
            self.run()

    def run(self) -> None:
        if self.job is not None:
            return
        self.job = SpeedTest(self)
        self.job.stage.connect(self.stage)
        self.job.finished_with.connect(self._done)
        self.job.start()

    def _done(self, entry: dict, error: str) -> None:
        self.job = None
        if error:
            self.failed.emit(error)
            return
        self.entries = (self.entries + [entry])[-MAX_ENTRIES:]
        self._write()
        self.changed.emit()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries), encoding="utf-8")

    def reset(self) -> None:
        self.entries = []
        self._write()
        self.changed.emit()


def network_name() -> str:
    """Der Name, den Windows dem Netz gibt. Dauert eine Sekunde, also nur aus einem Hintergrund-Thread aufrufen."""
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8;(Get-NetConnectionProfile | Select-Object -First 1).Name"],
                                capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


class NameLoader(QThread):
    loaded = Signal(str)

    def run(self) -> None:
        self.loaded.emit(network_name())


def popwin_part(arguments: list[str], root: str = str(ROOT).lower()) -> str | None:
    """Welcher Teil von PopWin das ist; None, wenn der Prozess nichts mit PopWin zu tun hat."""
    text = " ".join(arguments).lower()
    if FROZEN:
        ours = bool(arguments) and arguments[0].lower().startswith(root)
    else:
        ours = root in text and "unittest" not in text
    if not ours:
        return None
    if "--app" in arguments:
        index = arguments.index("--app") + 1
        return arguments[index] if index < len(arguments) else ""
    for name in APP_NAMES:
        if name and any(os.path.normcase(os.path.join(root, name)) in part.lower() for part in arguments):
            return name
    return ""


OUR_EXES = ("pythonw.exe", "python.exe", "popwin.exe")
_parts: dict[int, str | None] = {}  # PID -> Teil von PopWin; jede Befehlszeile wird nur einmal gelesen


def _part_of(pid: int) -> str | None:
    if pid not in _parts:
        try:
            _parts[pid] = popwin_part(psutil.Process(pid).cmdline())
        except psutil.Error:
            _parts[pid] = None
    return _parts[pid]


def popwin_memory(processes: list[tuple[int, str, int, int]]) -> dict[str, float]:
    """Megabyte je Teil von PopWin. Nimmt die fertige Prozessliste: psutil für jeden Prozess einzeln kostete bei 450 Prozessen
    2,5 Sekunden und hielt dabei Python fest. Der Maus-Hook wartete mit, und der Mauszeiger ruckelte."""
    used: dict[str, float] = {}
    alive = set()
    for pid, name, _cpu, memory in processes:
        if name.lower() not in OUR_EXES:
            continue
        alive.add(pid)
        part = _part_of(pid)
        if part is not None:
            label = APP_NAMES.get(part, part)
            used[label] = round(used.get(label, 0.0) + memory / 1024 ** 2, 1)
    for pid in [pid for pid in _parts if pid not in alive]:
        del _parts[pid]
    return used


class PerformanceLog:
    def __init__(self):
        self.points: deque[float] = deque(maxlen=PERFORMANCE_POINTS)
        self.parts: dict[str, float] = {}

    def sample(self, processes: list[tuple[int, str, int, int]]) -> None:
        self.parts = popwin_memory(processes)
        self.points.append(round(sum(self.parts.values()), 1))
