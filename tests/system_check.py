"""Prüft die Eingriffe ins System mit Testdaten und stellt jeden Zustand wieder her.

Nicht enthalten, weil nicht rückgängig zu machen: Papierkorb leeren, Sortieren der Desktop-Symbole.
Start: python tests/system_check.py <Ausgabeordner>
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import win_api
from core.shell_hooks import parse_hotkey

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def recycle_bin() -> None:
    before = win_api.recycle_bin_info()[0]
    folder = Path(tempfile.mkdtemp())
    target = folder / "popwin_test_datei.txt"
    target.write_text("PopWin Test", encoding="utf-8")
    ok = win_api.move_to_recycle_bin([str(target)])
    time.sleep(0.5)
    check("Datei landet im Papierkorb", ok and not target.exists() and win_api.recycle_bin_info()[0] == before + 1,
          f"{before} -> {win_api.recycle_bin_info()[0]}")
    check("Fehlende Datei wird abgelehnt", win_api.move_to_recycle_bin([str(folder / "gibt_es_nicht")]) is False)


def dark_mode_roundtrip() -> None:
    mode = win_api.system_mode()
    win_api.set_system_mode(mode)  # schreibt denselben Wert, die Darstellung ändert sich nicht
    check("Darkmode-Registry beschreibbar", win_api.system_mode() == mode, mode)


def autostart() -> None:
    was = win_api.autostart_enabled()
    script = str(ROOT / "main.py")
    win_api.set_autostart(True, sys.executable, script)
    made = win_api.autostart_enabled()
    win_api.set_autostart(was, sys.executable, script)
    check("Autostart lässt sich setzen und entfernen", made and win_api.autostart_enabled() == was)


def desktop_icons() -> None:
    check("Desktop-Ansicht gefunden", win_api.desktop_view() != 0, hex(win_api.desktop_view()))
    check("Desktop-Fenster erkannt", win_api.is_desktop_window(win_api.desktop_view()))
    check("Fremdes Fenster abgelehnt", not win_api.is_desktop_window(win_api.user32.FindWindowW("Shell_TrayWnd", None)))


def hotkey_table() -> None:
    check("Kill-Hotkey ist gültig", parse_hotkey("ctrl+alt+shift+k") is not None)
    mods, key = parse_hotkey("ctrl+alt+shift+k")
    free = win_api.user32.RegisterHotKey(None, 900, mods, key)
    if free:
        win_api.user32.UnregisterHotKey(None, 900)
    check("Kill-Hotkey ist frei", free)


def taskbar_recovery() -> None:
    """Simuliert den Ernstfall: PopWin stirbt, während die Taskleiste versteckt ist."""
    settings = ROOT / "config" / "settings.json"
    existed = settings.exists()
    backup = settings.read_text(encoding="utf-8") if existed else None
    state = win_api.taskbar_state()
    data = json.loads(backup) if backup else {}
    data["shell"] = {"hide_native_taskbar": True, "saved_taskbar_state": state}
    settings.write_text(json.dumps(data), encoding="utf-8")
    dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    watchdog = subprocess.Popen([sys.executable, str(ROOT / "core" / "taskbar_watchdog.py"), str(dummy.pid)])
    try:
        win_api.hide_native_taskbar()
        for _ in range(4):  # wie der Wächter-Timer im Programm
            time.sleep(0.5)
            win_api.reinforce_taskbar_hidden()
        time.sleep(0.5)
        check("Taskleiste wird ausgeblendet (alle Monitore)", not win_api.native_taskbar_visible())
        dummy.kill()  # harter Absturz
        watchdog.wait(10)
        time.sleep(0.5)
        check("Wächter holt sie nach Absturz zurück", win_api.native_taskbar_visible())
        check("Ursprünglicher Taskleisten-Zustand wiederhergestellt", win_api.taskbar_state() == state,
              f"{state} -> {win_api.taskbar_state()}")
    finally:
        win_api.show_native_taskbar(state)
        if dummy.poll() is None:
            dummy.kill()
        if existed:
            settings.write_text(backup, encoding="utf-8")
        else:
            settings.unlink(missing_ok=True)


def main() -> int:
    recycle_bin()
    dark_mode_roundtrip()
    autostart()
    desktop_icons()
    hotkey_table()
    taskbar_recovery()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} von {len(results)} bestanden")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
