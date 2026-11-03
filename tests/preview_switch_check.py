"""Wechselt die Vorschau zwischen zwei Apps, wie beim Fahren über die Taskbar. Nimmt keinen Fokus.

Start: python tests/preview_switch_check.py <Ausgabeordner>
"""
import faulthandler
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api
from ui.task_preview import CARD_WIDTH, THUMB_HEIGHT, MAX_CARDS

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
failed = False
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    failed = failed or not ok
    print(("OK    " if ok else "FEHLT ") + name, detail, flush=True)


class FakeTask(QWidget):
    """Steht für einen Task-Knopf am unteren Rand; ohne eigene Taskbar, das Programm läuft ja parallel."""

    def __init__(self, name: str, windows: list, x: int):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.windows = windows
        self.title = name
        self.dock = SimpleNamespace(edge="bottom")
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(x, area.bottom() - 40, 40, 40)
        self.show()

    def display_name(self) -> str:
        return self.title


def steps(ctx, app):
    windows = win_api.list_windows()
    groups: dict[str, list] = {}
    for window in windows:
        groups.setdefault(window.exe, []).append(window)
    ordered = sorted(groups.values(), key=len, reverse=True)
    many, few = ordered[0], ordered[-1]
    print("Viele:", len(many), "Wenige:", len(few))
    area = QGuiApplication.primaryScreen().availableGeometry()
    a = FakeTask("Viele Fenster", many, area.center().x() - 300)
    b = FakeTask("Ein Fenster mit einem sehr langen Namen zum Testen", few, area.center().x() + 300)
    preview = ctx.task_preview
    preview.hover(a)
    yield 1000
    shot = lambda name: preview.screen().grabWindow(
        0, preview.x() - preview.screen().geometry().x(), preview.y() - preview.screen().geometry().y(),
        preview.width(), preview.height()).save(str(out / f"{name}.png"))
    shot("erste")
    for round_number in range(3):
        for task, name in ((b, "wenige"), (a, "viele")):
            preview.unhover()
            preview.hover(task)
            yield 500
            count = min(len(task.windows), MAX_CARDS)
            expected = count * CARD_WIDTH + (count - 1) * 6 + 20
            ok = preview.width() == expected
            ratio = preview.devicePixelRatio()
            # Geprüft wird das Rechteck, das DWM wirklich bekommen hat, nicht die spätere Karte.
            headers = preview.kill.geometry().bottom() * ratio
            cards_ok = len(preview.placed) == len(preview._cards) and all(
                c.thumb.width() >= CARD_WIDTH - 20 and c.thumb.height() == THUMB_HEIGHT for c in preview._cards)
            inside = all(rect[1] > headers and rect[2] <= preview.width() * ratio
                         and rect[3] <= preview.height() * ratio
                         and rect[2] - rect[0] <= CARD_WIDTH * ratio for rect in preview.placed)
            if round_number == 2:
                check(f"{name}: Größe passt", ok, f"{preview.width()} × {preview.height()}")
                check(f"{name}: Live-Bilder im richtigen Feld", cards_ok and inside, f"{len(preview._cards)} Karten")
                check(f"{name}: Task beenden ganz sichtbar",
                      preview.kill.geometry().right() <= preview.width() - 4, str(preview.kill.geometry().right()))
                shot(name)
    preview.dismiss()
    yield 500
    a.close()
    b.close()
    app.quit()


def main() -> int:
    faulthandler.dump_traceback_later(30, exit=True)
    app = popwin_main.create_application([])
    ctx = popwin_main.PopWinApp(ConfigManager(Path(tempfile.mkdtemp()) / "settings.json"))
    sequence = steps(ctx, app)

    def advance():
        try:
            QTimer.singleShot(next(sequence), advance)
        except StopIteration:
            pass
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()

    QTimer.singleShot(300, advance)
    app.exec()
    print("BESTANDEN" if not failed and not errors else "FEHLGESCHLAGEN", "Codefehler:", len(errors))
    for error in errors[:4]:
        print(error)
    sys.stdout.flush()
    os._exit(1 if failed or errors else 0)


if __name__ == "__main__":
    main()
