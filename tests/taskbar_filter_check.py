"""Legt ein echtes Fenster auf einen Nebenmonitor und prüft, welche Docks es zeigen.

Beim Maximieren springt der Fokus kurz auf das Testfenster und danach zurück.
Start: python tests/taskbar_filter_check.py
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from core.monitor_manager import native_rect
from ui.taskbar import Dock

TITLE = "PopWinFensterTest"
CHILD = f"""
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
geo = QGuiApplication.screens()[int(sys.argv[1])].availableGeometry()
w = QWidget()
w.setWindowTitle("{TITLE}")
w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
w.setGeometry(geo.x() + 100, geo.y() + 100, 400, 300)
w.show()
QTimer.singleShot(40000, app.quit)
app.exec()
"""
failed = False


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def main() -> int:
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["bar_screens"]["topbar"] = {"mode": "custom", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    ctx.rebuild_bars()
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    index = next(i for i, s in enumerate(screens) if s != primary)
    target = screens[index]
    third = next(s for s in screens if s not in (primary, target))
    docks = {b.bar_screen: b for b in ctx.bars if isinstance(b, Dock)}

    def shown_on(screen) -> bool:
        ctx._windows_time = 0
        return any(w.title == TITLE for w in docks[screen]._windows_here())

    previous = win_api.foreground_window()
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(index)])
    try:
        hwnd = 0
        limit = time.monotonic() + 20
        while not hwnd and time.monotonic() < limit:
            app.processEvents()
            hwnd = next((w.hwnd for w in win_api.list_windows() if w.title == TITLE), 0)
            time.sleep(0.1)
        check("Testfenster gefunden", hwnd != 0)

        def test_button():
            ctx._windows_time = 0
            docks[target].refresh(force=True)
            return next(b for b in docks[target]._buttons.values() if any(w.title == TITLE for w in b.windows))

        def expect(state: str) -> None:
            app.processEvents()
            check(f"{state}: richtiger Monitor erkannt", win_api.window_monitor(hwnd) == native_rect(target))
            check(f"{state}: Hauptmonitor zeigt es", shown_on(primary))
            check(f"{state}: eigener Nebenmonitor zeigt es", shown_on(target))
            check(f"{state}: fremder Nebenmonitor zeigt es nicht", not shown_on(third))

        expect("Normal")
        win_api.user32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE
        time.sleep(0.8)
        check("Fenster ist minimiert", win_api.user32.IsIconic(hwnd))
        expect("Minimiert")
        check("Minimiert: Indikator zeigt 'nicht sichtbar'", test_button().property("shown") is False)
        win_api.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        time.sleep(0.8)
        check("Fenster ist maximiert", win_api.user32.IsZoomed(hwnd))
        expect("Maximiert")
        check("Maximiert: Indikator zeigt 'sichtbar'", test_button().property("shown") is True)
        win_api.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE, aus dem maximierten Zustand
        time.sleep(0.8)
        expect("Maximiert, dann minimiert")

        config.data["taskbar"]["window_filter"] = "own"
        check("Einstellung 'nur eigene': Hauptmonitor zeigt es nicht", not shown_on(primary))
        config.data["taskbar"]["window_filter"] = "all"
        check("Einstellung 'alle': fremder Monitor zeigt es", shown_on(third))
        config.data["taskbar"]["window_filter"] = "smart"
        docks[target].refresh(force=True)
        button = next(b for b in docks[target]._buttons.values()
                      if any(w.title == TITLE for w in b.windows))
        check("Taskbar-Knopf hält das Fenster", button.property("running") is True)
    finally:
        child.kill()
        if previous:
            win_api.activate_window(previous)
    ctx.shutdown()
    print("BESTANDEN" if not failed else "FEHLGESCHLAGEN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
