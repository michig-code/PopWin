"""PopWin - Entry-Point, App-Initialisierung und Safe-Exit."""
from __future__ import annotations

from core.dev import is_dev  # [Antigravity]

import atexit
import ctypes
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    # Installiert ist alles eine PopWin.exe: Die Apps dürfen PopWin selbst nicht mitstarten.
    from core.paths import app_from_arguments
    _app, _arguments = app_from_arguments(sys.argv[1:])
    if _app != "main":
        import importlib
        sys.argv = sys.argv[:1] + _arguments
        _module = {"explorer": "explorer.app", "editor": "editor.app", "terminal": "terminal.app",
                   "studio": "studio.app", "wallpaper": "wallpaper.app", "cleanup": "core.cleanup", "stream": "stream.app", "watchdog": "core.taskbar_watchdog",
                   "nova_tool": "core.nova_tool_runner"}[_app]
        sys.exit(importlib.import_module(_module).main())

# Muss vor Qt im Hauptthread geladen werden: comtypes meldet COM beim Beenden im Hauptthread ab.
# Beim ersten Import in einem Hintergrund-Thread entzieht das Qt die COM-Initialisierung -> Absturz.
import comtypes  # noqa: E402,F401
from PySide6.QtCore import QObject, QTimer  # noqa: E402
from PySide6.QtGui import QCursor, QFont, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config.config_manager import ConfigManager  # noqa: E402
from core import hardware, i18n, net_stats, paths, wallpapers, win_api, windows_pins  # noqa: E402
from core.version import VERSION  # noqa: E402
from core.paths import LOG_DIR, app_command, app_shortcut  # noqa: E402
from core.app_index import AppIndex  # noqa: E402
from core.media_controller import MediaController  # noqa: E402
from core.process_trim import ProcessTrimmer
from core.monitor_manager import MonitorManager, screen_key  # noqa: E402
from core.nova_controller import NovaController  # noqa: E402
from ui.nova_panel import NovaPanel  # noqa: E402
from core.quick_settings import FocusTimer  # noqa: E402
from core.shell_hooks import ShellHooks  # noqa: E402
from core.system_status import SystemStatus  # noqa: E402
from core.theme_manager import ThemeManager  # noqa: E402
from ui.animation import animation_duration  # noqa: E402
from ui import base  # noqa: E402
from ui.base import ForegroundWatch  # noqa: E402
from ui.context_menu import ContextMenu  # noqa: E402
from ui.desktop_groups import DesktopGroups  # noqa: E402
from ui.search_launcher import SearchLauncher  # noqa: E402
from core.notifications import NotificationWatcher  # noqa: E402
from extensions import ExtensionManager  # noqa: E402
from ui.notification_panel import NotificationPanel  # noqa: E402
from ui.settings_window import SettingsWindow  # noqa: E402
from ui.update_dialog import UpdateDialog  # noqa: E402
from core.updater import UpdateChecker  # noqa: E402
from ui.start_menu import StartMenu  # noqa: E402
from ui.task_panel import TaskController  # noqa: E402
from ui.task_preview import TaskPreview  # noqa: E402
from ui.taskbar import Dock, merge_pins, pin_for_path  # noqa: E402
from ui.topbar import TopBar  # noqa: E402
from ui.widgets import WidgetManager  # noqa: E402
from ui import sidebar  # noqa: E402  # [Antigravity]

log = logging.getLogger("popwin")
PID_FILE = LOG_DIR / "popwin.pid"
_MUTEX_NAME = "PopWin.SingleInstance"
SESSION_RETRY_MS = 30000
UPDATE_DELAY_MS = 20000  # erst wenn alles steht; die Suche fragt GitHub
ERROR_ALREADY_EXISTS = 183


