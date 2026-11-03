"""Eigenständiger Wächter: stellt die Windows-Taskleiste wieder her, sobald PopWin endet -
auch nach einem Absturz oder wenn der Prozess im Task-Manager beendet wurde.

Aufruf: taskbar_watchdog.py <pid>      (wartet auf den Prozess)
        taskbar_watchdog.py --restore  (stellt sofort wieder her)
"""
import ctypes
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import win_api  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402

SETTINGS = DATA_DIR / "settings.json"
SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF


def restore() -> None:
    saved = None
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8")).get("shell", {}).get("saved_taskbar_state")
    except (OSError, ValueError):
        pass
    # Beim sauberen Beenden hat PopWin den Zustand schon zurückgesetzt und gelöscht.
    if saved is not None or "--restore" in sys.argv:
        win_api.show_native_taskbar(saved)


def main() -> None:
    if sys.argv[1:] and sys.argv[1] != "--restore":
        handle = win_api.kernel32.OpenProcess(SYNCHRONIZE, False, int(sys.argv[1]))
        if handle:
            win_api.kernel32.WaitForSingleObject(ctypes.c_void_p(handle), INFINITE)
    restore()


if __name__ == "__main__":
    main()
