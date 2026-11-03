"""Spielt Sync- und Einzelmodus auf allen echten Monitoren durch, inklusive Einstellungsseite.

Reserviert kurz Platz auf jedem Bildschirm; maximierte Fenster können dabei springen.
Start: python tests/multi_monitor_check.py <Ausgabeordner>
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
from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton

import main as popwin_main
from config.config_manager import ConfigManager
from core.monitor_manager import native_rect, screen_key
from ui.taskbar import Dock
from ui.topbar import TopBar

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
STARTED = time.perf_counter()
results: list[tuple[str, bool, str]] = []
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{time.perf_counter() - STARTED:5.1f}s " + ("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def steps(ctx, app):
    config = ctx.config
    screens = QGuiApplication.screens()
    keys = [screen_key(s) for s in screens]
    count = len(screens)
    tops = lambda: [b for b in ctx.bars if isinstance(b, TopBar)]
    docks = lambda: [b for b in ctx.bars if isinstance(b, Dock)]

    check("Standard: Topbar und Taskbar auf jedem Monitor", len(tops()) == count and len(docks()) == count,
          f"{len(tops())} Topbars, {len(docks())} Docks, {count} Monitore")
    for bar in ctx.bars:
        left, top, right, bottom = bar._rect
        mon = native_rect(bar.bar_screen)
        inside = left >= mon[0] and top >= mon[1] and right <= mon[2] and bottom <= mon[3]
        if not inside:
            check("Leiste liegt auf ihrem Monitor", False, f"{bar._rect} / {mon}")
    check("Alle Leisten auf ihrem Monitor", not [r for r in results if not r[1]])

    # Der Sync-Modus: eine Änderung, alle Monitore
    config.set_monitor(keys[0], "theme", "clean")
    yield 400
    check("Sync: Stil gilt auf allen Monitoren", all(b.theme_name == "clean" for b in ctx.bars))
    config.set_monitor(None, "icon_size", 20)
    yield 200
    check("Sync: Icon-Größe gilt ohne Neuaufbau überall", all(d._icon_size() == 20 for d in docks()))
    config.set_monitor(None, "theme", "")
    yield 400

    # Der Einzelmodus: jeder Monitor für sich
    ctx.settings._switch_mode("separate")
    yield 400
    first, second = keys[0], keys[1]
    identity = {id(b) for b in ctx.bars}
    config.set_monitor(first, "theme", "minimalist")
    config.set_monitor(second, "show_user", False)
    yield 600
    by_key = {b.screen_id: b for b in tops()}
    check("Stilwechsel baut keine Leiste neu auf", {id(b) for b in ctx.bars} == identity)
    check("Einzeln: Stil nur auf einem Monitor", by_key[first].theme_name == "minimalist"
          and all(b.theme_name != "minimalist" for k, b in by_key.items() if k != first))
    check("Einzeln: Inhalt nur dort geändert", not by_key[second].user.isVisibleTo(by_key[second])
          and by_key[first].user.isVisibleTo(by_key[first]))
    for index, bar in enumerate(tops()):
        bar.grab().save(str(out / f"einzeln_{index}.png"))

    # Die Monitorwahl pro Leiste
    config.set_bar_screens("topbar", "primary", [])
    yield 500
    primary_key = screen_key(QGuiApplication.primaryScreen())
    check("Topbar nur auf dem Hauptmonitor", [b.screen_id for b in tops()] == [primary_key]
          and len(docks()) == count)
    config.set_bar_screens("taskbar", "custom", [keys[1], keys[2]])
    yield 500
    check("Taskbar genau auf den gewählten Monitoren", {d.screen_id for d in docks()} == {keys[1], keys[2]})
    config.set_bar_screens("topbar", "custom", [])
    yield 500
    check("Topbar auf keinem Monitor möglich", not tops())
    ctx.settings._fill_monitors()
    yield 200
    boxes = [b.text() for b in ctx.settings.monitor_page.findChildren(QCheckBox) if "Hauptmonitor)" in b.text()]
    check("Einstellungen bieten Monitore zum Ankreuzen", len(boxes) == 2, str(boxes))
    config.set_bar_screens("topbar", "all", [])
    config.set_bar_screens("taskbar", "all", [])
    yield 700
    check("Zurück auf allen Monitoren", len(tops()) == count and len(docks()) == count)

    # Die Einstellungsseite
    ctx.settings._fill_monitors()
    yield 200
    groups = ctx.settings.monitor_page.findChildren(QComboBox)
    buttons = [b for b in ctx.settings.monitor_page.findChildren(QPushButton)
               if b.text() == "Auf alle Monitore übertragen" and b.isEnabled()]
    check("Einstellungen zeigen jeden Monitor", len(buttons) >= count, f"{len(buttons)} Übertragen-Knöpfe")
    ctx.settings.tabs.setCurrentWidget(ctx.settings.monitor_page)
    ctx.settings.apply_theme()
    ctx.settings.resize(760, 900)
    yield 300
    ctx.settings.grab().save(str(out / "settings_einzeln.png"))
    ctx.settings._copy_to_all(first)
    yield 700
    check("Übertragen gleicht alle Monitore an", all(b.theme_name == "minimalist" for b in ctx.bars)
          and len(docks()) == count)

    # Zurück in den Sync-Modus
    config.set_monitor(second, "theme", "clean")
    ctx.settings._switch_mode("sync")
    yield 700
    check("Zurück zu Sync: alle wieder gleich", len({b.theme_name for b in ctx.bars}) == 1)
    ctx.settings._switch_mode("separate")
    yield 500
    check("Einzelprofil überlebt den Wechsel", config.monitor(second)["theme"] == "clean")
    ctx.settings._switch_mode("sync")
    yield 400
    ctx.settings._fill_monitors()
    yield 200
    ctx.settings.grab().save(str(out / "settings_sync.png"))
    start = time.perf_counter()
    for dock in docks():
        dock.refresh()
    check("Taskbar-Aktualisierung bleibt flott", time.perf_counter() - start < 0.2,
          f"{(time.perf_counter() - start) * 1000:.0f} ms für {len(docks())} Docks")
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(100, exit=True)  # zeigt die Stelle, falls etwas blockiert
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
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
            app.quit()

    QTimer.singleShot(1000, advance)
    QTimer.singleShot(90000, app.quit)
    app.exec()
    ctx.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)} von {len(results)} bestanden, Fehler im Code: {len(errors)}")
    for error in errors[:5]:
        print(error)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
