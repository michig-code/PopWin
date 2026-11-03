"""Live-Prüfung der neuen Funktionen an der echten Oberfläche, mit Wegwerf-Konfiguration.

Start: python tests/features_check.py <Ausgabeordner>
"""
import faulthandler
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from core.monitor_manager import screen_key
from ui.task_panel import STATUS
from ui.taskbar import Dock
from ui.topbar import TopBar
from ui.window_frame import HTCAPTION, HTMAXBUTTON

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
TITLE = "PopWinFeatureTest"
CHILD = f"""
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])
geo = QGuiApplication.screens()[int(sys.argv[1])].availableGeometry()
w = QLabel("Testfenster fuer die Vorschau")
w.setStyleSheet("background: #204060; color: white; font-size: 30px;")
w.setWindowTitle("{TITLE}")
w.setGeometry(geo.x() + 80, geo.y() + 80, 500, 320)
w.show()
QTimer.singleShot(50000, app.quit)
app.exec()
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def shot(window, name: str) -> None:
    rect = window.frameGeometry()
    screen = window.screen()
    origin = screen.geometry().topLeft()
    screen.grabWindow(0, rect.x() - origin.x(), rect.y() - origin.y(), rect.width(), rect.height()).save(
        str(out / f"{name}.png"))


def steps(ctx, app):
    config = ctx.config
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    second = next(s for s in screens if s != primary)
    docks = {b.bar_screen: b for b in ctx.bars if isinstance(b, Dock)}
    dock, other = docks[primary], docks[second]
    top = next(b for b in ctx.bars if isinstance(b, TopBar))
    index = screens.index(second)
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(index)])
    yield 2500
    ctx._windows_time = 0
    other.refresh(force=True)
    button = next(b for b in other._buttons.values() if any(w.title == TITLE for w in b.windows))
    try:
        # Reihenfolge bleibt beim Fokuswechsel
        ctx._windows_time = 0
        dock.refresh()
        before = list(dock._buttons)
        win_api.activate_window(button.windows[0].hwnd)
        yield 600
        ctx._windows_time = 0
        dock.refresh()
        check("Reihenfolge bleibt beim Fensterwechsel", list(dock._buttons) == before)

        # Verschieben
        keys = list(dock._buttons)
        first = dock._buttons[keys[0]]
        last = dock._buttons[keys[-1]]
        dock.drag_task(first, last.mapToGlobal(last.rect().center()).x() + 30)
        dock.save_order()
        yield 200
        ctx._windows_time = 0
        dock.refresh(force=True)
        check("Task frei verschoben", list(dock._buttons)[-1] == keys[0], str(list(dock._buttons)[:3]))
        check("Reihenfolge gespeichert", config.get("taskbar.order")[dock.screen_id][-1] == keys[0]
              or keys[0] in config.get("taskbar.order")[dock.screen_id])

        # Vorschau
        preview = ctx.task_preview
        preview.hover(button)
        yield 1200
        check("Vorschau erscheint", preview.isVisible(), preview.name.text())
        check("Live-Bild registriert", len(preview._thumbnails) == 1)
        check("Task-beenden-Knopf da", preview.kill.isVisible())
        shot(preview, "preview")
        minimized = button.windows[0].hwnd
        preview.dismiss()
        yield 500
        check("Vorschau schließt weich", not preview.isVisible())
        win_api.user32.ShowWindow(minimized, 7)
        yield 500
        preview.hover(button)
        yield 1000
        shot(preview, "preview_min")
        check("Vorschau auch minimiert", preview.isVisible() and len(preview._thumbnails) == 1)
        preview.dismiss()
        yield 400
        win_api.user32.ShowWindow(minimized, 9)
        yield 400

        # Pins pro Monitor
        config.set("taskbar.pin_scope", "primary")
        yield 500
        check("Pins nur auf dem Hauptmonitor", not other.is_pinned("explorer.exe") and dock.is_pinned("explorer.exe"))
        other.pin_paths([sys.executable])
        yield 500
        check("Eigener Pin auf dem Nebenmonitor", other.is_pinned(Path(sys.executable).name.lower()))
        check("erscheint nicht auf dem Hauptmonitor", not dock.is_pinned(Path(sys.executable).name.lower()))
        config.set("taskbar.pin_scope", "all")
        yield 400

        # Fenster-Menü
        top.user.click()
        yield 700
        panel = top._window_popup
        check("Fenster-Menü öffnet", panel is not None and panel.isVisible(), f"{len(panel.rows)} Fenster")
        shot(panel, "window_panel")
        panel.search.setText(TITLE[:10])
        yield 300
        check("Fenster-Suche filtert", len(panel.rows) >= 1 and all(TITLE in r.window.title or "python" in r.window.exe.lower() for r in panel.rows))
        panel.dismiss()
        yield 500
        check("Fenster-Menü schließt weich", not panel.isVisible())

        # Live-Aktionen
        launcher = ctx.launcher
        ctx.show_launcher()
        yield 500
        print("Situation:", [launcher.results.item(i).text() for i in range(launcher.results.count())])
        launcher.input.setText("blue")
        yield 200
        labels = [launcher.results.item(i).text() for i in range(launcher.results.count())]
        check("Live-Aktion beim Tippen", any("Bluetooth" in t for t in labels), str(labels[:2]))
        launcher.input.setText("fok")
        yield 150
        check("Fokus-Aktion", any("Fokus" in launcher.results.item(i).text() for i in range(launcher.results.count())))
        shot(launcher, "launcher_live")
        launcher.dismiss()
        yield 500
        check("Suche schließt weich", not launcher.isVisible())

        # Aufgaben: ein Erfolg, ein Fehler, dann Überspringen
        marker = Path(tempfile.gettempdir()) / "popwin_task_test.txt"
        marker.unlink(missing_ok=True)
        tasks = ctx.tasks
        tasks.propose([
            {"summary": "Testdatei schreiben", "kind": "powershell", "command": f'Set-Content "{marker}" "ok"',
             "setting": "", "value": None},
            {"summary": "Fehlender Befehl", "kind": "powershell", "command": "Gibt-EsNicht-Befehl; exit 3",
             "setting": "", "value": None},
            {"summary": "Icon-Größe ändern", "kind": "setting", "command": "", "setting": "monitor.icon_size", "value": 30},
        ])
        yield 700
        check("Aufgabenleiste am Rand", tasks.panel.isVisible() and len(tasks.panel.rows) == 3)
        shot(tasks.panel, "tasks")
        tasks.begin()
        limit = time.monotonic() + 15
        while time.monotonic() < limit and not tasks.error_log.isVisible():
            yield 200
        check("Task läuft erst nach dem vorherigen", marker.exists() and tasks.panel.rows[0].status.text() == STATUS["ok"])
        check("Fehler stoppt die Kette", tasks.error_log.isVisible() and tasks.index == 1
              and tasks.panel.rows[2].status.text() == STATUS["wait"])
        check("Fehlerprotokoll mit Prompt", "Gibt-EsNicht-Befehl" in tasks.error_log.prompt.toPlainText())
        shot(tasks.error_log, "error_log")
        tasks.skip()
        yield 900
        check("Danach geht es weiter", config.monitor()["icon_size"] == 30)
        check("Abschluss-Popup", tasks.done_popup.isVisible(), tasks.done_popup.text.text())
        shot(tasks.done_popup, "done")
        limit = time.monotonic() + 7
        while time.monotonic() < limit and tasks.done_popup.isVisible():
            yield 300
        check("Popup schließt nach 5 Sekunden", not tasks.done_popup.isVisible())
        marker.unlink(missing_ok=True)

        # Riskant: Enter gilt nicht
        answers = []
        risky = {"summary": "Test", "kind": "powershell", "command": "Remove-Item C:\\gibtsnicht", "setting": "", "value": None}
        tasks.confirm.ask(risky, answers.append)
        yield 500
        check("Warnung bei riskantem Befehl", tasks.confirm.warning.isVisible())
        shot(tasks.confirm, "confirm_risky")
        win_api.send_keys(win_api.VK_RETURN)
        yield 500
        check("Enter führt riskanten Befehl nicht aus", answers == [])
        tasks.confirm._answer(False)
        yield 400

        # KI-Plan, falls ein Key übergeben wurde
        key = os.environ.get("POPWIN_TEST_KEY")
        if key:
            config.api_key = lambda user=None: key
            results = {}
            ctx.tasks.propose = lambda found: results.setdefault("tasks", found)
            launcher.open()
            launcher.plan("Öffne die Windows-Einstellungen für Bluetooth")
            limit = time.monotonic() + 25
            while time.monotonic() < limit and "tasks" not in results:
                yield 300
            print("KI-Plan:", results.get("tasks"), launcher.answer.text()[:100])
            check("KI liefert eine Aktion", bool(results.get("tasks")))
            launcher.dismiss()
            yield 400

        # Einstellungen mit eigenem Fenster
        settings = ctx.settings
        ctx.open_settings()
        yield 900
        check("Einstellungen öffnen", settings.isVisible())
        style = win_api.user32.GetWindowLongPtrW(int(settings.winId()), -16)
        check("Bleibt ein echtes Windows-Fenster", bool(style & 0x40000) and bool(style & 0xC00000), hex(style))
        rect = win_api.window_rect(int(settings.winId()))
        title = settings.titlebar
        ratio = settings.devicePixelRatio()
        point = title.mapTo(settings, title.maximize.geometry().center())
        lparam = lambda x, y: ((rect[1] + y) << 16) | ((rect[0] + x) & 0xFFFF)
        check("Maximieren-Knopf meldet Snap-Layouts", settings._hit_test(lparam(round(point.x() * ratio), round(point.y() * ratio))) == HTMAXBUTTON)
        drag = title.mapTo(settings, QPoint(title.title.geometry().right() + 8, 10))
        check("Titelleiste lässt sich ziehen", settings._hit_test(lparam(round(drag.x() * ratio), round(drag.y() * ratio))) == HTCAPTION)
        settings.search.setText("icon")
        yield 300
        check("Suche findet Einstellungen", settings.hits.count() >= 2, settings.hits.item(0).text() if settings.hits.count() else "")
        shot(settings, "settings")
        settings._search_accept()
        yield 400
        check("Springt zur Einstellung", settings.tabs.currentIndex() >= 0 and not settings.hits.isVisible())
        settings.toggle_maximized()
        yield 900
        check("Maximieren", settings.isMaximized())
        settings.toggle_maximized()
        yield 900
        settings.hide()

        # Auto-Hide
        config.set_monitor(None, "auto_hide", True)
        yield 1800
        check("Taskbar blendet aus", not dock.isVisible())
        check("Platz wird nicht mehr reserviert", not dock.reserves_space())
        area = primary.geometry()
        win_api.user32.SetCursorPos(area.center().x(), area.y() + area.height() - 2)
        yield 900
        check("Erscheint am Rand", dock.isVisible() and dock.windowOpacity() > 0.9)
        shot(dock, "dock_autohide")
        win_api.user32.SetCursorPos(area.center().x(), area.center().y())
        yield 1500
        check("Verschwindet wieder", not dock.isVisible())
        config.set_monitor(None, "auto_hide", False)
        yield 900
    finally:
        child.kill()
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(110, exit=True)
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["monitor_shared"]["auto_hide"] = False
    primary = QGuiApplication.primaryScreen()
    second = next(s for s in QGuiApplication.screens() if s != primary)
    config.data["bar_screens"]["topbar"] = {"mode": "primary", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "custom", "screens": [screen_key(primary), screen_key(second)]}
    ctx = popwin_main.PopWinApp(config)
    cursor = win_api.wintypes.POINT()
    win_api.user32.GetCursorPos(win_api.wintypes.LPPOINT(cursor))
    previous = win_api.foreground_window()
    ctx.status.start()
    ctx.apps.run()
    ctx.rebuild_bars()
    sequence = steps(ctx, app)

    def advance():
        try:
            QTimer.singleShot(next(sequence), advance)
        except StopIteration:
            pass
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()

    QTimer.singleShot(2500, advance)
    QTimer.singleShot(100000, app.quit)
    try:
        app.exec()
    finally:
        ctx.shutdown()
        win_api.user32.SetCursorPos(cursor.x, cursor.y)
        if previous:
            win_api.activate_window(previous)
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:5]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
