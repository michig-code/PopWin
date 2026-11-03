"""Prüft den PopWin Explorer.

Start: python tests/explorer_check.py <Ausgabeordner>
"""
import faulthandler
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from explorer.app import ExplorerContext, ExplorerWindow, search_tree

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


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


def steps(app):
    folder = Path(tempfile.mkdtemp(prefix="popwin_explorer_"))
    (folder / "Unterordner").mkdir()
    (folder / "Unterordner" / "tief_liegend.txt").write_text("Hallo aus der Tiefe", encoding="utf-8")
    (folder / "notiz.txt").write_text("Vorschau-Text", encoding="utf-8")
    context = ExplorerContext()
    window = ExplorerWindow(context, str(folder))
    window.show()
    yield 1500
    pane = window.current_pane()
    check("Explorer öffnet den Ordner", pane.path == str(folder) and window.isVisible(), pane.path)
    window.new_tab(str(folder / "Unterordner"))
    yield 500
    check("Zweiter Tab", window.tabbar.count() == 2)
    check("Tab zeigt den Ordnernamen", window.tabbar.tabText(1) == "Unterordner", window.tabbar.tabText(1))
    window.close_tab(1)
    yield 300
    window.toggle_split()
    yield 500
    check("Geteilte Ansicht", len(window._page(0).panes) == 2)
    window.preview.show_path(str(folder / "notiz.txt"))
    yield 300
    check("Text-Vorschau", "Vorschau-Text" in window.preview.text.toPlainText())
    pane.search.setText("tief")
    yield 1200
    check("Blitzsuche findet auch Unterordner", pane.results.count() == 1, str(pane.results.count()))
    shot(window, "explorer")
    pane.search.clear()
    yield 400
    pane.open(str(folder / "Unterordner"))
    yield 300
    pane.go_back()
    yield 300
    check("Zurück-Navigation", pane.path == str(folder))
    check("Schutz vor Windows-Ordnern", not list(search_tree(str(folder), "zzzgibtsnicht", lambda: False)))
    window.close()
    yield 300

    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(80, exit=True)
    app = popwin_main.create_application([])
    sequence = steps(app)

    def advance():
        try:
            QTimer.singleShot(next(sequence), advance)
        except StopIteration:
            pass
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()

    QTimer.singleShot(200, advance)
    QTimer.singleShot(70000, app.quit)
    app.exec()
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:5]:
        print(error)
    sys.stdout.flush()
    os._exit(1 if failed or errors else 0)


if __name__ == "__main__":
    main()
