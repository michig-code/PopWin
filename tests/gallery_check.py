"""Echter Mausklick auf Widget und Desktop, Galerie, alle Widgets und die App-Übernahme.

Start: python tests/gallery_check.py <Ausgabeordner>
"""
import faulthandler
import sys
import tempfile
import traceback
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QSpinBox

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from ui.taskbar import Dock
from ui.widgets import WIDGET_TYPES

MOUSE_RIGHT_DOWN, MOUSE_RIGHT_UP = 0x0008, 0x0010
VK_ESC = 0x1B
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def right_click(x: int, y: int) -> None:
    win_api.user32.SetCursorPos(x, y)
    win_api.user32.mouse_event(MOUSE_RIGHT_DOWN, 0, 0, 0, 0)
    win_api.user32.mouse_event(MOUSE_RIGHT_UP, 0, 0, 0, 0)


def photo(window, name: str) -> None:
    window.grab().save(str(out / f"{name}.png"))


def find_free_desktop_point():
    for rect in win_api.monitor_rects():
        for fx in (0.5, 0.7, 0.3, 0.85):
            for fy in (0.5, 0.3, 0.7):
                x, y = int(rect[0] + (rect[2] - rect[0]) * fx), int(rect[1] + (rect[3] - rect[1]) * fy)
                if win_api.is_desktop_window(win_api.user32.WindowFromPoint(wintypes.POINT(x, y))):
                    return x, y
    return None


def steps(ctx, app):
    config = ctx.config
    dock = next(b for b in ctx.bars if isinstance(b, Dock))
    before = len(config.get("taskbar.pinned"))
    added = ctx.import_windows_pins()
    yield 1500
    check("Windows-Apps übernommen", added >= 8, f"{before} + {added}")
    pins = config.get("taskbar.pinned")
    names = [p["name"] for p in pins]
    print("Pins:", names)
    check("Spotify und Discord dabei", "Spotify" in names and "Discord" in names)
    check("Kein zweiter Explorer", sum("explorer" in n.lower() for n in names) == 1)
    empty = [b.key for b in dock._buttons.values() if b.icon().isNull()]
    check("Alle Apps haben ein Icon", not empty, str(empty))
    check("Zweite Übernahme ändert nichts", ctx.import_windows_pins() == 0)
    dock.grab().save(str(out / "dock.png"))

    ctx.widgets.open_gallery()
    yield 700
    gallery = ctx.widgets.gallery
    check("Galerie geht ohne Widget auf", gallery.isVisible())
    for kind in WIDGET_TYPES:
        gallery._add(kind)
        yield 120
    yield 3500
    windows = ctx.widgets.windows
    check("Alle Widgets offen", len(windows) == len(WIDGET_TYPES) and all(w.isVisible() for w in windows.values()),
          str(len(windows)))
    state = ctx.status.state
    check("Prozessliste läuft", len(state["processes"]) >= 3, str(state["processes"][:2]))
    check("Laufwerke gelesen", len(state["disks"]) >= 1, str(len(state["disks"])))
    check("Netz-Tempo läuft", state["net_down"] >= 0 and ctx.status._net_last is not None, f"{state['net_down']:.0f} B/s")
    cpu = next(w for w in windows.values() if w.spec["type"] == "cpu")
    check("CPU-Widget hat einen Wert", cpu.gauge.value is not None, cpu.gauge.text)
    for window in windows.values():
        photo(window, f"widget_{window.spec['type']}")

    cpu.set_layer("overlay")
    primary = QGuiApplication.primaryScreen().availableGeometry()
    cpu.move(primary.x() + 300, primary.y() + 200)
    win_api.set_zorder(int(cpu.winId()), win_api.HWND_TOPMOST)
    check("Overlay liegt oben", win_api.is_topmost(int(cpu.winId())))
    gallery.close()
    yield 500
    center = cpu.frameGeometry().center()
    right_click(center.x(), center.y())
    yield 1000
    check("Echter Rechtsklick öffnet die Galerie", gallery.isVisible())
    check("mit dem angeklickten Widget", gallery.current is cpu, gallery.chooser.currentText())
    gallery.grab().save(str(out / "gallery_live.png"))

    spin = next(s for s in gallery.form_host.findChildren(QSpinBox) if s.value() == 110)
    spin.setValue(160)
    yield 300
    check("Feld ändert das Widget sofort", cpu.gauge.height() == 160, str(cpu.gauge.height()))
    for style in ("bar", "graph", "number", "ring"):
        cpu.set_option("style", style)
        yield 120
    photo(cpu, "cpu_ring_gross")
    cpu.set_option("accent", "#FF8800")
    check("Akzentfarbe gespeichert", cpu.spec["options"]["accent"] == "#FF8800")
    cpu.set_layer("desktop")
    yield 300
    check("Wieder auf der Desktop-Ebene", not win_api.is_topmost(int(cpu.winId())))
    cpu.set_opacity(0.5)
    check("Deckkraft wirkt", abs(cpu.windowOpacity() - 0.5) < 0.02)

    watch = next(w for w in windows.values() if w.spec["type"] == "stopwatch")
    watch.toggle.click()
    yield 1200
    watch.toggle.click()
    check("Stoppuhr läuft", 0.9 < watch._current() < 2.5, watch.value.text())

    reloaded = ConfigManager(config.path)
    saved = next(s for s in reloaded.get("widgets") if s["type"] == "cpu")
    check("Anpassungen überstehen einen Neustart", saved["options"].get("style") == "ring"
          and saved["options"].get("height") == 160 and saved["opacity"] == 0.5)

    gallery._remove(cpu)
    yield 300
    check("Entfernen wirkt", all(w.spec["type"] != "cpu" for w in windows.values()))
    ctx.widgets.set_visible(False)
    yield 600
    check("Alle Widgets lassen sich ausblenden", not any(w.isVisible() for w in windows.values()))
    gallery.close()
    yield 300

    point = find_free_desktop_point()
    if point is None:
        print("HINWEIS Keine freie Desktop-Stelle sichtbar, Rechtsklick nicht geprüft")
    else:
        right_click(*point)
        yield 1500
        shown = ctx.context_menu.isVisible()
        check("Rechtsklick auf den Desktop öffnet das PopWin-Menü", shown, str(point))
        if shown:
            ctx.context_menu.grab().save(str(out / "desktop_menu.png"))
            ctx.context_menu.close()
        else:
            win_api.send_keys(VK_ESC)
        yield 400
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(75, exit=True)
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["bar_screens"]["topbar"] = {"mode": "primary", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "primary", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    previous = win_api.foreground_window()
    cursor = wintypes.POINT()
    win_api.user32.GetCursorPos(wintypes.LPPOINT(cursor))
    ctx.status.start()
    ctx.rebuild_bars()
    ctx._restart_hooks()
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
    QTimer.singleShot(65000, app.quit)
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
