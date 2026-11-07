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
RESTART_WAIT = 20.0  # so lange darf ein neu gestartetes PopWin brauchen, bis es seine Kennung schreibt


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
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get("shell", {}).get("saved_taskbar_state")
    except (OSError, ValueError):
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
            win_api.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), INFINITE)
        if _saved_state() is not None and wait_for_successor(old_pid):
            return  # Neustart: Die Leiste bleibt versteckt, ein Spiel im Vollbild wird nicht verschoben
    restore()


if __name__ == "__main__":
    main()
