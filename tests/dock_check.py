"""Prüft schmale Taskbar, Ausrichtung, Abstand, Anheften und Animationen an einer echten Leiste.

Start: python tests/dock_check.py <Ausgabeordner>
"""
import faulthandler
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent, QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from core.monitor_manager import native_rect
from ui.taskbar import Dock

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def drop(dock, paths) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dock.dropEvent(event)


def steps(ctx, app):
    config = ctx.config
    dock = next(b for b in ctx.bars if isinstance(b, Dock))
    monitor = native_rect(dock.bar_screen)
    width = lambda: dock._placed[2]
    x = lambda: dock._placed[0]

    yield 700
    check("Einblenden endet sichtbar", dock.windowOpacity() > 0.99, str(dock.windowOpacity()))
    full_width = width()

    config.set_monitor(None, "dock_style", "fit")
    yield 900
    check("Schmale Taskbar ist schmaler", width() < full_width / 1.5, f"{full_width} -> {width()}")
    check("Mittig auf dem Monitor", abs((x() + width() / 2) - (monitor[0] + monitor[2]) / 2) <= 2)
    check("Schwebt mit Abstand zum Rand", dock._placed[1] + dock._placed[3] < monitor[3])
    screen = dock.bar_screen
    geo = screen.geometry()

    def photo(name):
        px, py, w, h = dock._placed
        screen.grabWindow(0, px - geo.x() - 20, py - geo.y() - 20, w + 40, h + 40).save(str(out / f"{name}.png"))

    photo("fit_center")

    before = width()
    config.set_monitor(None, "icon_spacing", 20)
    yield 500
    gaps = len(dock._buttons) - 1
    check("Abstand macht die Leiste breiter", width() - before >= gaps * 14, f"{before} -> {width()} bei {gaps} Lücken")
    photo("fit_spacing")
    config.set_monitor(None, "icon_spacing", 3)
    yield 400

    before = width()
    folder = Path(tempfile.mkdtemp())
    document = folder / "PopWin Testdatei.txt"
    document.write_text("x", encoding="utf-8")
    drop(dock, [str(document)])
    yield 900
    pins = config.get("taskbar.pinned")
    check("Drag and Drop heftet an", any(p["target"] == os.path.normpath(document) for p in pins),
          str([p["name"] for p in pins]))
    check("Leiste wächst mit der App", width() > before, f"{before} -> {width()}")
    drop(dock, [str(document)])
    yield 500
    check("Doppeltes Anheften wird ignoriert", len(config.get("taskbar.pinned")) == len(pins))

    start_menu = Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    link = next((p for p in start_menu.rglob("*.lnk") if win_api.shortcut_target(str(p)).lower().endswith(".exe")), None)
    if link:
        drop(dock, [str(link)])
        yield 700
        added = config.get("taskbar.pinned")[-1]
        check("Verknüpfung merkt sich das Programm", added.get("exe", "").endswith(".exe"), str(added))

    running = next((b for b in dock._buttons.values() if b.windows and b.target and not dock.is_pinned(b.key)), None)
    check("Laufende, nicht angeheftete App vorhanden", running is not None)
    if running:
        key = running.key
        dock.toggle_pin(running)
        yield 500
        check("Rechtsklick-Funktion heftet an", dock.is_pinned(key), key)
        dock.toggle_pin(dock._buttons[key])
        yield 500
        check("Und löst wieder", not dock.is_pinned(key))

    before = width()
    config.set("taskbar.pinned", config.get("taskbar.pinned")[:2])
    yield 900
    check("Leiste schrumpft mit weniger Apps", width() < before, f"{before} -> {width()}")

    config.set_monitor(None, "dock_align", "left")
    yield 500
    check("Schmal links", abs(x() - monitor[0]) <= 8, str(x()))
    config.set_monitor(None, "dock_align", "right")
    yield 500
    check("Schmal rechts", abs(x() + width() - monitor[2]) <= 8, str(x() + width()))
    photo("fit_right")

    config.set_monitor(None, "dock_style", "full")
    config.set_monitor(None, "dock_align", "center")
    yield 900
    left = dock.start.mapToGlobal(dock.start.rect().topLeft()).x()
    last = list(dock._buttons.values())[-1]
    right = last.mapToGlobal(last.rect().topRight()).x()
    check("Volle Breite: Icons in der echten Mitte", abs((left + right) / 2 - (monitor[0] + monitor[2]) / 2) <= 12,
          f"{left}..{right} pad={dock.pad.width()} lead={dock.lead.width()}/{dock.lead.isVisible()} "
          f"trail={dock.trail.width()}/{dock.trail.isVisible()} fenster={dock.width()}")
    config.set_monitor(None, "dock_align", "right")
    yield 500
    check("Volle Breite: Icons rechts", dock.start.mapToGlobal(dock.start.rect().topLeft()).x() > left)
    config.set_monitor(None, "dock_align", "left")
    yield 500
    check("Volle Breite: Icons links", dock.start.mapToGlobal(dock.start.rect().topLeft()).x() < left)

    config.set_monitor(None, "dock_height", 80)
    config.set_monitor(None, "icon_size", 48)
    yield 1500
    check("Höhe einstellbar", dock._placed[3] == 80, str(dock._placed))
    check("Icon-Größe folgt", dock._icon_size() == 48)
    config.set_monitor(None, "show_start", False)
    config.set_monitor(None, "show_search", False)
    config.set_monitor(None, "show_media", False)
    config.set_monitor(None, "show_recycle", False)
    yield 500
    check("Nur noch Icons", not any(w.isVisible() for w in (dock.start, dock.search, dock.media, dock.trash)))
    config.set_monitor(None, "dock_style", "fit")
    yield 800
    photo("only_icons")

    config.set("animations.enabled", False)
    ctx.launcher.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    ctx.launcher.animator.enter(0.7, 10)
    check("Animationen abschaltbar", abs(ctx.launcher.windowOpacity() - 0.7) < 0.02)
    config.set("animations.enabled", True)

    samples = []

    def sample():
        samples.append(dock.windowOpacity())
        if len(samples) < 12:
            QTimer.singleShot(20, sample)

    sample()
    ctx.disable_ui()
    yield 800
    print("Ausblend-Verlauf:", [round(s, 2) for s in samples])
    check("Beenden blendet stufenweise aus", any(0.05 < s < 0.95 for s in samples))
    check("Beenden räumt auf", ctx._closed)


def main() -> int:
    faulthandler.dump_traceback_later(70, exit=True)
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    config.data["monitor_shared"]["auto_hide"] = False  # dieser Test misst die dauerhaft sichtbare Leiste
    config.data["bar_screens"]["topbar"] = {"mode": "custom", "screens": []}
    config.data["bar_screens"]["taskbar"] = {"mode": "primary", "screens": []}
    ctx = popwin_main.PopWinApp(config)
    ctx.rebuild_bars()
    sequence = steps(ctx, app)

    def advance():
        try:
            QTimer.singleShot(next(sequence), advance)
        except StopIteration:
            pass
        except Exception:
            errors.append(traceback.format_exc())

    app.aboutToQuit.connect(lambda: None)
    QTimer.singleShot(200, advance)
    QTimer.singleShot(60000, app.quit)
    # disable_ui() beendet die Schleife; die letzten Prüfungen laufen danach.
    app.exec()
    end = QTimer(singleShot=True, interval=1000)
    end.timeout.connect(app.quit)
    end.start()
    app.exec()
    ctx.shutdown()
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:5]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