class PopWinApp(QObject):
    """Hält alle Teile zusammen; die Module bekommen diese Klasse als `ctx`."""

    def __init__(self, config: ConfigManager | None = None):
        super().__init__()
        self.config = config or ConfigManager()
        i18n.install(QApplication.instance(), self.config.get("language"))
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
        self._game_focused = False  # ein Vollbild-Spiel hat gerade den Fokus
        self._watchdog: subprocess.Popen | None = None
        self._closed = False
        self._exiting = False
        self.restart_requested = False
        self._started = False
        self._taskbar_hidden = False
        self._windows_cache: list[win_api.WindowInfo] = []
        self._windows_time = 0.0

        self.config.set("mode", win_api.system_mode(), notify=False)
        self.media.updated.connect(self._remember_media)
        self.status.updated.connect(self._follow_system_mode)

        self.task_preview = TaskPreview(self)
        self.tasks = TaskController(self)
        self.nova = NovaController(self)
        self.launcher = SearchLauncher(self)
        self.notifications = NotificationWatcher(self)
        self.notification_panel = NotificationPanel(self)
        self.nova_panel = NovaPanel(self)
        self.start_menu = StartMenu(self)
        self.context_menu = ContextMenu(self)
        self.widgets = WidgetManager(self)
        self.extensions = ExtensionManager(self)
        self.speed = net_stats.SpeedHistory(self)
        self.desktop_groups = DesktopGroups(self)
        self.settings = SettingsWindow(self)
        self.updates = UpdateChecker(self.config, self)
        self.update_dialog: UpdateDialog | None = None
        self.updates.found.connect(self._offer_update)

        self._hook_timer = QTimer(self, singleShot=True, interval=300)
        self._hook_timer.timeout.connect(self._restart_hooks)
        self._taskbar_guard = QTimer(self, interval=500)
        self._taskbar_guard.timeout.connect(self._keep_taskbar_hidden)

        self.wallpaper = wallpapers.WallpaperPlayer(self)
        self.trimmer = ProcessTrimmer(self.config, self)
        self.config.changed.connect(self._on_config)
        self.config.changed.connect(lambda key: key == "desktop_groups.style" and self.desktop_groups.sync())
        self.config.changed.connect(lambda key: key.startswith("wallpaper.") and self.wallpaper.sync())
        self.status.updated.connect(lambda _state: self.wallpaper.sync())
        # Windows meldet einen Monitorwechsel in mehreren Schritten, dazwischen stehen sterbende Bildschirme
        # noch in der Liste. Erst bauen, wenn es ruhig ist.
        self._screens_timer = QTimer(self, singleShot=True, interval=800)
        self._screens_timer.timeout.connect(self._screens_settled)
        self.monitors.screens_changed.connect(self._screens_timer.start)

    def start(self) -> None:
        try:
            win_api.ensure_start_menu_entry(*app_shortcut("main"), str(ROOT / "assets" / "popwin.ico"))
            win_api.move_autostart_link(*app_shortcut("main"))
        except Exception as exc:  # die Oberfläche darf daran nie scheitern
            log.warning("Startmenü-Eintrag nicht anlegbar: %s", exc)
        self.status.start()
        self.media.start()
        self.apps.start()
        self.notifications.start()
        self.nova.start()
        # Zuerst die Windows-Taskleiste: Sonst docken die Leisten über ihr an und rutschen danach nach.
        self._started = True
        if not self.config.get("taskbar.windows_pins_imported"):
            self.import_windows_pins()
        leftover = self.config.get("shell.saved_taskbar_state") is not None  # nach einem Neustart noch versteckt
        self._apply_native_taskbar()
        if leftover and not self._taskbar_hidden:
            self._restore_native_taskbar()
        self.rebuild_bars()
        self.widgets.start()
        self.trimmer.sync()
        self.desktop_groups.sync()
        self.extensions.start()
        ForegroundWatch.get().changed.connect(self._on_foreground)
        self._restart_hooks()
        QTimer.singleShot(UPDATE_DELAY_MS, self.updates.check)  # auch nach "PopWin neu starten"
        if self.config.get("first_run"):
            self.config.set("first_run", False, notify=False)
            self.config.set("updates.seen", VERSION, notify=False)
            QTimer.singleShot(600, self.show_setup)
        else:
            QTimer.singleShot(1500, self.show_whats_new)

    def show_setup(self) -> None:
        """Die animierte Einrichtung; auch später über Einstellungen → Setup zu öffnen."""
        from ui.intro_screens import SetupWizard
        if getattr(self, "_intro", None) is not None and self._intro.isVisible():
            self._intro.raise_()
            return
        self._intro = SetupWizard(self)
        self._intro.destroyed.connect(lambda: setattr(self, "_intro", None))
        self._intro.present()

    def show_whats_new(self, force: bool = False) -> bool:
        """Nach einem Update einmal die Neuerungen in kurzen Stichpunkten; `force` zeigt die neueste Version erneut."""
        from ui.intro_screens import WhatsNew, changelog_sections, sections_since
        seen = self.config.get("updates.seen", "")
        if seen == VERSION and not force:
            return False
        self.config.set("updates.seen", VERSION, notify=False)
        try:
            text = (paths.ROOT / "CHANGELOG.md").read_text(encoding="utf-8-sig")
        except OSError:
            return False
        sections = sections_since(changelog_sections(text), "" if force else seen)
        if not sections:
            return False
        self._news = WhatsNew(self, sections)
        self._news.present()
        return True

    # --- Leisten ------------------------------------------------------------
    def _wanted_bars(self) -> dict[tuple, tuple]:
        """(Monitor, Leiste, Rand) -> (Bildschirm, Klasse, Stil) laut Einstellungen."""
        wanted = {}
        for screen in self.monitors.screens():
            key = screen_key(screen)
            primary = self.monitors.is_primary(screen)
            state = self.config.monitor(key)
            for kind, cls in (("topbar", TopBar), ("taskbar", Dock)):
                if self.config.bar_on(kind, key, primary):
                    edge = sidebar.effective_edge(state[f"{kind}_position"]) if kind == "taskbar" else state[f"{kind}_position"]  # [Antigravity: Sidebar Support]
                    wanted[(key, cls, edge)] = (screen, cls, state["theme"])
        return wanted

    def rebuild_bars(self, recreate: bool = False) -> None:
        """Ändert nur, was sich wirklich geändert hat.

        Jede An- und Abmeldung einer AppBar lässt Fenster springen; viele davon auf einmal
        blockieren sogar den Explorer. Ein reiner Stilwechsel kommt deshalb ganz ohne aus.
        `recreate` ist für Monitorwechsel: Dann zeigen die Leisten auf ungültige Bildschirme.
        """
        wanted = {} if recreate else self._wanted_bars()
        kept = {}
        for bar in self.bars:
            slot = (bar.screen_id, type(bar), bar.edge)
            if slot in wanted:
                kept[slot] = bar
            else:
                bar.undock()
                bar.deleteLater()
        if recreate:
            wanted = self._wanted_bars()
        self.bars = []
        added = []
        for slot, (screen, cls, theme) in wanted.items():
            bar = kept.get(slot)
            if bar is None:
                try:
                    bar = cls(self, screen, slot[2], theme)
                    self.bars.append(bar)  # sofort eintragen: Sonst liefe sie bei einem Fehler herrenlos weiter
                    bar.dock()
                    added.append(bar)
                except Exception:  # eine kaputte Leiste darf die übrigen nicht mitnehmen
                    log.exception("Leiste für %s nicht angelegt", slot[0])
                continue
            if bar.theme_name != theme:
                bar.set_theme_name(theme)
            self.bars.append(bar)
        # Erst wenn alle stehen: Das Andocken blockiert, einzeln würde die Animation ruckeln.
        for bar in added:
            bar.reveal()

    def windows(self) -> list[win_api.WindowInfo]:
        """Kurz zwischengespeichert: Jedes Dock fragt das sonst pro Monitor einzeln ab."""
        now = time.monotonic()
        if now - self._windows_time > 0.3:
            self._windows_cache = win_api.list_windows() + self._own_windows()
            self._windows_time = now
        return self._windows_cache

    def _own_windows(self) -> list[win_api.WindowInfo]:
        """list_windows lässt PopWin aus, die Einstellungen sollen aber wie eine App in der Taskbar stehen."""
        windows = [("settings", self.settings), ("widgets", self.widgets.gallery)]
        # --- [Antigravity Start] Dev-Menü auf der Taskbar anzeigen ---
        if is_dev():
            dev_menu = getattr(sys.modules.get("devtools.menu"), "_menu", None)
            if dev_menu is not None:
                windows.append(("dev", dev_menu))
        # --- [Antigravity Ende] ---
        return [win_api.WindowInfo(int(window.winId()), window.windowTitle(), sys.executable, os.getpid(), popwin_app=app)
                for app, window in windows if window is not None and window.isVisible()]

    def _on_config(self, key: str) -> None:
        if key == "monitors":
            self.rebuild_bars()
            self._apply_native_taskbar()
        elif key in ("launcher.hotkey", "launcher.override_win_s", "context_menu.enabled", "start_menu.win_key",
                     "nova.hotkey", "nova.enabled"):
            self._hook_timer.start()

    def _restart_hooks(self) -> None:
        if self.hooks:
            self.hooks.stop()
        self.hooks = ShellHooks(self.config.get("launcher.hotkey"), self.config.get("context_menu.enabled"),
                                self.config.get("launcher.override_win_s"),
                                self.config.get("start_menu.win_key") != "windows",
                                self.config.get("nova.hotkey") if self.nova_enabled() else "", self)
        self.hooks.launcher_requested.connect(self.launcher.toggle)
        self.hooks.win_key_tapped.connect(self._on_win_key)
        self.hooks.nova_requested.connect(self.nova.toggle)
        self.hooks.kill_requested.connect(self.disable_ui)
        self.hooks.desktop_right_click.connect(lambda: self.show_context_menu(QCursor.pos(), desktop=True))
        self.hooks.hotkey_active.connect(self.settings.show_active_hotkey)
        self.hooks.pause_mouse(self._game_focused)
        self.hooks.start()

    def _on_foreground(self, hwnd: int) -> None:
        """Hat ein Vollbild-Spiel den Fokus, läuft keine einzige Mausbewegung mehr durch PopWin."""
        self._game_focused = win_api.is_fullscreen_window(hwnd)
        if self.hooks:
            self.hooks.pause_mouse(self._game_focused)

    def _screens_settled(self) -> None:
        """Spiele mit eigener Auflösung (etwa „gestreckt“ in Fortnite) melden das als Monitorwechsel.
        Alle Leisten neu anzumelden blockiert dann mitten im Spiel sekundenlang und verschiebt den Arbeitsbereich,
        deshalb erst, wenn das Vollbild weg ist. Bis dahin liegen die Leisten ohnehin hinter dem Spiel."""
        if win_api.foreground_is_fullscreen():
            self._screens_timer.start()
            return
        self.rebuild_bars(recreate=True)

    # --- Aktionen für die Module -----------------------------------------------
    def show_launcher(self) -> None:
        self.launcher.open()

    def toggle_start_menu(self, anchor) -> None:
        self.start_menu.animator.length = 0  # per Klick in normalem Tempo
        self.start_menu.toggle(anchor)

    def _on_win_key(self) -> None:
        if self.config.get("start_menu.win_key") == "launcher":
            self.launcher.toggle()
            return
        # An der Taskbar des gewählten Bildschirms; gibt es dort keine, frei in der Mitte unter der Maus.
        dock = self._home_dock()
        # Im Spiel geht alles schneller, beim Öffnen wie beim Zurückklicken.
        self.start_menu.animator.length = base.QUICK_MS if dock is not None and dock._fullscreen else 0
        peek = dock is not None and not self.start_menu.isVisible()
        if self.start_menu.isVisible() or dock is None or not dock.option("show_start"):
            self.start_menu.toggle(None if dock is None or not dock.option("show_start") else dock.start)
        else:
            dock.show_then(lambda: self.start_menu.toggle(dock.start))
        if peek:
            # Erst nach dem Home-Menü: Dann hat PopWin den Vordergrund, und Leisten und Menü starten ihre Animation
            # im selben Augenblick. Vorher kamen die Leisten hinter dem Spiel ein Stück später nach.
            self._peek_bars(dock.bar_screen)

    def _peek_bars(self, screen) -> None:
        for bar in self.bars:  # im Vollbild lagen die Leisten hinter dem Spiel oder Video
            if bar.bar_screen == screen:
                bar.peek()

    def _home_dock(self) -> Dock | None:
        docks = [bar for bar in self.bars if isinstance(bar, Dock)]
        if self.config.get("start_menu.win_key_screen") == "cursor":
            screen = QGuiApplication.screenAt(QCursor.pos())
            return next((dock for dock in docks if dock.bar_screen == screen), None)
        return next((dock for dock in docks if self.monitors.is_primary(dock.bar_screen)), None)

    def nova_enabled(self) -> bool:
        """Wer Nova im Setup abgelehnt hat, sieht sie nirgends außer in den Einstellungen."""
        return bool(self.config.get("nova.enabled", True))

    def open_settings(self) -> None:
        self.settings.open_page()

    def open_widget_settings(self) -> None:
        self.widgets.open_gallery()

    def has_feature(self, name: str) -> bool:
        return not self.config.get("hide_missing_hardware") or hardware.available(self.status.state["hardware"], name)

    def open_settings_at(self, page: str) -> None:
        self.settings.open_named(page)

    def open_explorer(self, path: str = "") -> None:
        self.open_app("explorer", *([path] if path else []))

    def open_app(self, name: str, *arguments: str) -> None:
        subprocess.Popen(app_command(name, *arguments), cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)

    def import_windows_pins(self) -> int:
        """Übernimmt die Apps der Windows-Taskleiste; bereits vorhandene bleiben unverändert."""
        shortcuts, store_ids = windows_pins.pinned_paths()
        new = [pin_for_path(p) for p in shortcuts] + [windows_pins.store_pin(a) for a in store_ids]
        pinned = self.config.get("taskbar.pinned")
        merged = merge_pins(pinned, new)
        self.config.data["taskbar"]["windows_pins_imported"] = True
        self.config.set("taskbar.pinned", merged)
        return len(merged) - len(pinned)

    def show_context_menu(self, position, desktop: bool) -> None:
        self.context_menu.show_at(position, desktop)

    def primary_geometry(self):
        return QGuiApplication.primaryScreen().availableGeometry()

    def set_dark_mode(self, mode: str) -> None:
        win_api.set_system_mode(mode)
        self.theme.set_mode(mode)

    def _remember_media(self, media) -> None:
        self.last_media = media

    def _follow_system_mode(self, _state) -> None:
        mode = win_api.system_mode()
        if mode != self.theme.mode:
            self.theme.set_mode(mode)

    # --- Native Taskleiste ------------------------------------------------------
    def set_native_taskbar_hidden(self, hidden: bool) -> None:
        self.config.set("shell.hide_native_taskbar", hidden, notify=False)
        self._apply_native_taskbar()

    def _apply_native_taskbar(self) -> None:
        """Ausgeblendet, solange PopWin läuft - aber nie, wenn es gar keine eigene Taskbar gibt."""
        if not self._started:
            return
        has_dock = any(cls is Dock for _screen, cls, _theme in self._wanted_bars().values())
        hide = bool(self.config.get("shell.hide_native_taskbar")) and has_dock
        if hide == self._taskbar_hidden:
            return
        self._taskbar_hidden = hide
        if hide:
            # Muss vor dem Ausblenden gespeichert sein, der Wächter liest ihn nach einem Absturz.
            # Steht schon einer da, kommt PopWin aus einem Neustart: Die Leiste ist noch versteckt, der alte Wert ist der echte.
            if self.config.get("shell.saved_taskbar_state") is None:
                self.config.set("shell.saved_taskbar_state", win_api.taskbar_state(), notify=False)
            self._start_watchdog()
            win_api.hide_native_taskbar()
            self._taskbar_guard.start()
        else:
            self._restore_native_taskbar()

    def _restore_native_taskbar(self) -> None:
        self._taskbar_guard.stop()
        win_api.show_native_taskbar(self.config.get("shell.saved_taskbar_state"))
        self.config.set("shell.saved_taskbar_state", None, notify=False)

    def session_ending(self) -> None:
        """Beim Herunterfahren beendet Windows PopWin, bevor es aufräumen kann. Auto-Hide speichert Windows aber dauerhaft,
        und nach dem nächsten Anmelden fehlte die Taskleiste, bis PopWin da ist. Deshalb sofort zurückstellen.
        Bricht man das Herunterfahren ab, blendet PopWin sie kurz danach wieder aus."""
        if self._taskbar_hidden:
            self._taskbar_hidden = False
            self._restore_native_taskbar()
            QTimer.singleShot(SESSION_RETRY_MS, self._apply_native_taskbar)

    def _keep_taskbar_hidden(self) -> None:
        # Der Explorer blendet sie nach einem Neustart oder Monitorwechsel wieder ein.
        win_api.reinforce_taskbar_hidden()

    def _start_watchdog(self) -> None:
        if self._watchdog is None or self._watchdog.poll() is not None:
            self._watchdog = subprocess.Popen(
                app_command("watchdog", str(win_api.kernel32.GetCurrentProcessId())),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)

    # --- Safe Exit --------------------------------------------------------------
    def disable_ui(self) -> None:
        """Kill-Switch: blendet alles aus, räumt auf und beendet PopWin."""
        if self._exiting:
            return
        self._exiting = True
        for window in [*self.bars, *self.widgets.windows.values()]:
            window.animator.leave()
        delay = animation_duration(self.config.get("animations"), self.config.get("performance.level", "balanced")) + 40
        QTimer.singleShot(delay, self._finish_exit)

    def _offer_update(self, release: dict) -> None:
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
        self.update_dialog.offer(release)

    def restart_ui(self) -> None:
        """Beendet PopWin wie der Kill-Switch; main() startet es danach neu.

        Offene Fenster wie der PopWin Explorer bleiben, wie sie sind: Ein Start von PopWin öffnet nie von selbst ein Fenster,
        nur beim allerersten Mal die Einstellungen auf der Setup-Seite.
        """
        self.restart_requested = True
        self.disable_ui()

    def is_exiting(self) -> bool:
        return self._exiting or self._closed

    def _finish_exit(self) -> None:
        self.shutdown()
        QApplication.quit()

    def shutdown(self) -> None:
        self.wallpaper.stop()
        self.desktop_groups.stop()
        if self._closed:
            return
        self._closed = True
        # Zuerst die Taskleiste: Sie muss zurückkommen, selbst wenn danach etwas schiefgeht.
        # Beim Neustart nicht: Sie nähme kurz Platz und schöbe ein laufendes Spiel (Valorant) nach oben.
        # Das neue PopWin versteckt sie gleich wieder; kommt es nicht, stellt der Wächter sie zurück.
        if self._taskbar_hidden and not self.restart_requested:
            self._restore_native_taskbar()
        for bar in self.bars:
            bar.undock()
        self.widgets.shutdown()
        self.extensions.shutdown()
        self.launcher.shutdown()
        self.nova.stop()
        if self.hooks:
            self.hooks.stop()
        self.status.stop()
        self.media.stop()
        self.notifications.stop()
        self.apps.wait(1000)


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "popwin.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.excepthook = lambda *exc: log.critical("Unbehandelter Fehler", exc_info=exc)


