"""Drückt echte Tasten und prüft Windows-Taste, Win+S und die Suchanzeige.

Nimmt für etwa 15 Sekunden immer wieder den Fokus und gibt ihn danach zurück.
Start: python tests/win_key_check.py <Bild.png>
"""
import faulthandler
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api

VK_LWIN, VK_B, VK_R, VK_S, VK_ESC = 0x5B, 0x42, 0x52, 0x53, 0x1B
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def native_start_open() -> bool:
    exe = win_api.exe_of(win_api._pid(win_api.foreground_window())).lower()
    return "startmenuexperiencehost" in exe or "searchhost" in exe


def steps(ctx, app):
    home, launcher = ctx.start_menu, ctx.launcher
    win_api.send_keys(VK_LWIN)
    yield 1000
    check("Windows-Taste öffnet das Home-Menü", home.isVisible())
    check("Windows-Startmenü bleibt zu", not native_start_open())
    check("Home-Menü hat den Fokus", win_api._pid(win_api.foreground_window()) == win_api.kernel32.GetCurrentProcessId())
    win_api.send_keys(VK_LWIN)
    yield 800
    check("Erneut drücken schließt es", not home.isVisible())
    check("Auch dabei bleibt das Windows-Startmenü zu", not native_start_open())

    win_api.send_keys(VK_LWIN)
    yield 800
    win_api.send_keys(VK_ESC)
    yield 500
    check("Esc schließt das Home-Menü", not home.isVisible())

    win_api.send_keys(VK_LWIN)
    yield 800
    win_api.send_keys(VK_R)
    yield 800
    check("Lostippen startet die Suche", launcher.isVisible() and launcher.input.text().lower() == "r",
          repr(launcher.input.text()))
    win_api.send_keys(VK_ESC)
    yield 400
    check("Esc schließt die Suche", not launcher.isVisible())

    win_api.send_keys(VK_LWIN, VK_B)  # fokussiert nur den Infobereich
    yield 700
    check("Kombination öffnet kein Home-Menü", not home.isVisible())

    win_api.send_keys(VK_LWIN, VK_S)
    yield 800
    check("Win+S öffnet die PopWin-Suche", launcher.isVisible())
    check("Windows-Suche bleibt zu", not native_start_open())
    win_api.send_keys(VK_ESC)
    yield 400

    ctx.config.set("start_menu.win_key", "launcher")
    yield 800
    win_api.send_keys(VK_LWIN)
    yield 800
    check("Einstellung 'Suche': Windows-Taste öffnet die Suche", launcher.isVisible() and not home.isVisible())
    win_api.send_keys(VK_ESC)
    yield 400

    config = ctx.config
    config.data["launcher"].update(width=900, position="center", max_results=3, result_font=18, input_font=26,
                                   opacity=0.9, placeholder="Was suchst du?", show_icons=False,
                                   show_commands=False, theme="pop_basic", screen="primary")
    ctx.show_launcher()
    launcher.input.setText("reg")
    yield 800
    primary = QGuiApplication.primaryScreen().availableGeometry()
    check("Breite angewandt", launcher.width() == 900, str(launcher.width()))
    check("Auf dem Hauptmonitor mittig", primary.contains(launcher.geometry().center())
          and abs(launcher.geometry().center().x() - primary.center().x()) < 3)
    check("Deckkraft angewandt", abs(launcher.windowOpacity() - 0.9) < 0.02)
    check("Stil angewandt", launcher.theme_name == "pop_basic")
    check("Platzhalter angewandt", launcher.input.placeholderText() == "Was suchst du?")
    labels = [launcher.results.item(i).text() for i in range(launcher.results.count())]
    check("Systembefehle ausgeblendet", not any("regedit" in t or "Ausführen" in t for t in labels), str(labels))
    launcher.grab().save(sys.argv[1])
    launcher.hide()

    ctx.config.set("start_menu.win_key", "windows")
    yield 800
    check("Einstellung 'Windows': Hook fängt die Taste nicht mehr", ctx.hooks.remap_win_key is False)
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(60, exit=True)
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["launcher"]["override_win_s"] = True
    config.data["bar_screens"]["topbar"] = {"mode": "custom", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "primary", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    ctx.apps.run()
    ctx.rebuild_bars()
    ctx._restart_hooks()
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

    QTimer.singleShot(2000, advance)
    app.exec()
    ctx.shutdown()
    if previous:
        win_api.activate_window(previous)
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:4]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
