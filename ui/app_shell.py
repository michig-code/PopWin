"""Gemeinsamer Start der eigenständigen PopWin-Apps (Editor, Terminal, PowerShell Studio)."""
from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from config.config_manager import ConfigManager
from core import i18n, win_api
from core.theme_manager import ThemeManager


SMOKE_MS = 2500


class AppContext:
    """Was die gemeinsamen PopWin-Bausteine brauchen, ohne das ganze PopWin zu starten."""

    def __init__(self):
        self.config = ConfigManager()
        self.theme = ThemeManager(self.config)
        self.config.data["mode"] = win_api.system_mode()

    @staticmethod
    def is_exiting() -> bool:
        return False


def run_app(name: str, title: str, create_window) -> int:
    """create_window bekommt den Kontext und die Argumente und liefert das Hauptfenster."""
    # Eigene Kennung: Windows gruppiert das Fenster sonst unter Python.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"PopWin.{name}")
    app = QApplication(sys.argv)
    app.setApplicationName(title)
    context = AppContext()
    i18n.install(app, context.config.get("language"))
    window = create_window(context, sys.argv[1:])
    if os.environ.get("POPWIN_SMOKE"):  # Build-Test: Läuft die App? Das Fenster bleibt unsichtbar.
        QTimer.singleShot(SMOKE_MS, lambda: (window.close(), app.quit()))
        return app.exec()
    window.show()
    return app.exec()
