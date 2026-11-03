"""Ausführung von KI-Aufgaben: Risikoprüfung, Hintergrundlauf, PopWin-Einstellungen."""
from __future__ import annotations

import re
import subprocess
import time

from PySide6.QtCore import QThread, Signal

from config.config_manager import DEFAULTS, MONITOR_DEFAULTS
from core import win_api

TIMEOUT_SECONDS = 300
# Windows hat keinen eingebauten Maßstab dafür; im Modellvergleich stufte nur eines von fünf das Stoppen
# eines Dienstes als riskant ein. Die Entscheidung liegt deshalb hier und nicht bei der KI.
_RISKY = re.compile(
    r"\b(remove-item|rm|del|erase|rd|rmdir|format|format-volume|clear-disk|diskpart|bcdedit|vssadmin|cipher|"
    r"stop-service|stop-process|taskkill|shutdown|stop-computer|restart-computer|set-executionpolicy|"
    r"invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer|invoke-expression|iex|"
    r"takeown|icacls|cacls|attrib|net\s+user|sc\s+delete|reg\s+(add|delete)|set-itemproperty|remove-itemproperty|"
    r"new-itemproperty|clear-recyclebin|runas)\b|-verb\s+runas|-enc(odedcommand)?\b|\|\s*(iex|cmd|powershell)\b",
    re.I)
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(password|passwort|pwd|token|api[_-]?key|secret)(\s*[=:]\s*)\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
)
HIDDEN_SETTINGS = ("first_run", "shell", "monitors", "monitor_shared", "widgets", "bar_screens", "taskbar.pinned",
                   "taskbar.order", "taskbar.screen_pins", "taskbar.windows_pins_imported", "notifications.hidden", "extensions", "nova",
                   "start_menu.pinned", "launcher.search_models", "launcher.repair_models")


def is_risky(task: dict) -> bool:
    return task["kind"] in ("powershell", "cmd") and bool(_RISKY.search(task["command"]))


def redact(text: str) -> str:
    """Entfernt Keys und Passwörter, bevor Text angezeigt oder an die KI geschickt wird."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[entfernt]" if m.lastindex == 2 else "[entfernt]", text)
    return text


def _scalar_paths(node: dict, prefix: str = "") -> dict[str, object]:
    paths = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if any(path == hidden or path.startswith(f"{hidden}.") for hidden in HIDDEN_SETTINGS):
            continue
        if isinstance(value, dict):
            paths.update(_scalar_paths(value, f"{path}."))
        elif isinstance(value, (bool, int, float, str)):
            paths[path] = value
    return paths


def setting_paths() -> dict[str, object]:
    paths = _scalar_paths(DEFAULTS)
    paths.update({f"monitor.{key}": value for key, value in MONITOR_DEFAULTS.items()})
    return paths


def settings_catalog(ctx) -> str:
    """Kurze Liste für die KI: Schlüssel=aktueller Wert. Knapp halten, sie geht bei jeder Anfrage mit."""
    profile = ctx.config.monitor()
    lines = []
    for path in setting_paths():
        value = profile[path[8:]] if path.startswith("monitor.") else ctx.config.get(path)
        lines.append(f"{path}={value}")
    return "\n".join(lines)


def _coerce(default, value):
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("true", "1", "ja", "an")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return str(value)


def apply_setting(ctx, path: str, value) -> str:
    """Setzt eine erlaubte Einstellung; gibt einen Fehlertext zurück oder einen leeren Text."""
    known = setting_paths()
    if path not in known:
        return f"Unbekannte Einstellung: {path}"
    try:
        value = _coerce(known[path], value)
    except (TypeError, ValueError):
        return f"Ungültiger Wert für {path}: {value}"
    if path == "theme":
        ctx.theme.set_theme(value)
    elif path == "mode":
        ctx.set_dark_mode(value)
    elif path.startswith("monitor."):
        ctx.config.set_monitor(None, path[8:], value)
    else:
        ctx.config.set(path, value)
    return ""


class TaskRunner(QThread):
    """Führt genau einen Befehl aus und meldet erst, wenn er wirklich fertig ist."""
    finished_task = Signal(bool, str)

    def __init__(self, task: dict, parent=None):
        super().__init__(parent)
        self.task = task
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        task = self.task
        if task["kind"] == "uri":
            ok = win_api.shell_open(task["command"])
            self.finished_task.emit(ok, "" if ok else f"Konnte nicht geöffnet werden: {task['command']}")
            return
        command = (["powershell", "-NoProfile", "-NonInteractive", "-Command", task["command"]]
                   if task["kind"] == "powershell" else ["cmd", "/c", task["command"]])
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            self.finished_task.emit(False, str(exc))
            return
        end = time.monotonic() + TIMEOUT_SECONDS
        while process.poll() is None:
            if self._cancel or time.monotonic() > end:
                process.kill()
                reason = "Abgebrochen." if self._cancel else f"Nach {TIMEOUT_SECONDS} Sekunden abgebrochen."
                self.finished_task.emit(False, reason)
                return
            time.sleep(0.1)
        output = process.stdout.read().decode("cp850", "replace").strip()
        self.finished_task.emit(process.returncode == 0, output[-4000:])
