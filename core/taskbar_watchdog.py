"""Eigenständiger Wächter: stellt die Windows-Taskleiste wieder her, sobald PopWin endet -
auch nach einem Absturz oder wenn der Prozess im Task-Manager beendet wurde.

Aufruf: taskbar_watchdog.py <pid>      (wartet auf den Prozess)
        taskbar_watchdog.py --restore  (stellt sofort wieder her)
"""
import ctypes
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import win_api  # noqa: E402
from core.paths import DATA_DIR, LOG_DIR  # noqa: E402

SETTINGS = DATA_DIR / "settings.json"
PID_FILE = LOG_DIR / "popwin.pid"
SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
RETRY_BACKOFF_S = (0.05, 0.1, 0.15, 0.2)
RESTART_WAIT = 20.0  # so lange darf ein neu gestartetes PopWin brauchen, bis es seine Kennung schreibt

win_api.kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
win_api.kernel32.WaitForSingleObject.restype = ctypes.c_uint32


def _settings_file() -> Path:
    """War settings.json beim Start gesperrt, schreibt PopWin in die Ausweichdatei - dann gilt die, wenn sie neuer ist."""
    session = SETTINGS.with_name(f"{SETTINGS.stem}.session.json")
    try:
        if session.stat().st_mtime >= SETTINGS.stat().st_mtime:
            return session
    except FileNotFoundError:
        if session.exists():
            return session
    except OSError:
        pass
    return SETTINGS


def _alive(pid: int) -> bool:
    handle = win_api.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return win_api.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0  # 0 = schon beendet
    finally:
        win_api.kernel32.CloseHandle(ctypes.c_void_p(handle))


def successor_running(old_pid: int) -> bool:
    """Läuft schon ein neues PopWin? Dann ist es ein Neustart, und das neue kümmert sich um die Taskleiste."""
    try:
        pid = int(PID_FILE.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    return pid != old_pid and _alive(pid)


def wait_for_successor(old_pid: int, wait: float = RESTART_WAIT) -> bool:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if successor_running(old_pid):
            return True
        time.sleep(0.5)
    return False


def _saved_state():
    """Liest den gemerkten Taskleisten-Zustand; bei einer kurz gesperrten Datei (OneDrive) mit kurzen Wiederholungen."""
    source = _settings_file()
    for attempt in range(len(RETRY_BACKOFF_S) + 1):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                shell = data.get("shell")
                if isinstance(shell, dict):
                    return shell.get("saved_taskbar_state")
            return None
        except ValueError:
            return None
        except OSError:
            if attempt < len(RETRY_BACKOFF_S):
                time.sleep(RETRY_BACKOFF_S[attempt])
    return None


def restore() -> None:
    saved = _saved_state()
    # Beim sauberen Beenden hat PopWin den Zustand schon zurückgesetzt und gelöscht.
    if saved is not None or "--restore" in sys.argv:
        win_api.show_native_taskbar(saved)


def main() -> None:
    if sys.argv[1:] and sys.argv[1] != "--restore":
        old_pid = int(sys.argv[1])
        handle = win_api.kernel32.OpenProcess(SYNCHRONIZE, False, old_pid)
        if handle:
            try:
                win_api.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), INFINITE)
            finally:
                win_api.kernel32.CloseHandle(ctypes.c_void_p(handle))
        if _saved_state() is not None and wait_for_successor(old_pid):
            return  # Neustart: Die Leiste bleibt versteckt, ein Spiel im Vollbild wird nicht verschoben
    restore()


if __name__ == "__main__":
    main()
