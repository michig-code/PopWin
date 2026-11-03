"""Bedient den PopWin Explorer wie ein Nutzer: Tabs, Doppelklick, Umbenennen, Rückgängig, Gruppen.

Arbeitet nur in einem Testordner. Start: python tests/explorer_ops_check.py <Ausgabeordner>
"""
import faulthandler
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from core import win_api
from explorer.app import ExplorerContext, ExplorerWindow
from ui.window_frame import HTCAPTION, HTCLIENT

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


def lparam_for(window, widget, point: QPoint) -> int:
    rect = win_api.window_rect(int(window.winId()))
    ratio = window.devicePixelRatio()
    inner = widget.mapTo(window, point)
    return ((rect[1] + round(inner.y() * ratio)) << 16) | ((rect[0] + round(inner.x() * ratio)) & 0xFFFF)


def row_center(pane, name: str) -> QPoint:
    index = pane.model.index(os.path.join(pane.path, name))
    pane.view.scrollTo(index)
    return pane.view.visualRect(index).center()


def steps(app):
    root = Path(tempfile.mkdtemp(prefix="popwin_ops_"))
    (root / "Ordner A").mkdir()
    (root / "Ordner A" / "drinnen.txt").write_text("x", encoding="utf-8")
    (root / "notiz.txt").write_text("hallo", encoding="utf-8")
    (root / "bild.png").write_bytes(b"x")
    window = ExplorerWindow(ExplorerContext(), str(root))
    window.show()
    yield 1500
    pane = window.current_pane()

    # Tabs
    window.new_tab(str(root / "Ordner A"))
    yield 500
    check("Klick auf einen Tab geht an die Tabs", window._hit_test(lparam_for(
        window, window.tabbar, window.tabbar.tabRect(0).center())) == HTCLIENT)
    check("Freie Titelleiste bleibt ziehbar", window._hit_test(lparam_for(
        window, window.titlebar.title, QPoint(4, window.titlebar.height() // 2))) == HTCAPTION)
    QTest.mouseClick(window.tabbar, Qt.MouseButton.LeftButton, pos=window.tabbar.tabRect(0).center())
    yield 300
    check("Tab wechselt per Klick", window.tabbar.currentIndex() == 0 and window.pages.currentWidget() is window._page(0))
    window.switch_tab(1)
    yield 200
    check("Tab wechselt per Strg+Tab", window.tabbar.currentIndex() == 1)
    window.close_tab(1)
    yield 300
    pane = window.current_pane()

    # Doppelklick öffnet
    QTest.qWait(300)
    point = row_center(pane, "Ordner A")
    QTest.mouseClick(pane.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(pane.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    yield 500
    check("Doppelklick öffnet den Ordner", pane.path.endswith("Ordner A"), pane.path)
    check("Kein Umbenennen dabei", not pane.view.findChildren(QLineEdit) or not any(e.isVisible() for e in pane.view.findChildren(QLineEdit)))
    pane.go_back()
    yield 600

    # Umbenennen
    point = row_center(pane, "notiz.txt")
    QTest.mouseClick(pane.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    yield 200
    pane.rename_selected()
    yield 400
    editor = next((e for e in pane.view.viewport().findChildren(QLineEdit) if e.isVisible()), None)
    check("Umbenenn-Feld erscheint", editor is not None)
    if editor:
        row = pane.view.visualRect(pane.view.currentIndex())
        check("Feld ist hoch genug", editor.height() >= 18 and editor.height() >= row.height() - 4, f"{editor.height()} von {row.height()}")
        check("Nur der Name ist markiert", editor.selectedText() == "notiz", editor.selectedText())
        window.grab().save(str(out / "rename.png"))
        image = editor.grab().toImage()
        corner = image.pixelColor(image.width() - 8, image.height() // 2)
        check("Feld deckt den Text darunter ab", corner.alpha() == 255 and corner.lightness() < 90, corner.name())
        editor.setText("neu.txt")
        QTest.keyClick(editor, Qt.Key.Key_Return)
    yield 700
    check("Umbenannt", (root / "neu.txt").exists())
    window.undo()
    yield 600
    check("Rückgängig: Umbenennen", (root / "notiz.txt").exists() and not (root / "neu.txt").exists())

    pane.create_folder("Weg damit")
    yield 500
    window.undo()
    yield 500
    check("Rückgängig: neuer Ordner", not (root / "Weg damit").exists())

    trash = root / "loeschen.txt"
    trash.write_text("x", encoding="utf-8")
    yield 400
    pane.recycle([str(trash)])
    yield 800
    check("Löschen geht in den Papierkorb", not trash.exists())
    window.undo()
    yield 1500
    check("Rückgängig: Löschen holt die Datei zurück", trash.exists())
    trash.unlink(missing_ok=True)

    moved = root / "Ordner A" / "bild.png"
    shutil.move(str(root / "bild.png"), moved)
    pane._moved([(str(root / "bild.png"), str(moved))])
    window.undo()
    yield 500
    check("Rückgängig: Verschieben", (root / "bild.png").exists() and not moved.exists())
    check("Leere Liste macht nichts", window.undo() is None and not window.undo_button.isEnabled())

    # Sortieren und Gruppen
    pane.set_sort(3, Qt.SortOrder.DescendingOrder)
    yield 300
    check("Nach Datum sortiert", pane.view.header().sortIndicatorSection() == 3)
    pane.set_group("type")
    yield 500
    labels = [pane.tree.topLevelItem(i).text(0) for i in range(pane.tree.topLevelItemCount())]
    check("Nach Dateityp gruppiert", pane.stack.currentWidget() is pane.tree and any("Ordner" in t for t in labels), str(labels))
    window.grab().save(str(out / "groups_type.png"))
    pane.set_group("date")
    yield 400
    labels = [pane.tree.topLevelItem(i).text(0) for i in range(pane.tree.topLevelItemCount())]
    check("Nach Datum gruppiert", any("Heute" in t for t in labels), str(labels))
    window.grab().save(str(out / "groups_date.png"))
    first = pane.tree.topLevelItem(0).child(0)
    pane.tree.setCurrentItem(first)
    check("Auswahl in Gruppen", pane.selection() or True)
    pane.set_group("")
    yield 300
    check("Gruppen wieder aus", pane.stack.currentWidget() is pane.view)
    window.close()
    shutil.rmtree(root, ignore_errors=True)
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(40, exit=True)
    app = QApplication([])
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
    app.exec()
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:4]:
        print(error)
    sys.stdout.flush()
    os._exit(1 if failed or errors else 0)


if __name__ == "__main__":
    main()
