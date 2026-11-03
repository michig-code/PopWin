"""Klickt den Tray-Pfeil bei ausgeblendeter Windows-Taskleiste - die Situation im Alltag.

Start: python tests/tray_check.py <Bild.png>
"""
import faulthandler
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from core.monitor_manager import native_rect
from ui.topbar import TopBar

errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def steps(ctx, app):
    bar = next(b for b in ctx.bars if isinstance(b, TopBar) and ctx.monitors.is_primary(b.bar_screen))
    check("Windows-Taskleiste ausgeblendet", not win_api.native_taskbar_visible())
    bar.chevron.click()
    yield 1500
    hwnd = win_api.tray_overflow_window()
    check("Überlauf ist offen", hwnd != 0)
    left, top, right, bottom = win_api.window_rect(hwnd)
    corner = bar.chevron.mapToGlobal(bar.chevron.rect().bottomRight())
    check("Direkt unter dem Pfeil", 0 <= top - corner.y() <= 16, f"Pfeil y={corner.y()}, Fenster y={top}")
    check("Rechtsbündig am Pfeil", abs(right - corner.x()) <= 4, f"Pfeil x={corner.x()}, Fenster rechts={right}")
    check("Komplett auf dem Monitor", left >= native_rect(bar.bar_screen)[0])
    screen = bar.bar_screen
    screen.grabWindow(0, max(0, left - 300), 0, right - left + 340, bottom + 12).save(sys.argv[1])

    before = len(win_api.list_windows())
    bar.chevron.click()
    yield 900
    check("Zweiter Klick schließt ihn", not win_api.tray_overflow_window())
    check("Keine App wurde dabei gestartet", len(win_api.list_windows()) <= before)

    bar.chevron.click()
    yield 1200
    check("Dritter Klick öffnet wieder", win_api.tray_overflow_window() != 0)
    win_api.close_tray_overflow()
    yield 500
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(50, exit=True)
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["bar_screens"]["topbar"] = {"mode": "primary", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "primary", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    previous = win_api.foreground_window()
    sequence = steps(ctx, app)

    def advance():
        try:
            QTimer.singleShot(next(sequence), advance)
        except StopIteration:
            pass
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()

    try:
        ctx.start()
        QTimer.singleShot(2500, advance)
        app.exec()
    finally:
        ctx.shutdown()
    if previous:
        win_api.activate_window(previous)
    print("Taskleiste danach sichtbar:", win_api.native_taskbar_visible())
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:4]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
