"""Öffnet ein fremdes Vollbild-Fenster und prüft, dass die Leisten nur auf diesem Monitor weichen.

Nimmt für etwa 3 Sekunden den Fokus. Start: python tests/fullscreen_check.py
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication

import main as popwin_main
from config.config_manager import ConfigManager
from core import win_api

CHILD = """
import sys
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
target = QGuiApplication.screens()[int(sys.argv[1])]
w = QWidget()
w.setWindowFlags(Qt.WindowType.FramelessWindowHint)
w.setStyleSheet("background: #204060;")
w.setGeometry(target.geometry())
w.show()
w.activateWindow()
QTimer.singleShot(4000, app.quit)
app.exec()
"""


def main() -> int:
    app = popwin_main.create_application([])
    config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
    config.data["first_run"] = False
    screens = QGuiApplication.screens()
    primary = next(i for i, s in enumerate(screens) if s == QGuiApplication.primaryScreen())
    other = next(i for i in range(len(screens)) if i != primary)
    ctx = popwin_main.PopWinApp(config)
    ctx.rebuild_bars()
    here = [b for b in ctx.bars if b.bar_screen == screens[other]]
    elsewhere = [b for b in ctx.bars if b.bar_screen == screens[primary]]
    top = lambda bars: [win_api.is_topmost(int(b.winId())) for b in bars]
    results = {}

    def wait_for(condition, then, seconds: float = 12.0) -> None:
        # Auf das Ereignis warten statt auf eine feste Zeit: Das Testfenster startet je nach Last langsam.
        limit = time.monotonic() + seconds

        def poll():
            if condition() or time.monotonic() > limit:
                then()
            else:
                QTimer.singleShot(100, poll)

        poll()

    def before():
        results["vorher oben"] = top(here)
        subprocess.Popen([sys.executable, "-c", CHILD, str(other)])
        wait_for(lambda: not any(top(here)), during)

    def during():
        results["Vollbild-Monitor"] = top(here)
        results["anderer Monitor"] = top(elsewhere)
        wait_for(lambda: all(top(here)), after)

    def after():
        results["nach dem Schließen"] = top(here)
        app.quit()

    QTimer.singleShot(1000, before)
    app.exec()
    ctx.shutdown()
    for name, value in results.items():
        print(name, value)
    ok = (all(results["vorher oben"]) and not any(results["Vollbild-Monitor"])
          and all(results["anderer Monitor"]) and all(results["nach dem Schließen"]))
    print("BESTANDEN" if ok else "FEHLGESCHLAGEN")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
