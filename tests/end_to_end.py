"""Startet das echte Programm wie die .bat und prüft Taskleiste, Kill-Hotkey und Absturz.

Start: python tests/end_to_end.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import win_api

VK_SHIFT, VK_MENU, VK_K = 0x10, 0x12, 0x4B
STARTUP_SECONDS = 14
settings = ROOT / "config" / "settings.json"
existed = settings.exists()
backup = settings.read_text(encoding="utf-8") if existed else None
pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
state_before = win_api.taskbar_state()
failed = False


def check(name: str, ok: bool) -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, flush=True)


def stays_hidden(seconds: float = 3.0) -> bool:
    """Muss über die ganze Zeit weg sein - ein einzelner Blick übersieht Flackern."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if win_api.native_taskbar_visible():
            return False
        time.sleep(0.05)
    return True


first = subprocess.Popen([pythonw, str(ROOT / "main.py")], cwd=ROOT)
crash = None
try:
    time.sleep(STARTUP_SECONDS)
    check("Programm läuft", first.poll() is None)
    check("Windows-Taskleiste bleibt ausgeblendet", stays_hidden())
    second = subprocess.Popen([pythonw, str(ROOT / "main.py")], cwd=ROOT)
    second.wait(15)
    check("Zweite Instanz beendet sich selbst", second.returncode == 0)
    win_api.send_keys(win_api.VK_CONTROL, VK_MENU, VK_SHIFT, VK_K)
    first.wait(20)
    check("Kill-Hotkey beendet das Programm", first.returncode == 0)
    time.sleep(0.5)
    check("Taskleiste nach dem Beenden zurück", win_api.native_taskbar_visible())
    check("Taskleisten-Zustand wie vorher", win_api.taskbar_state() == state_before)

    crash = subprocess.Popen([pythonw, str(ROOT / "main.py")], cwd=ROOT)
    time.sleep(STARTUP_SECONDS)
    check("Nach Neustart wieder ausgeblendet", stays_hidden())
    crash.kill()  # wie "Task beenden" im Task-Manager
    time.sleep(3)
    check("Wächter holt sie nach hartem Abbruch zurück", win_api.native_taskbar_visible())
    check("Zustand auch dann wie vorher", win_api.taskbar_state() == state_before)
finally:
    for process in (first, crash):
        if process and process.poll() is None:
            process.kill()
            print("MUSSTE HART BEENDET WERDEN")
    win_api.show_native_taskbar(state_before)
    (ROOT / "popwin.pid").unlink(missing_ok=True)
    if existed:
        settings.write_text(backup, encoding="utf-8")
    else:
        settings.unlink(missing_ok=True)
print("BESTANDEN" if not failed else "FEHLGESCHLAGEN")
sys.exit(1 if failed else 0)
