"""Zeigt Launcher, Menü und Medien-Widget echt auf dem Bildschirm, aber ohne Fokus zu nehmen.

Damit lässt sich prüfen, ob DWM (Blur) und Qt (Inhalt) zusammen das richtige Bild ergeben.
Start: python tests/live_windows.py <Ausgabeordner>
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
win_api.force_foreground = lambda hwnd: None  # kein Fokus-Klau, das Spiel läuft weiter
WAIT = 2500


def photo(window, name: str) -> None:
    rect = window.frameGeometry()
    screen = window.screen()
    origin = screen.geometry().topLeft()
    screen.grabWindow(0, rect.x() - origin.x(), rect.y() - origin.y(), rect.width(), rect.height()).save(
        str(out / f"{name}.png"))
    print(name, rect.getRect(), flush=True)


def main() -> int:
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["theme"] = "liquid_glass"
    ctx = popwin_main.PopWinApp(config)
    ctx.apps.run()
    screens = QGuiApplication.screens()
    target = next((s for s in screens if not ctx.monitors.is_primary(s)), screens[0])  # Nebenmonitor
    geometry = target.availableGeometry()
    ctx.media.start()
    launcher = ctx.launcher
    launcher.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def step1():
        launcher.apply_theme()
        launcher.input.setText("reg")
        launcher.move(geometry.x() + 100, geometry.y() + 100)
        launcher.show()
        QTimer.singleShot(WAIT, step2)

    def step2():
        photo(launcher, "live_launcher")
        launcher.hide()
        ctx.context_menu.build(True)
        ctx.context_menu.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        ctx.context_menu.show_at(QPoint(geometry.x() + 100, geometry.y() + 100), True)
        QTimer.singleShot(WAIT, step3)

    def step3():
        photo(ctx.context_menu, "live_menu")
        ctx.context_menu.close()
        ctx.widgets.windows.clear()
        spec = {"id": "m", "type": "media", "x": geometry.x() + 100, "y": geometry.y() + 100, "z": "overlay", "data": {}}
        config.data["widgets"] = [spec]
        ctx.widgets.start()
        QTimer.singleShot(WAIT + 1000, step4)

    def step4():
        photo(ctx.widgets.windows["m"], "live_media")
        app.quit()

    QTimer.singleShot(1500, step1)
    QTimer.singleShot(30000, app.quit)
    app.exec()
    ctx.shutdown()
    print("Fehler:", len(errors))
    for error in errors[:5]:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
