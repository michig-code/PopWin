"""Betätigt die Schnelleinstellungen echt und stellt jeden Windows-Zustand wieder her.

Bluetooth und "PC schlafen legen" werden bewusst nicht ausgelöst.
Start: python tests/quick_check.py <Ausgabeordner>
"""
import faulthandler
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QSpinBox

import main as popwin_main
from config.config_manager import ConfigManager
from core import quick_settings as qs
from core import win_api
from ui.base import IconButton
from ui.topbar import TopBar

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def real_focus_active() -> bool:
    from winsdk.windows.ui.shell import FocusSessionManager
    return FocusSessionManager.get_default().is_focus_active


def brightness(screen, rect) -> float:
    left, top, right, bottom = rect
    geo = screen.geometry()
    image = screen.grabWindow(0, left - geo.x(), top - geo.y(), right - left, bottom - top).toImage()
    points = [image.pixelColor(x, image.height() // 2).lightness()
              for x in range(5, image.width() - 5, max(1, image.width() // 40))]
    return sorted(points)[len(points) // 2]


def steps(ctx, app):
    bar = next(b for b in ctx.bars if isinstance(b, TopBar))
    bar.quick.click()
    yield 700
    panel = bar._quick_popup
    check("Symbol öffnet das Panel", panel is not None and panel.isVisible())
    geometry = panel.frameGeometry()
    screen = panel.screen()
    origin = screen.geometry().topLeft()
    screen.grabWindow(0, geometry.x() - origin.x(), geometry.y() - origin.y(),
                      geometry.width(), geometry.height()).save(str(out / "panel.png"))
    icon = bar.quick.mapToGlobal(bar.quick.rect().topLeft()).x()
    clock = bar.clock.mapToGlobal(bar.clock.rect().topLeft()).x()
    check("Symbol sitzt links neben der Uhr", 0 < clock - icon < 60, f"{icon} / {clock}")
    tile = lambda key: panel.tiles[key][0]

    mode = win_api.system_mode()
    target = "light" if mode == "dark" else "dark"
    tile("dark").click()
    yield 900
    check("Dark-Mode-Schalter ändert Windows", win_api.system_mode() == target, win_api.system_mode())
    check("und PopWin", bar.property("popMode") == target)
    tile("dark").setChecked(mode == "dark")
    ctx.set_dark_mode(mode)
    yield 900
    check("Modus wiederhergestellt", win_api.system_mode() == mode)

    quiet = qs.do_not_disturb()
    panel.refresh()
    tile("quiet").click()
    yield 500
    check("Nicht stören schaltet", qs.do_not_disturb() != quiet, str(qs.do_not_disturb()))
    qs.set_do_not_disturb(quiet)
    yield 300
    check("Nicht stören wiederhergestellt", qs.do_not_disturb() == quiet)

    toasts = qs.notifications_enabled()
    panel.refresh()
    tile("toasts").click()
    yield 400
    check("Mitteilungen schalten", qs.notifications_enabled() != toasts)
    qs.set_notifications_enabled(toasts)
    yield 300
    check("Mitteilungen wiederhergestellt", qs.notifications_enabled() == toasts)

    ctx.widgets.add("clock")
    yield 500
    window = next(iter(ctx.widgets.windows.values()))
    panel.refresh()
    tile("widgets").click()
    yield 700
    check("Widgets ausblenden", not window.isVisible())
    panel.refresh()
    tile("widgets").click()
    yield 700
    check("Widgets einblenden", window.isVisible())

    panel.refresh()
    tile("animations").click()
    check("Animationen abschalten", ctx.config.get("animations.enabled") is False)
    ctx.config.set("animations.enabled", True)

    was_active = real_focus_active()
    check("Vor dem Test läuft keine Fokus-Sitzung", not was_active)
    if not was_active:
        ctx.focus.start(1)
        yield 1500
        # Windows verweigert Programmen ohne App-Paket die eigene Fokus-Sitzung; dann greift der Ersatzweg.
        check("Fokus läuft", real_focus_active() or (ctx.focus._fallback and qs.do_not_disturb()),
              f"fallback={ctx.focus._fallback}")
        check("Restzeit in der Topbar", bar.focus_time.isVisible(), bar.focus_time.text())
        print("Fokus aktiv, Nicht stören:", qs.do_not_disturb())
        ctx.focus.stop()
        yield 1500
        check("Beenden stoppt die Fokus-Sitzung", not real_focus_active() and not ctx.focus.active)
        check("Restzeit verschwindet", not bar.focus_time.isVisible())

    manager = ctx.focus._manager
    ctx.focus._manager = staticmethod(lambda: None)
    ctx.focus.start(1)
    yield 400
    check("Ersatzweg nutzt Nicht stören", ctx.focus._fallback and qs.do_not_disturb())
    ctx.focus._end = time.monotonic() + 1
    yield 2200
    check("Ablauf beendet den Timer und Nicht stören", not ctx.focus.active and not qs.do_not_disturb())
    ctx.focus._manager = manager

    window_page = ctx.settings.topbar_page
    ctx.config.set_monitor(None, "dock_height", 80)
    ctx.settings._fill_monitors()
    yield 300
    spin = next(s for s in window_page.findChildren(QSpinBox) if s.value() == 80)
    spin.parent().findChild(IconButton).click()
    yield 300
    check("Zurücksetzen-Knopf wirkt", ctx.config.monitor()["dock_height"] == 0)

    win_api.hide_native_taskbar()
    for _ in range(5):
        yield 400
        win_api.reinforce_taskbar_hidden()
    win_api.show_native_taskbar(0)
    yield 1500
    expected_dark = win_api.system_mode() == "dark"
    for hwnd in win_api._native_taskbars():
        left, top, right, bottom = win_api.window_rect(hwnd)
        screen = QGuiApplication.screenAt(QPoint(left + 20, top + 10))
        if screen is None:
            continue
        value = brightness(screen, (left, top, right, bottom))
        ok = value < 110 if expected_dark else value > 110
        check(f"Windows-Taskleiste nach Rückkehr im richtigen Design ({screen.name()})", ok, f"Helligkeit {value}")
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(70, exit=True)
    mode, quiet, toasts = win_api.system_mode(), qs.do_not_disturb(), qs.notifications_enabled()
    state = win_api.taskbar_state()
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["bar_screens"]["topbar"] = {"mode": "primary", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "primary", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    previous = win_api.foreground_window()
    ctx.status.start()
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
    QTimer.singleShot(62000, app.quit)
    try:
        app.exec()
    finally:
        ctx.focus.stop()
        if win_api.system_mode() != mode:
            win_api.set_system_mode(mode)
        qs.set_do_not_disturb(quiet)
        if qs.notifications_enabled() != toasts:
            qs.set_notifications_enabled(toasts)
        win_api.show_native_taskbar(state)
        ctx.shutdown()
        if previous:
            win_api.activate_window(previous)
    print("Wiederhergestellt:", win_api.system_mode() == mode, qs.do_not_disturb() == quiet,
          qs.notifications_enabled() == toasts)
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:5]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