def create_application(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("PopWin")
    app.setQuitOnLastWindowClosed(False)
    # ClearType erzeugt auf transparenten Fenstern Farbsäume an Schrift und Symbolen.
    font = app.font()
    font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
    app.setFont(font)
    return app


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _setup_logging()
    if os.environ.get("POPWIN_SMOKE"):  # Build-Test: Alles lädt, ohne Leisten und ohne das laufende PopWin zu stören.
        app = create_application(sys.argv)
        PopWinApp()
        app.processEvents()
        log.info("Build-Test: PopWin vollständig geladen")
        logging.shutdown()
        os._exit(0)  # ohne Aufräumen: Die Fenster wurden nie gezeigt, Windows meldet dabei einen Exitcode ungleich 0
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        log.info("PopWin läuft bereits")  # nichts aufpoppen lassen, egal ob Autostart, .bat oder Startmenü
        return 0

    if win_api.AUTOSTART_FLAG in sys.argv:
        win_api.wait_for_shell()
    PID_FILE.write_text(str(win_api.kernel32.GetCurrentProcessId()), encoding="ascii")
    app = create_application(sys.argv)
    popwin = PopWinApp()
    app.aboutToQuit.connect(popwin.shutdown)
    app.commitDataRequest.connect(lambda _manager: popwin.session_ending())
    atexit.register(popwin.shutdown)
    popwin.start()
    code = app.exec()
    PID_FILE.unlink(missing_ok=True)
    ctypes.windll.kernel32.CloseHandle(mutex)
    if popwin.restart_requested:  # erst jetzt: solange der Mutex lebt, gäbe das neue PopWin sofort auf
        subprocess.Popen(app_command("main"), cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    return code


if __name__ == "__main__":
    sys.exit(main())
