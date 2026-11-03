"""Startet die echte Oberfläche mit einer Wegwerf-Konfiguration und macht Fotos aller Teile.

Greift nicht in Windows ein: kein Darkmode-Wechsel, Taskleiste und Papierkorb bleiben unberührt.
Start: python tests/smoke_ui.py <Ausgabeordner>
"""
import logging
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from core.theme_manager import PRESETS

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def photo(window, name: str) -> None:
    rect = window.frameGeometry()
    screen = window.screen()
    screen.grabWindow(0, rect.x() - screen.geometry().x(), rect.y() - screen.geometry().y(),
                      rect.width(), rect.height()).save(str(out / f"{name}.png"))


def steps(ctx):
    primary = QGuiApplication.primaryScreen()
    topbar, dock = ctx.bars[0], ctx.bars[1]
    ctx.status.updated.disconnect(ctx._follow_system_mode)  # sonst setzt der Status den Modus zurück
    for theme in PRESETS:
        ctx.theme.set_theme(theme)
        for mode in ("dark", "light"):
            ctx.theme.set_mode(mode)
            yield 500
            photo(topbar, f"{theme}_{mode}_topbar")
            photo(dock, f"{theme}_{mode}_dock")
    ctx.theme.set_theme("liquid_glass")
    ctx.theme.set_mode("dark")
    yield 300

    ctx.launcher.open()
    ctx.launcher.input.setText("whitemode aktivieren")
    yield 400
    photo(ctx.launcher, "launcher_mode")
    print("launcher mode items:", [ctx.launcher.results.item(i).text() for i in range(ctx.launcher.results.count())])
    ctx.launcher.input.setText("reg")
    yield 300
    photo(ctx.launcher, "launcher_apps")
    print("launcher reg items:", [ctx.launcher.results.item(i).text() for i in range(ctx.launcher.results.count())])
    ctx.launcher.input.setText("? test")
    ctx.launcher._accept()
    yield 300
    photo(ctx.launcher, "launcher_nokey")
    print("launcher answer:", ctx.launcher.answer.text())
    ctx.launcher.hide()
    yield 200

    ctx.start_menu.toggle(dock.start)
    yield 500
    photo(ctx.start_menu, "start_menu")
    ctx.start_menu.close()
    yield 200

    for desktop, name in ((True, "context_desktop"), (False, "context_ui")):
        geo = primary.availableGeometry()
        ctx.show_context_menu(geo.center(), desktop)
        yield 500
        photo(ctx.context_menu, name)
        ctx.context_menu.close()
        yield 200

    topbar._show_volume()
    yield 400
    photo(topbar._volume_popup, "popup_volume")
    topbar._volume_popup.close()
    topbar._show_network()
    yield 400
    photo(topbar._network_popup, "popup_network")
    print("network:", topbar._network_popup.title.text(), repr(topbar._network_popup.detail.text()))
    topbar._network_popup.close()

    for kind in ("sysmon", "notes", "media", "clock"):
        ctx.widgets.add(kind)
    yield 2500
    for spec in ctx.config.get("widgets"):
        window = ctx.widgets.windows[spec["id"]]
        window.raise_()
        win_api.set_zorder(int(window.winId()), win_api.HWND_TOPMOST)
        yield 200
        photo(window, f"widget_{spec['type']}")
        window._zorder_timer.stop()
        window.spec["z"] = "desktop"
        window.apply_layer()

    ctx.settings.open_page(setup=True)
    yield 500
    for index in range(ctx.settings.tabs.count()):
        ctx.settings.tabs.setCurrentIndex(index)
        yield 300
        ctx.settings.grab().save(str(out / f"settings_{index}.png"))

    print("status:", {k: v for k, v in ctx.status.state.items()})
    print("media:", {k: v for k, v in (ctx.last_media or {}).items() if k != "thumbnail"})
    print("apps:", len(ctx.apps.apps))
    print("windows:", [(w.title[:30], Path(w.exe).name) for w in win_api.list_windows()][:8])
    print("buttons:", list(dock._buttons))
    print("workarea top/bottom primary:", primary.availableGeometry())
    print("bar rects:", [bar._rect for bar in ctx.bars])


def main() -> int:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as folder:
        config = ConfigManager(Path(folder) / "settings.json")
        config.data["first_run"] = False
        config.data["shell"]["hide_native_taskbar"] = False
        ctx = popwin_main.PopWinApp(config)
        ctx.start()
        print("gestartet, Leisten:", len(ctx.bars))
        sequence = steps(ctx)

        def advance():
            try:
                delay = next(sequence)
            except StopIteration:
                app.quit()
                return
            except Exception:
                errors.append(traceback.format_exc())
                app.quit()
                return
            QTimer.singleShot(delay, advance)

        QTimer.singleShot(4000, advance)  # Status, Medien und App-Index einlaufen lassen
        QTimer.singleShot(90000, app.quit)  # Notbremse
        app.exec()
        ctx.shutdown()
    print("FEHLER:" if errors else "keine Fehler", len(errors))
    for error in errors[:10]:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
