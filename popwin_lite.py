"""PopWin Lite: nur das Nötigste. Taskleiste, Uhr und Systemanzeige, Startmenü und Suche.

Start: python popwin_lite.py   (oder pythonw popwin_lite.py)

Nova, Widgets, Erweiterungen, Wallpaper, Desktop-Gruppen, Updates und der Explorer werden hier gar nicht
erst gebaut. Abgeschaltete Teile sind ein stiller Platzhalter (_Off), damit kein Aufruf abstürzt.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import main as full
from config.config_manager import ConfigManager
from config import config_manager
from core import win_api
from core.app_index import AppIndex
from core.media_controller import MediaController
from core.monitor_manager import MonitorManager
from core.paths import app_shortcut
from core.process_trim import ProcessTrimmer
from core.quick_answers import ClipHistory, FileIndex
from core.quick_settings import FocusTimer
from core.shell_hooks import ShellHooks
from core.system_status import SystemStatus
from core.theme_manager import ThemeManager
from core.notifications import NotificationWatcher
from ui.base import ForegroundWatch
from ui.context_menu import ContextMenu
from ui.notification_panel import NotificationPanel
from ui.search_launcher import SearchLauncher
from ui.settings_window import SettingsWindow
from ui.start_menu import StartMenu
from ui.task_panel import TaskController
from ui.task_preview import TaskPreview
from core.schedule import mode_for, parse_clock  # noqa: F401  (Zeitplan für Hell/Dunkel)

ROOT = full.ROOT


def _noop(*_args, **_kwargs):
    """Ein echter Python-Aufruf, den Qt-Signale verbinden dürfen: tut nichts."""
    return None


# Namen, die Oberfläche und Leisten als Funktion aufrufen oder mit Qt verbinden
_CALLABLE_NAMES = {"reveal", "show_artifact", "toggle", "new_chat", "ask", "remove", "open_gallery",
                   "close_engine", "add_file", "stop", "start", "sync", "shutdown"}
# Namen, deren Aufruf eine Liste liefern muss (die Oberfläche iteriert darüber)
_LIST_NAMES = {"names", "items", "keys", "values", "list", "entries", "all", "history"}


class _OffList(list):
    """Leere Liste, die sich auch wie eine Funktion aufrufen lässt (liefert wieder eine leere Liste)."""

    def __call__(self, *_args, **_kwargs):
        return _OffList()

    def __getattr__(self, name):
        return _Off().__getattr__(name)


class _Off:
    """Platzhalter für ein abgeschaltetes Teil. Aufrufe tun nichts; Daten sind leer."""

    def __getattr__(self, name):
        if name in _CALLABLE_NAMES:
            return _noop
        if name in _LIST_NAMES:
            return _OffList()
        return _Off()

    def __call__(self, *_args, **_kwargs):
        return _Off()

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __reversed__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


class LitePopWinApp(full.PopWinApp):
    def __init__(self, config: ConfigManager | None = None):
        QObject.__init__(self)
        self.config = config or ConfigManager()
        full.i18n.install(QApplication.instance(), self.config.get("language"))
        self.theme = ThemeManager(self.config)
        self.monitors = MonitorManager()
        self.status = SystemStatus(self)
        self.status.check_online = lambda: self.config.get("privacy.online_check")
        self.media = MediaController(self)
        self.apps = AppIndex(self)
        self.focus = FocusTimer(self)
        self.last_media: dict | None = None
        self.bars: list = []
        self.hooks: ShellHooks | None = None
        self._game_focused = False
        self._watchdog = None
        self._closed = False
        self._exiting = False
        self.restart_requested = False
        self._started = False
        self._taskbar_hidden = False
        self._windows_cache: list = []
        self._windows_time = 0.0
        self._fullscreen_cache: dict = {}

        self.config.set("mode", win_api.system_mode(), notify=False)
        self.media.updated.connect(self._remember_media)
        self.status.updated.connect(self._follow_system_mode)

        self.files = FileIndex([])
        self.clips = ClipHistory()
        QGuiApplication.clipboard().dataChanged.connect(self._remember_clip)
        self.task_preview = TaskPreview(self)
        self.tasks = TaskController(self)
        self.nova = _Off()  # Lite: kein Nova
        self.nova_panel = _Off()
        self.launcher = SearchLauncher(self)
        self.notifications = NotificationWatcher(self)
        self.notification_panel = NotificationPanel(self)
        self.start_menu = StartMenu(self)
        self.context_menu = ContextMenu(self)
        self.widgets = _Off()
        self.extensions = _Off()
        self.speed = _Off()
        self.desktop_groups = _Off()
        self.wallpaper = _Off()
        self.settings = SettingsWindow(self)
        self.updates = _Off()
        self.update_dialog = None

        self._hook_timer = QTimer(self, singleShot=True, interval=300)
        self._hook_timer.timeout.connect(self._restart_hooks)
        self._taskbar_guard = QTimer(self, interval=500)
        self._taskbar_guard.timeout.connect(self._keep_taskbar_hidden)
        self._scheduled_mode = ""
        self._mode_timer = QTimer(self, interval=60_000)
        self._mode_timer.timeout.connect(self._follow_clock)
        self.config.changed.connect(lambda key: key.startswith("auto_mode") and self._sync_mode_clock())
        self.trimmer = ProcessTrimmer(self.config, self)
        self.config.changed.connect(self._on_config)
        self._screens_timer = QTimer(self, singleShot=True, interval=800)
        self._screens_timer.timeout.connect(self._screens_settled)
        self.monitors.screens_changed.connect(self._screens_timer.start)

    # Lite hat kein Nova, daher nie Nova-Funktionen anbieten
    def nova_enabled(self) -> bool:
        return False

    def start(self) -> None:
        try:
            win_api.ensure_start_menu_entry(*app_shortcut("main"), str(ROOT / "assets" / "popwin.ico"))
            win_api.move_autostart_link(*app_shortcut("main"))
        except Exception as exc:
            full.log.warning("Startmenü-Eintrag nicht anlegbar: %s", exc)
        config_manager.backup_settings(self.config.path)
        self._sync_mode_clock()
        self.status.start()
        self.media.start()
        self.apps.start()
        self.notifications.start()
        self._started = True
        if not self.config.get("taskbar.windows_pins_imported"):
            self.import_windows_pins()
        leftover = self.config.get("shell.saved_taskbar_state") is not None
        self._apply_native_taskbar()
        if leftover and not self._taskbar_hidden:
            self._restore_native_taskbar()
        self.rebuild_bars()
        self.trimmer.sync()
        ForegroundWatch.get().changed.connect(self._on_foreground)
        self._restart_hooks()
        if self.config.get("first_run"):
            self.config.set("first_run", False, notify=False)
            QTimer.singleShot(600, self.show_setup)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._taskbar_hidden and not self.restart_requested:
            self._restore_native_taskbar()
        for bar in self.bars:
            bar.undock()
        if self.hooks:
            self.hooks.stop()
        self.status.stop()
        self.media.stop()
        self.notifications.stop()
        self.launcher.shutdown()
        self.apps.wait(1000)


def main() -> int:
    """Dasselbe wie PopWin, nur mit der Lite-Klasse."""
    full.LOG_DIR.mkdir(parents=True, exist_ok=True)
    full._setup_logging()
    app = full.create_application(sys.argv)
    popwin = LitePopWinApp()
    app.aboutToQuit.connect(popwin.shutdown)
    popwin.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
