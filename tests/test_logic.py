"""Reine Logik, ohne Fenster und ohne Eingriff ins System. Start: python tests/test_logic.py"""
import asyncio
import ctypes
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as popwin_main
import psutil
from PySide6.QtCore import QDate, QDateTime, QEvent, QMimeData, QPoint, QPointF, QRect, QRectF, Qt, QTime, QUrl
from PySide6.QtGui import QColor, QContextMenuEvent, QCursor, QIcon, QFontMetricsF, QGuiApplication, QImage, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QGridLayout, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)
from win32com.client import Dispatch

from config.config_manager import DEFAULTS, ConfigManager, _merge
from core import win_api
from core.app_index import App, AppIndex
from core.media_controller import RECONNECT_AFTER, MediaController
from core.monitor_manager import screen_key
from core.notifications import (
    Notice, app_name, format_age, from_filetime, local_image, parse_payload, read_notices, to_filetime, visible_notices,
)
from core.paths import app_command, app_from_arguments
from core.replacements import is_editor, is_explorer, is_studio, is_terminal, launch, taskbar_slot
from core.quick_settings import FocusTimer, format_remaining
from core import windows_pins
from core.windows_pins import ordered_shortcuts, store_apps, store_pin, unpin_verb
import explorer.app as explorer_app
from explorer.app import (
    EMPTY_PREVIEW, TORN_WINDOWS, archive_stem, read_thumbnail, tab_title, free_name, inside_itself, ordered_places, ExplorerWindow, Pane, PreviewPane, date_bucket, is_torn_out, file_category, grouped_entries, is_slow_click, type_label,
)
from core.nova_controller import LiveSpeech, SentenceCutter
from core.shell_hooks import KBDLLHOOKSTRUCT, MOD_ALT, MOD_CONTROL, ShellHooks, parse_hotkey
from core.win_api import _abgr, covers_monitor
from ui.context_menu import ContextMenu, trim_separators, unique_path
from ui.search_launcher import is_run_target, parse_mode_command, split_ai_prefix
from ui.animation import WindowAnimator, animation_duration
from editor.app import EditorWindow
from editor.widget import LANGUAGES, CodeEditor, EditorPanel, decode, highlight_rules, language_of
from stream.app import Capture, canvas_size, cursor_spot, frame_bytes, layout_rects
from studio.app import parse_commands, run_line, same_file, word_at
from studio.formatter import TOKEN_SCRIPT, FormatOptions, format_lines, joined, only_lines, same_code
from studio.debugger import quoted, BreakPoint, console_command, host_script, outline, parse_problems, read_state, run_command, write_plan
from studio.settings import SCHEMES, COLOR_LABELS, DEFAULTS as STUDIO_DEFAULTS, colors_of, load_settings
from editor.widget import BreakMark, CodeEditor
from ui.search_launcher import wants_studio
from terminal.app import clean_command
from terminal.widget import TerminalWidget, color_of, profiles, to_key_text
from ui import base
from ui.base import EDGE_LEVELS, G, IconButton, NovaButton, edge_reveals, glyph_icon, themed_menu
from ui.column_layout import ColumnLayout, column_count
from ui.monitor_map import MonitorEntry, MonitorNumbers, MonitorTile, fit_rects
from ui.notification_panel import NoticeCard
from explorer.app import PreviewPane, add_text_menu
from ui import desktop_groups, file_dialog
from ui.audio_mixer import AppRow
from ui.stat_pages import CachePanel, LineChart, gigabytes
from core.shell_hooks import KBDLLHOOKSTRUCT, VK_LWIN, VK_S, WM_KEYDOWN, ShellHooks
from ui.audio_outputs import AudioOutputs
from ui.taskbar import mark_y, pin_key_of_window, web_app_name
from ui.widgets import clear_icons
from ui.pin_editor import TopItemsEditor
from ui.quick_panel import QuickPanel
from ui.search_actions import typed_actions
from ui.nova_panel import CARD_PADDING, ApprovalCard, ChatText
from ui.pin_editor import PinEditor, TopItemsEditor, WindowsPinEditor, match_ai_answer
from ui.settings_window import SettingsWindow
from ui.start_menu import StartMenu
from ui.task_preview import TaskPreview
from ui.taskbar import (
    MEDIA_WIDTHS, SEARCH_WIDTHS, Dock, synced_order, aligned_x, format_size, in_hot_zone, merge_pins, pin_for_path, shows_all_windows,
)
from ui.widgets import WIDGET_TYPES
from ui.widgets.clock import ClockWidget
from ui.widgets.gallery import WidgetGallery
from ui.widgets.metrics import CpuWidget, NetworkWidget, format_speed
from ui.widgets.system import format_uptime
from ui.widgets.time_widgets import format_stopwatch
from ui.clock_place import PlacePicker
from ui import window_frame
from core import updater
from ui.update_dialog import UpdateDialog
from ui.dock_tools import tools_wanted
from config.config_manager import migrate_clock
from ui.topbar import TOP_ITEMS, TopBar, complete_order, format_center, format_clock

import chess

from core import audio_mixer, cleanup, hardware, i18n, net_stats, wallpapers, nova_brain, nova_chess, nova_tools, system_status
ROOT_PATH = Path(__file__).resolve().parent.parent
from core import theme_manager
from core.theme_manager import NOVA_CLASSICS, PRESETS
from terminal.widget import PADDING, TerminalWidget
from core.nova_brain import ChatLog, Memory
from core.nova_controller import NovaTurn, is_key_error, strip_images, trim_history
from core import nova_artifacts, nova_voice, nova_watch
from core.nova_artifacts import ArtifactStore
from core.nova_connections import Connections
from ui import dock_tools
from ui.nova_artifacts_view import ArtifactEditor, ArtifactsView, ConnectionForm, NovaNotice
from ui.search_actions import SpeechGate, VoiceInput
from ui.widgets.notes import NotesWidget

app = QApplication([])
CONTEXTS = []


def release_contexts(_cls=None) -> None:
    """Nach jeder Testklasse: ihre PopWin-Kontexte abbauen und freigeben. Sonst liefen am Ende der Suite Dutzende
    vollständige PopWins mit allen Timern nebeneinander, ein einziges processEvents() dauerte 190 ms, und Tests, die
    einige Sekunden auf einen Hintergrund-Thread warten, schafften ihre Frist nicht mehr."""
    import gc
    while CONTEXTS:
        context = CONTEXTS.pop()
        context.shutdown()
        context.deleteLater()
    # Fenster ohne Eltern löscht Qt nicht von selbst, auch wenn Python sie vergessen hat: Sie liefen samt Timern weiter.
    for window in QApplication.topLevelWidgets():
        window.hide()
        window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    base.PopupPanel._open_panel = None  # merkt sich klassenweit das offene Panel; das ist jetzt gelöscht
    gc.collect()


unittest.TestCase.tearDownClass = classmethod(release_contexts)


class TestClipboard:
    """Die Zwischenablage der Tests. Die echte teilt der unsichtbare Desktop mit der Sitzung des Nutzers: Ein Test
    überschrieb sonst, was der Nutzer gerade kopiert hatte, und scheiterte, wenn er währenddessen selbst kopierte."""

    def __init__(self):
        self.data = QMimeData()

    def mimeData(self, *_mode):
        return self.data

    def setMimeData(self, data, *_mode):
        self.data = data

    def clear(self, *_mode):
        self.data = QMimeData()

    def text(self, *_mode):
        return self.data.text()

    def setText(self, text, *_mode):
        self.data = QMimeData()
        self.data.setText(text)


TEST_CLIPBOARD = TestClipboard()
QGuiApplication.clipboard = staticmethod(lambda: TEST_CLIPBOARD)


explorer_app.STORE = Path(tempfile.mkdtemp()) / "explorer.json"  # Tests fassen die echten Ansichten nie an


# Was Fenster oder Fokus des Nutzers träfe, auch von einem unsichtbaren Desktop aus: AppBars teilen den Platz der ganzen Sitzung.
for _name, _stub in (("appbar_register", lambda *a: None), ("appbar_remove", lambda *a: None),
                     ("appbar_set_pos", lambda hwnd, edge, monitor, thickness, current=None: tuple(monitor)),
                     ("force_foreground", lambda *a: None), ("set_zorder", lambda *a: None), ("move_desktop_icon", lambda *a: None),
                     ("desktop_command", lambda *a: None), ("send_keys", lambda *a: None), ("own_by_desktop", lambda *a: True),
                     ("shell_open", lambda *a: True)):  # kein Test startet je ein echtes Programm, eine Datei oder Webseite
    setattr(win_api, _name, _stub)


class ResetContext(popwin_main.PopWinApp):
    """Echte Teile mit Wegwerf-Einstellungen; nichts wird gestartet oder angezeigt."""

    def __init__(self):
        config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
        keys: dict[str, str] = {}  # Tests fassen den Windows-Anmeldespeicher nie an
        config.api_key = lambda user="gemini_api_key": keys.get(user, "")
        config.set_api_key = lambda key, user="gemini_api_key": bool(keys.update({user: key}) if key else keys.pop(user, None)) or True
        config.keys = keys
        super().__init__(config)
        folder = Path(tempfile.mkdtemp())
        self.nova.memory = Memory(folder / "memory.json")  # Novas echtes Gedächtnis bleibt unberührt
        self.nova.toolbox.memory = self.nova.memory
        self.nova.use_artifacts(ArtifactStore(folder / "artifacts"))  # und ihre echten Artefakte auch
        self.config.data["nova"]["reserve_space"] = False  # sonst schrumpfte jeder Test den echten Arbeitsbereich
        self.nova.log = ChatLog(folder / "chat.json")
        self.wallpaper.sync = lambda: None  # ein Test darf nie echte Programme wie Wallpaper Engine starten oder beenden
        CONTEXTS.append(self)


class ModeCommands(unittest.TestCase):
    def test_spec_examples(self):
        self.assertEqual(parse_mode_command("whitemode aktivieren"), "light")
        self.assertEqual(parse_mode_command("darkmode deaktivieren"), "light")
        self.assertEqual(parse_mode_command("darkmode aktivieren"), "dark")
        self.assertEqual(parse_mode_command("whitemode deaktivieren"), "dark")

    def test_variants(self):
        self.assertEqual(parse_mode_command("  WhiteMode   Aktivieren "), "light")
        self.assertEqual(parse_mode_command("dark mode an"), "dark")

    def test_no_match(self):
        self.assertIsNone(parse_mode_command("darkmode"))
        self.assertIsNone(parse_mode_command("wie aktiviere ich windows"))
        self.assertIsNone(parse_mode_command("darkmode aktivieren aus"))


class Launcher(unittest.TestCase):
    def test_ai_prefix(self):
        self.assertEqual(split_ai_prefix("? hallo"), "hallo")
        self.assertEqual(split_ai_prefix("ai: hallo"), "hallo")
        self.assertEqual(split_ai_prefix("AI:hallo"), "hallo")
        self.assertIsNone(split_ai_prefix("hallo?"))

    def test_run_targets(self):
        for text in ("cmd", "regedit", "control", "mspaint", "shell:startup", "services.msc"):
            self.assertTrue(is_run_target(text), text)
        self.assertFalse(is_run_target("gibtsnichtxyz"))


class WinKey(unittest.TestCase):
    """Spielt Tastenfolgen durch die Hook-Logik, ohne einen echten Hook zu installieren."""
    WIN, D, UP = 0x5B, 0x44, 0x0101

    def setUp(self):
        self.hooks = ShellHooks("alt+space", False, False, True)
        self.hooks._win_held = lambda: True  # die Taste ist hier nur gespielt, nicht wirklich gedrückt
        self.tapped = []
        self.hooks.win_key_tapped.connect(lambda: self.tapped.append(1))
        win_api.send_keys = lambda *keys: None
        win_api.user32.CallNextHookEx = lambda *args: 0

    def press(self, message, key):
        event = KBDLLHOOKSTRUCT(vkCode=key)
        self.hooks._on_key(0, message, ctypes.addressof(event))

    def test_tap_triggers(self):
        self.press(0x100, self.WIN)
        self.press(self.UP, self.WIN)
        self.assertEqual(len(self.tapped), 1)

    def test_key_repeat_counts_once(self):
        for _ in range(5):
            self.press(0x100, self.WIN)
        self.press(self.UP, self.WIN)
        self.assertEqual(len(self.tapped), 1)

    def test_combination_stays_with_windows(self):
        self.press(0x100, self.WIN)
        self.press(0x100, self.D)
        self.press(self.UP, self.D)
        self.press(self.UP, self.WIN)
        self.assertEqual(self.tapped, [])

    def _win_s(self, modifier_held: bool) -> list:
        opened = []
        self.hooks.override_win_s = True
        self.hooks.launcher_requested.connect(lambda: opened.append(1))
        self.hooks._other_modifier_held = lambda: modifier_held
        self.press(0x100, self.WIN)
        self.press(0x100, 0x53)
        self.press(self.UP, 0x53)
        self.press(self.UP, self.WIN)
        return opened

    def test_plain_win_s_opens_the_launcher(self):
        self.assertEqual(len(self._win_s(False)), 1)

    def test_win_shift_s_stays_the_windows_screenshot(self):
        self.assertEqual(self._win_s(True), [])
        self.assertEqual(self.tapped, [])

    def test_disabled_keeps_windows_key(self):
        self.hooks.remap_win_key = False
        self.press(0x100, self.WIN)
        self.press(self.UP, self.WIN)
        self.assertEqual(self.tapped, [])


class Hotkeys(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("alt+space"), (MOD_ALT, 0x20))
        self.assertEqual(parse_hotkey("Ctrl + K"), (MOD_CONTROL, ord("K")))
        self.assertIsNone(parse_hotkey("alt"))
        self.assertIsNone(parse_hotkey("foo+bar"))


class Formats(unittest.TestCase):
    def test_clock(self):
        now = QDateTime(QDate(2026, 3, 7), QTime(9, 5, 2))
        self.assertEqual(format_clock(now), ("09:05:02", "07.03.2026"))

    def test_size(self):
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(1536), "1,5 KB")

    def test_abgr(self):
        self.assertEqual(_abgr("#11223344"), 0x44332211)


class Config(unittest.TestCase):
    def test_merge_rejects_wrong_types(self):
        merged = _merge(DEFAULTS, {"theme": 5, "context_menu": {"font_size": "x", "enabled": False}})
        self.assertEqual(merged["theme"], DEFAULTS["theme"])
        self.assertEqual(merged["context_menu"]["font_size"], DEFAULTS["context_menu"]["font_size"])
        self.assertFalse(merged["context_menu"]["enabled"])

    def test_roundtrip_and_broken_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            config = ConfigManager(path)
            config.set("launcher.gemini_model", "test")
            self.assertEqual(ConfigManager(path).get("launcher.gemini_model"), "test")
            path.write_text("{kaputt", encoding="utf-8")
            self.assertEqual(ConfigManager(path).get("launcher.gemini_model"), DEFAULTS["launcher"]["gemini_model"])
            self.assertTrue(path.with_suffix(".broken.json").exists())

    def test_key_never_in_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            ConfigManager(path).save()
            self.assertNotIn("api", json.dumps(json.loads(path.read_text(encoding="utf-8"))).lower())


class MonitorProfiles(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "settings.json"
        self.config = ConfigManager(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_all_monitors_on_and_synced_by_default(self):
        self.assertTrue(self.config.synced)
        self.assertTrue(self.config.bar_on("topbar", "A", False))
        self.assertTrue(self.config.bar_on("taskbar", "B", False))

    def test_bar_only_on_primary(self):
        self.config.set_bar_screens("topbar", "primary", [])
        self.assertTrue(self.config.bar_on("topbar", "A", True))
        self.assertFalse(self.config.bar_on("topbar", "B", False))
        self.assertTrue(self.config.bar_on("taskbar", "B", False))

    def test_bar_on_chosen_monitors(self):
        self.config.set_bar_screens("taskbar", "custom", ["B", "C"])
        self.assertFalse(self.config.bar_on("taskbar", "A", True))
        self.assertTrue(self.config.bar_on("taskbar", "B", False))
        self.config.set_bar_screens("taskbar", "custom", [])
        self.assertFalse(self.config.bar_on("taskbar", "B", False))

    def test_bar_choice_is_independent_of_sync(self):
        self.config.set_bar_screens("topbar", "custom", ["A"])
        self.config.set_monitor_mode("separate")
        self.assertFalse(self.config.bar_on("topbar", "B", False))
        self.config.set_monitor_mode("sync")
        self.assertFalse(self.config.bar_on("topbar", "B", False))

    def test_sync_changes_every_monitor(self):
        self.config.set_monitor("A", "theme", "clean")
        self.assertEqual(self.config.monitor("B")["theme"], "clean")

    def test_separate_changes_only_one_and_starts_from_shared(self):
        self.config.set_monitor(None, "icon_size", 30)
        self.config.set_monitor_mode("separate")
        self.config.set_monitor("A", "theme", "clean")
        self.assertEqual(self.config.monitor("A")["theme"], "clean")
        self.assertEqual(self.config.monitor("A")["icon_size"], 30)
        self.assertEqual(self.config.monitor("B")["theme"], "")

    def test_switching_back_keeps_individual_profiles(self):
        self.config.set_monitor_mode("separate")
        self.config.set_monitor("A", "theme", "clean")
        self.config.set_monitor_mode("sync")
        self.assertEqual(self.config.monitor("A")["theme"], "")
        self.config.set_monitor_mode("separate")
        self.assertEqual(ConfigManager(self.path).monitor("A")["theme"], "clean")

    def test_copy_to_all(self):
        self.config.set_monitor_mode("separate")
        self.config.set_monitor("A", "theme", "clean")
        self.config.set_monitor("B", "theme", "minimalist")
        self.config.copy_profile_to_all("A", ["A", "B"])
        self.assertEqual(self.config.monitor("B")["theme"], "clean")

    def test_signal_tells_bars_whether_to_rebuild(self):
        seen = []
        self.config.changed.connect(seen.append)
        self.config.set_monitor("A", "icon_size", 30)
        self.config.set_monitor("A", "theme", "clean")
        self.assertEqual(seen, ["profile", "monitors"])


class QuietStart(unittest.TestCase):
    """Ein Start von PopWin öffnet nie von selbst ein Fenster, nur beim allerersten Mal die Setup-Seite."""

    def test_first_start_shows_setup_once(self):
        ctx = ResetContext()
        opened = []
        ctx.settings.open_page = lambda setup=False: opened.append(setup)
        for name in ("_apply_native_taskbar", "rebuild_bars", "_restart_hooks", "import_windows_pins"):
            setattr(ctx, name, lambda *a: None)
        for part in (ctx.status, ctx.media, ctx.apps, ctx.notifications, ctx.widgets, ctx.extensions, ctx.nova):
            part.start = lambda *a: None
        ctx.desktop_groups.sync = lambda: None
        with unittest.mock.patch.object(win_api, "ensure_start_menu_entry"), unittest.mock.patch.object(win_api, "move_autostart_link"), \
                unittest.mock.patch("main.QTimer.singleShot"):
            ctx.start()
            self.assertEqual(opened, [True])
            ctx.start()  # jeder weitere Start: nichts
        self.assertEqual(opened, [True])

    def test_second_start_and_restart_open_nothing(self):
        source = (ROOT_PATH / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("notify_running_instance", source)
        self.assertNotIn("open_explorer()", source.split("def main", 1)[1])


class WindowsPins(unittest.TestCase):
    def test_store_apps_from_text(self):
        text = "junk SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify x Microsoft.Paint_8wekyb3d8bbwe!App"
        self.assertEqual(store_apps(text), ["SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
                                            "Microsoft.Paint_8wekyb3d8bbwe!App"])

    def test_store_pin_matches_running_exe(self):
        pin = store_pin("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
        self.assertEqual((pin["name"], pin["exe"]), ("Spotify", "spotify.exe"))
        self.assertEqual(pin["target"], "shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")

    def test_shortcut_special_cases(self):
        original = win_api.shortcut_target
        try:
            win_api.shortcut_target = lambda path: r"C:\Users\x\AppData\Local\Discord\Update.exe"
            self.assertEqual(pin_for_path(r"C:\p\Discord.lnk")["exe"], "discord.exe")
            win_api.shortcut_target = lambda path: ""
            self.assertEqual(pin_for_path(r"C:\p\File Explorer.lnk")["exe"], "explorer.exe")
            self.assertNotIn("exe", pin_for_path(r"C:\p\Etwas.lnk"))
        finally:
            win_api.shortcut_target = original

    def test_windows_order(self):
        self.assertEqual(ordered_shortcuts(["B.lnk", "A.lnk", "C.lnk"], "..C.lnk..B.lnk.."), ["C.lnk", "B.lnk", "A.lnk"])

    def test_merge_keeps_existing_and_skips_duplicates(self):
        old = [{"name": "Explorer", "target": "explorer.exe"}]
        new = [{"name": "File Explorer", "target": "x.lnk", "exe": "explorer.exe"},
               {"name": "Discord", "target": "d.lnk", "exe": "discord.exe"}]
        self.assertEqual([p["name"] for p in merge_pins(old, new)], ["Explorer", "Discord"])


class Widgets(unittest.TestCase):
    def context(self):
        ctx = ResetContext()
        ctx.widgets.windows.clear()
        return ctx

    def test_every_widget_builds_with_defaults_and_each_option(self):
        ctx = self.context()
        for kind, cls in WIDGET_TYPES.items():
            spec = {"id": kind, "type": kind, "data": {}, "options": {}}
            ctx.config.data["widgets"].append(spec)
            window = cls(ctx, spec)
            ctx.widgets.windows[kind] = window
            for option in window.all_options():
                if option.kind == "bool":
                    window.set_option(option.key, not window.option(option.key))
                elif option.kind == "int":
                    window.set_option(option.key, option.high)
                elif option.kind == "choice":
                    for value in option.choices:
                        window.set_option(option.key, value)
                else:
                    window.set_option(option.key, "#FF8800")
            self.assertTrue(window.DESCRIPTION, kind)

    def test_old_saved_widget_still_loads(self):
        ctx = self.context()
        window = ClockWidget(ctx, {"id": "alt", "type": "clock"})
        self.assertEqual(window.option("size"), 40)

    def test_option_is_saved(self):
        ctx = self.context()
        spec = {"id": "a", "type": "cpu", "data": {}}
        ctx.config.data["widgets"].append(spec)
        window = CpuWidget(ctx, spec)
        ctx.widgets.windows["a"] = window
        window.set_option("style", "graph")
        self.assertEqual(json.loads(ctx.config.path.read_text(encoding="utf-8"))["widgets"][0]["options"]["style"], "graph")

    def test_formats(self):
        self.assertEqual(format_speed(512), "512 B/s")
        self.assertEqual(format_speed(1536), "1,5 KB/s")
        self.assertEqual(format_uptime(90061), "1 T 1 Std 1 Min")
        self.assertEqual(format_stopwatch(65.5), "01:05,50")
        self.assertEqual(format_stopwatch(65.5, False), "01:05")

    def test_gallery_add_and_remove(self):
        ctx = self.context()
        gallery = WidgetGallery(ctx)
        gallery._add("gpu")
        self.assertEqual(gallery.chooser.count(), 1)
        gallery._remove(gallery.current)
        self.assertEqual(ctx.config.get("widgets"), [])
        self.assertIsNone(gallery.current)

    def test_notes_widget_expands_freely_downwards(self):
        # [Antigravity: Notiz-Widget vergrößert & verkleinert sich frei über Eckgriff inkl. Textfeld]
        from PySide6.QtGui import QMouseEvent
        ctx = self.context()
        spec = {"id": "notes_1", "type": "notes", "data": {}, "options": {"width": 260, "height": 0}}
        ctx.config.data["widgets"].append(spec)
        w = NotesWidget(ctx, spec)
        w.show()
        self.addCleanup(w.hide)
        self.assertFalse(hasattr(w, "bottom_grip"))
        initial_w = w.width()
        initial_h = w.height()
        initial_edit_h = w.edit.height()

        # 1. Vergrößern nach rechts und unten per Eckgriff (w.grip)
        grip = w.grip
        press_pos = grip.mapToGlobal(QPoint(8, 8))
        grip.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(8, 8), press_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        drag_pos = press_pos + QPoint(100, 150)
        grip.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPoint(108, 158), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

        self.assertGreater(w.width(), initial_w + 50)
        self.assertGreater(w.height(), initial_h + 100)
        self.assertGreater(w.edit.height(), initial_edit_h + 100)

        # Loslassen speichert Breite und Höhe atomar
        grip.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPoint(108, 158), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.spec["options"]["width"], w.width())
        self.assertEqual(w.spec["options"]["height"], w.height())
        enlarged_h = w.height()
        enlarged_edit_h = w.edit.height()
        self.assertGreater(enlarged_edit_h, initial_edit_h + 100)

        # 2. Wieder verkleinern per Eckgriff
        press_pos = grip.mapToGlobal(QPoint(8, 8))
        grip.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(8, 8), press_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        drag_pos = press_pos + QPoint(-50, -50)
        grip.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPoint(-42, -42), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

        self.assertLess(w.height(), enlarged_h)
        self.assertLess(w.edit.height(), enlarged_edit_h)

        grip.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPoint(-42, -42), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.spec["options"]["width"], w.width())
        self.assertEqual(w.spec["options"]["height"], w.height())

    def test_network_widget_resize_and_shrink(self):
        # [Antigravity: Netz-Tempo Widget lässt sich über Eckgriff frei vergrößern und verkleinern]
        from PySide6.QtGui import QMouseEvent
        ctx = self.context()
        spec = {"id": "net_test", "type": "network", "data": {}, "options": {}}
        ctx.config.data["widgets"].append(spec)
        w = NetworkWidget(ctx, spec)
        w.show()
        self.addCleanup(w.hide)
        initial_w = w.width()
        initial_h = w.height()
        self.assertGreater(w.gauge.height(), 0)
        self.assertTrue(w.download.text().startswith("↓"))
        self.assertTrue(w.upload.text().startswith("↑"))

        # 1. Vergrößern (+60, +70)
        grip = w.grip
        press_pos = grip.mapToGlobal(QPoint(8, 8))
        grip.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(8, 8), press_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        drag_pos = press_pos + QPoint(60, 70)
        grip.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPoint(68, 78), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        grip.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPoint(68, 78), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        enlarged_w = w.width()
        enlarged_h = w.height()
        self.assertGreater(enlarged_w, initial_w)
        self.assertGreater(enlarged_h, initial_h)
        self.assertEqual(w.spec["options"]["width"], enlarged_w)
        self.assertEqual(w.spec["options"]["height"], enlarged_h)

        # 2. Wieder verkleinern (-60, -70)
        press_pos = grip.mapToGlobal(QPoint(8, 8))
        grip.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(8, 8), press_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        drag_pos = press_pos + QPoint(-60, -70)
        grip.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPoint(-52, -62), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        grip.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPoint(-52, -62), drag_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        self.assertLess(w.width(), enlarged_w)
        self.assertLess(w.height(), enlarged_h)
        self.assertEqual(w.width(), initial_w)
        self.assertEqual(w.height(), initial_h)


class WinKeyDock(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.monitors.is_primary = lambda screen: True
        self.ctx.config.set("start_menu.win_key_screen", "primary")
        self.calls = []

    def dock(self, auto_hide: bool) -> Dock:
        self.ctx.config.set_monitor(None, "auto_hide", auto_hide)
        dock = Dock(self.ctx, app.primaryScreen(), "bottom")
        self.ctx.bars = [dock]
        self.ctx.start_menu.toggle = lambda anchor: self.calls.append(anchor)
        return dock

    def test_visible_dock_opens_home_at_once(self):
        dock = self.dock(False)
        dock.show_then(lambda: self.calls.append("sofort"))
        self.assertEqual(self.calls, ["sofort"])

    def test_hidden_dock_reveals_first_then_opens_home(self):
        dock = self.dock(True)
        dock._revealed = False
        self.ctx._on_win_key()
        self.assertTrue(dock._revealed and dock.isVisible())
        self.assertEqual(self.calls, [dock.start])  # sofort: das Menü rechnet die Verschiebung selbst heraus
        dock.hide()

    def test_open_home_keeps_the_dock(self):
        dock = self.dock(True)
        self.assertFalse(dock._keeps_open())
        self.ctx.start_menu.isVisible = lambda: True
        self.assertTrue(dock._keeps_open())

    def test_home_follows_the_cursor(self):
        here = self.dock(False)
        other = Dock(self.ctx, app.primaryScreen(), "top")
        other._screen = object()  # steht für einen anderen Bildschirm
        self.ctx.bars = [other, here]
        screen = app.primaryScreen()
        original = QGuiApplication.screenAt
        QGuiApplication.screenAt = staticmethod(lambda position: screen)
        self.ctx.config.set("start_menu.win_key_screen", "cursor")
        try:
            with unittest.mock.patch.object(popwin_main.Dock, "bar_screen", new_callable=unittest.mock.PropertyMock) as prop:
                prop.side_effect = lambda: screen
                self.assertIs(self.ctx._home_dock(), other)
            QGuiApplication.screenAt = staticmethod(lambda position: None)
            self.assertIsNone(self.ctx._home_dock())  # keine Taskbar dort: Menü frei unter der Maus
        finally:
            QGuiApplication.screenAt = original

    def test_only_the_anchoring_dock_stays_open(self):
        dock = self.dock(True)
        other = Dock(self.ctx, app.primaryScreen(), "top")
        menu = self.ctx.start_menu
        menu.isVisible = lambda: True
        menu.anchor = other.start
        self.assertTrue(other._keeps_open())
        self.assertFalse(dock._keeps_open())


class HotZone(unittest.TestCase):
    AREA = (0, 0, 1920, 1080)

    def test_zone_depth_follows_the_setting(self):
        self.assertTrue(in_hot_zone(500, 1060, self.AREA, "bottom", 24))
        self.assertFalse(in_hot_zone(500, 1050, self.AREA, "bottom", 24))
        self.assertTrue(in_hot_zone(500, 10, self.AREA, "top", 24))

    def test_other_monitor_does_not_count(self):
        self.assertFalse(in_hot_zone(2500, 1070, self.AREA, "bottom", 24))


class ExplorerGroups(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(file_category("Urlaub.JPG", False), "Bilder")
        self.assertEqual(file_category("track.flp", False), "Audio")
        self.assertEqual(file_category("Ordner.jpg", True), "Ordner")
        self.assertEqual(file_category("etwas.xyz", False), "Andere")

    def test_date_buckets(self):
        monday_noon = time.mktime((2026, 10, 28, 12, 0, 0, 0, 0, -1))  # ein Mittwoch
        self.assertEqual(date_bucket(monday_noon - 3600, monday_noon), "Heute")
        self.assertEqual(date_bucket(monday_noon - 86400, monday_noon), "Gestern")
        self.assertEqual(date_bucket(monday_noon - 2 * 86400, monday_noon), "Diese Woche")
        self.assertEqual(date_bucket(monday_noon - 10 * 86400, monday_noon), "Dieser Monat")
        self.assertEqual(date_bucket(monday_noon - 90 * 86400, monday_noon), "Früher")

    def test_grouping_a_real_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sub").mkdir()
            (root / "b.txt").touch()
            (root / "a.png").touch()
            groups = grouped_entries(folder, "type", time.time())
            self.assertEqual(list(groups), ["Ordner", "Bilder", "Dokumente"])
            self.assertEqual(list(grouped_entries(folder, "date", time.time())), ["Heute"])


class Defaults(unittest.TestCase):
    def test_default_lookup(self):
        self.assertEqual(ConfigManager.default_of("launcher.width"), 640)
        self.assertEqual(ConfigManager.default_of("context_menu.size"), "normal")

    def test_every_settings_control_can_reset(self):
        window = SettingsWindow(ResetContext())
        spins = window.findChildren(QSpinBox)
        self.assertGreater(len(spins), 5)
        for spin in spins:
            buttons = spin.parent().findChildren(IconButton)
            self.assertEqual(len(buttons), 1, spin.suffix())

    def test_dock_height_reset_returns_to_theme(self):
        ctx = ResetContext()
        window = SettingsWindow(ctx)
        ctx.config.set_monitor(None, "dock_height", 80)
        window._fill_taskbar_monitors()
        spin = next(s for s in window.taskbar_monitors.findChildren(QSpinBox) if s.value() == 80)
        spin.parent().findChild(IconButton).click()
        self.assertEqual(ctx.config.monitor()["dock_height"], 0)


class QuickSettings(unittest.TestCase):
    def test_remaining(self):
        self.assertEqual(format_remaining(1500), "25:00")
        self.assertEqual(format_remaining(61), "01:01")
        self.assertEqual(format_remaining(-3), "00:00")

    def test_focus_timer_starts_idle(self):
        timer = FocusTimer()
        self.assertFalse(timer.active)
        self.assertEqual(timer.remaining, 0)
        timer.stop()  # darf ohne laufende Sitzung nichts anfassen


class Layout(unittest.TestCase):
    def test_alignment(self):
        self.assertEqual(aligned_x("left", 100, 1100, 200), 100)
        self.assertEqual(aligned_x("center", 100, 1100, 200), 500)
        self.assertEqual(aligned_x("right", 100, 1100, 200), 900)

    def test_animation_can_be_disabled(self):
        self.assertEqual(animation_duration({"enabled": True, "duration": 220}), 220)
        self.assertEqual(animation_duration({"enabled": False, "duration": 220}), 0)


class Pins(unittest.TestCase):
    def test_file_gets_its_name(self):
        pin = pin_for_path(r"C:\Spiele\Spiel.exe")
        self.assertEqual(pin, {"name": "Spiel", "target": r"C:\Spiele\Spiel.exe"})

    def test_shortcut_remembers_program(self):
        original = win_api.shortcut_target
        win_api.shortcut_target = lambda path: r"C:\Programme\Foo\Foo.EXE"
        try:
            self.assertEqual(pin_for_path(r"C:\x\Foo App.lnk")["exe"], "foo.exe")
        finally:
            win_api.shortcut_target = original

    def test_shortcut_prefers_its_own_icon(self):
        with tempfile.TemporaryDirectory() as folder:
            icon = Path(folder) / "app.ico"
            icon.write_bytes(b"")
            link = str(Path(folder) / "Discord.lnk")
            shortcut = Dispatch("WScript.Shell").CreateShortcut(link)
            shortcut.TargetPath = sys.executable
            shortcut.IconLocation = f"{icon},0"
            shortcut.Save()
            self.assertEqual(win_api.shortcut_icon_file(link), str(icon))
            shortcut = Dispatch("WScript.Shell").CreateShortcut(link)
            shortcut.IconLocation = str(Path(folder) / "weg.ico") + ",0"
            shortcut.Save()
            self.assertEqual(win_api.shortcut_icon_file(link).lower(), sys.executable.lower())

    def test_explorer_is_replaced_until_switched_off(self):
        class Recorder:
            config = ConfigManager(Path(tempfile.mkdtemp()) / "settings.json")
            opened = []
            open_explorer = staticmethod(opened.append)

        ctx = Recorder()
        ctx.config.set("replacements.explorer", True)  # Beta, ab Werk aus
        shell_open, target = win_api.shell_open, win_api.shortcut_target
        started = []
        win_api.shell_open = lambda path: started.append(path) or True
        win_api.shortcut_target = lambda path: ""
        try:
            self.assertTrue(is_explorer(r"shell:AppsFolder\Microsoft.Windows.Explorer"))
            self.assertTrue(is_explorer(r"C:\p\File Explorer.lnk"))
            self.assertFalse(is_explorer(r"C:\p\Internet Explorer.lnk"))
            launch(ctx, "explorer.exe")
            launch(ctx, tempfile.gettempdir())
            self.assertEqual(ctx.opened, ["", tempfile.gettempdir()])
            ctx.config.set("replacements.explorer", False)
            launch(ctx, "explorer.exe")
            self.assertEqual((len(ctx.opened), started), (2, ["explorer.exe"]))
        finally:
            win_api.shell_open, win_api.shortcut_target = shell_open, target

    def test_helper_windows_belong_to_their_program(self):
        parents = {30: 20, 20: 10, 10: 5}
        paths = {20: r"C:\Steam\bin\cef\webhelper.exe", 10: r"C:\Steam\steam.exe", 5: r"C:\Windows\explorer.exe"}

        class FakeProcess:
            def __init__(self, pid):
                self.pid = pid

            def ppid(self):
                return parents[self.pid]

        original_exe, original_process = win_api.exe_of, psutil.Process
        win_api.exe_of, psutil.Process = paths.get, FakeProcess
        win_api._main_processes.clear()
        try:
            self.assertEqual(win_api._main_process(r"C:\Steam\bin\cef\webhelper.exe", 30), (r"C:\Steam\steam.exe", 10))
            self.assertEqual(win_api._main_process(r"C:\Spiele\spiel.exe", 40), (r"C:\Spiele\spiel.exe", 40))
            paths[5] = r"C:\Steam\steam.exe"
            parents[77], paths[10] = 5, r"C:\Windows\explorer.exe"
            self.assertEqual(win_api._main_process(r"D:\Apps\helper.exe", 77), (r"D:\Apps\helper.exe", 77))
        finally:
            win_api.exe_of, psutil.Process = original_exe, original_process
            win_api._main_processes.clear()
        window = win_api.WindowInfo(1, "Steam", r"C:\Steam\steam.exe", 30, 10)
        self.assertEqual((window.main_pid, window.from_helper), (10, True))
        self.assertFalse(win_api.WindowInfo(1, "App", "a.exe", 30).from_helper)

    def test_notification_payload_and_app_names(self):
        toast = ('<toast launch="x"><visual><binding template="ToastGeneric"><text>Titel</text><text>Zeile</text>'
                 '<text placement="attribution">Quelle</text></binding></visual></toast>').encode()
        self.assertEqual(parse_payload(toast), ("Titel", "Zeile", "", ""))
        self.assertEqual(parse_payload(b"kaputt"), ("", "", "", ""))
        apps = [App("Lunar Client", "shell:AppsFolder\\com.moonsworth.client")]
        self.assertEqual(app_name("com.moonsworth.client", apps), "Lunar Client")
        self.assertEqual(app_name("33C30B79.NGENUITY_922sw8z9z7n5w!App", apps), "NGENUITY")
        self.assertEqual(app_name("NVIDIA Broadcast", apps), "NVIDIA Broadcast")

    def test_notification_pictures(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "mein bild.png"
            image = QPixmap(800, 400)
            image.fill(QColor("red"))
            image.save(str(picture))
            url = picture.as_uri()
            self.assertEqual(local_image(url), str(picture))
            self.assertEqual(local_image(str(picture)), str(picture))
            self.assertEqual(local_image("https://example.com/a.png"), "")
            self.assertEqual(local_image("ms-appx:///a.png"), "")
            self.assertEqual(local_image(str(Path(folder) / "weg.png")), "")
            toast = (f'<toast><text>T</text><image placement="appLogoOverride" src="{picture}"/>'
                     f'<image placement="hero" src="{url}"/></toast>').encode()
            self.assertEqual(parse_payload(toast), ("T", "", str(picture), str(picture)))
            logo_only = f'<toast><text>T</text><image placement="appLogoOverride" src="{picture}"/></toast>'.encode()
            self.assertEqual(parse_payload(logo_only)[2:], ("", str(picture)))

            ctx = ResetContext()
            ctx.notifications.notices = [Notice(1, "App", "T", "", datetime.now().astimezone(), str(picture))]
            panel = ctx.notification_panel
            panel.rebuild()
            shown = panel.picture(str(picture))
            self.assertLessEqual(shown.deviceIndependentSize().width(), ctx.config.get("notifications.width") - 56)
            self.assertIsNone(panel.picture(""))
            self.assertIsNone(panel.picture(str(Path(folder) / "weg.png")))
            self.assertEqual(len([l for l in panel.findChildren(QLabel) if not l.pixmap().isNull()]) > 0, True)

    def test_notification_times(self):
        moment = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(from_filetime(to_filetime(moment)), moment)
        self.assertEqual(format_age(moment, moment + timedelta(seconds=30)), "Gerade eben")
        self.assertEqual(format_age(moment, moment + timedelta(minutes=5)), "vor 5 Min.")
        self.assertEqual(format_age(moment, moment + timedelta(hours=2)), "vor 2 Std.")
        self.assertEqual(format_age(moment, moment + timedelta(days=1)), "Gestern 12:00")
        self.assertEqual(format_age(moment, moment + timedelta(days=9)), "01.05. 12:00")

    def test_notification_database_is_read_from_a_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wpndatabase.db"
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE NotificationHandler (RecordId INTEGER, PrimaryId TEXT)')
            db.execute('CREATE TABLE Notification ("Order" INTEGER, HandlerId INTEGER, Type TEXT, Payload BLOB, '
                       'ArrivalTime INT64, ExpiryTime INT64)')
            db.execute("INSERT INTO NotificationHandler VALUES (1, 'App/Name ')")
            soon = to_filetime(datetime.now(timezone.utc) + timedelta(hours=1))
            toast = b"<toast><text>Hallo</text><text>Welt</text></toast>"
            db.executemany("INSERT INTO Notification VALUES (?, 1, ?, ?, ?, ?)", [
                (1, "toast", toast, soon, soon), (2, "toast", toast, soon, 5), (3, "badge", toast, soon, soon)])
            db.commit()
            db.close()
            notices = read_notices(path)
        self.assertEqual([(n.id, n.app_id, n.title, n.body) for n in notices], [(1, "App\\Name", "Hallo", "Welt")])
        self.assertEqual(visible_notices(notices, [1]), [])

    def test_notification_panel_hides_entries(self):
        ctx = ResetContext()
        moment = datetime.now().astimezone()
        ctx.notifications.notices = [Notice(1, "App", "Eins", "", moment), Notice(2, "App", "Zwei", "", moment),
                                     Notice(3, "Anders", "Drei", "Text", moment)]
        panel = ctx.notification_panel
        panel.rebuild()
        self.assertEqual(len(panel.findChildren(NoticeCard)), 3)
        panel.hide_notices([ctx.notifications.notices[0]])
        self.assertEqual(ctx.config.get("notifications.hidden"), [1])
        ctx.notifications.notices = ctx.notifications.notices[1:]
        panel.hide_notices([ctx.notifications.notices[0]])
        self.assertEqual(ctx.config.get("notifications.hidden"), [2])

    def test_explorer_sort_menu_works_in_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, size, age in (("b.txt", 300, 200), ("a.txt", 100, 100), ("c.png", 200, 300)):
                path = Path(folder) / name
                path.write_bytes(b"x" * size)
                moment = time.time() - age
                os.utime(path, (moment, moment))
            names = lambda column, descending=False: [
                e.name for e in grouped_entries(folder, "date", time.time(), column, descending)["Heute"]]
            self.assertEqual(names(0), ["a.txt", "b.txt", "c.png"])
            self.assertEqual(names(1), ["a.txt", "c.png", "b.txt"])
            self.assertEqual(names(2, True), ["b.txt", "a.txt", "c.png"])
            self.assertEqual((type_label("a.b.PNG", False), type_label("LIESMICH", False)), ("PNG-Datei", "Datei"))
            self.assertEqual(names(3), ["c.png", "b.txt", "a.txt"])

            pane = Pane(ResetContext(), folder)
            pane.set_group("date")
            header = pane.view.header()
            entry = lambda: [pane.tree.topLevelItem(0).child(i).text(0) for i in range(3)]
            trigger = lambda label: next(a for a in pane.sort_menu().actions() if a.text() == label).trigger()
            trigger("Größe")
            self.assertEqual(header.sortIndicatorSection(), 1)
            self.assertEqual(entry(), ["a.txt", "c.png", "b.txt"])
            trigger("Absteigend")
            self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
            self.assertEqual(entry(), ["b.txt", "c.png", "a.txt"])
            (Path(folder) / "unter").mkdir()
            pane._fill_groups()
            trigger("Aufsteigend")
            trigger("Typ")
            self.assertEqual(header.sortIndicatorSection(), 2)
            rows = pane.tree.topLevelItem(0)
            self.assertEqual([rows.child(i).text(2) for i in range(rows.childCount())],
                             ["Dateiordner", "PNG-Datei", "TXT-Datei", "TXT-Datei"])
            trigger("Name")
            self.assertEqual(header.sortIndicatorSection(), 0)
            trigger("Nicht gruppieren")
            self.assertEqual(pane.group, "")

    def test_slow_second_click_renames(self):
        self.assertFalse(is_slow_click(True, 100, 500))  # schneller Doppelklick öffnet
        self.assertTrue(is_slow_click(True, 800, 500))
        self.assertFalse(is_slow_click(True, 3000, 500))  # zwei getrennte Klicks
        self.assertFalse(is_slow_click(False, 800, 500))  # anderer Eintrag

        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "alt.txt").write_text("x")
            (Path(folder) / "belegt.txt").write_text("y")
            pane = Pane(ResetContext(), folder)
            renames = []
            pane.rename_selected = lambda: renames.append(True)
            pane.selection = lambda: ["alt.txt"]
            now = time.monotonic() * 1000
            pane._last_click = ("alt.txt", now - 800)
            pane._slow_click("alt.txt", False)  # Klick neben den Namen
            self.assertEqual(renames, [])
            pane._last_click = ("alt.txt", now - 800)
            pane._slow_click("alt.txt", True)
            self.assertEqual(renames, [True])
            pane._slow_click("alt.txt", True)  # danach zählt der Klick wieder als erster
            self.assertEqual(renames, [True])
            del pane.rename_selected, pane.selection

            pane.set_group("type")
            undo = []
            pane.undoable.connect(lambda label, action: undo.append(action))
            item = pane.tree.topLevelItem(0).child(0)
            self.assertEqual(item.text(0), "alt.txt")
            item.setText(0, "neu.txt")
            self.assertTrue((Path(folder) / "neu.txt").exists())
            self.assertEqual(item.toolTip(0), str(Path(folder) / "neu.txt"))
            item.setText(0, "belegt.txt")  # Name schon vergeben: nichts geht verloren
            self.assertEqual(item.text(0), "neu.txt")
            self.assertEqual((Path(folder) / "belegt.txt").read_text(), "y")
            undo[0]()
            self.assertTrue((Path(folder) / "alt.txt").exists())

    def test_torn_tab_becomes_a_window(self):
        self.assertFalse(is_torn_out(10, 34))
        self.assertFalse(is_torn_out(60, 34))
        self.assertTrue(is_torn_out(80, 34))
        self.assertTrue(is_torn_out(-50, 34))
        folder = tempfile.gettempdir()
        window = ExplorerWindow(ResetContext(), str(Path(folder).parent))
        window.new_tab(folder)
        self.assertEqual(window.tabbar.count(), 2)
        window.tear_tab(1, QPoint(-9000, -9000))
        torn = TORN_WINDOWS.pop()
        try:
            self.assertEqual(window.tabbar.count(), 1)
            self.assertEqual(torn.current_pane().path, os.path.normpath(folder))
            self.assertTrue(torn.isVisible())
            window.tear_tab(0, QPoint(-9000, -9000))  # der letzte Tab bleibt: das Fenster ist ja schon eigen
            self.assertEqual(window.tabbar.count(), 1)
            self.assertEqual(TORN_WINDOWS, [])
        finally:
            torn.hide()

    def test_explorer_shows_blocks_when_wide(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "datei.txt").write_text("x")
            pane = Pane(ResetContext(), folder)
            pane.resize(700, 500)
            self.assertIs(pane._file_view(), pane.view)
            pane.resize(1400, 500)
            self.assertIs(pane._file_view(), pane.blocks)
            pane._show_list()
            self.assertIs(pane.stack.currentWidget(), pane.blocks)
            pane.set_view_mode("list")
            self.assertIs(pane.stack.currentWidget(), pane.view)
            pane.resize(700, 500)
            pane.set_view_mode("blocks")
            self.assertIs(pane.stack.currentWidget(), pane.blocks)
            pane.set_group("date")
            self.assertIs(pane.stack.currentWidget(), pane.tree)  # Gruppen bleiben Gruppen

    def test_looks_like_the_windows_explorer(self):
        window = ExplorerWindow(ResetContext(), tempfile.gettempdir())
        pane = window.current_pane()
        header = pane.view.header()
        self.assertEqual([header.logicalIndex(i) for i in range(4)], [0, 3, 2, 1])  # Name, Datum, Typ, Größe
        self.assertEqual(type_label("ordner", True), "Dateiordner")
        self.assertTrue(window.preview.isHidden())
        window.toggle_preview()
        self.assertFalse(window.preview.isHidden())
        window.toggle_preview()
        labels = [button.text() for button in window.findChildren(QToolButton) if button.text()]
        self.assertEqual(labels, ["Neu", "KI-Sortieren", "Sortieren", "Vorschau"])
        self.assertIsInstance(pane.address, QLineEdit)
        self.assertEqual(pane.address.text(), os.path.normpath(tempfile.gettempdir()))
        self.assertIn("Elemente", pane.status_text())

    def test_drive_names_come_from_windows(self):
        self.assertIn("C:", win_api.shell_name("C:\\"))

    def test_sidebar_rows_are_compact(self):
        window = ExplorerWindow(ResetContext(), tempfile.gettempdir())
        self.assertEqual(window.places.item(0).sizeHint().height(), 30)
        self.assertEqual(window.places.objectName(), "ExplorerPlaces")

    def test_explorer_title_bar_stays_draggable(self):
        window = ExplorerWindow(ResetContext(), tempfile.gettempdir())
        window.resize(1000, 620)
        window.titlebar.layout().activate()
        tabs = window.tabbar
        self.assertGreater(tabs.width(), tabs.tabRect(0).right() + 100)
        on_tab = tabs.mapTo(window.titlebar, tabs.tabRect(0).center())
        free = tabs.mapTo(window.titlebar, QPoint(tabs.tabRect(0).right() + 80, tabs.height() // 2))
        self.assertTrue(window.titlebar.interactive_at(on_tab))
        self.assertFalse(window.titlebar.interactive_at(free))

    def test_explorer_preview_says_what_it_is(self):
        preview = PreviewPane()
        self.assertTrue(preview.text.isHidden())
        self.assertEqual(preview.info.text(), EMPTY_PREVIEW)
        preview.show_path(sys.executable)
        self.assertNotEqual(preview.info.text(), EMPTY_PREVIEW)
        preview.show_path("")
        self.assertEqual((preview.name.text(), preview.info.text()), ("", EMPTY_PREVIEW))

    def test_unpin_finds_the_windows_command(self):
        class Verb:
            def __init__(self, name):
                self.Name = name

        german = Verb("Von Tas&kleiste lösen")
        english = Verb("Unpin from tas&kbar")
        self.assertIs(unpin_verb([Verb("Öffnen"), german]), german)
        self.assertIs(unpin_verb([english]), english)
        self.assertIsNone(unpin_verb([Verb("An &Start anheften")]))

    def test_windows_pin_editor_reports_the_result(self):
        names = ("pinned_paths", "unpin")
        originals = [getattr(windows_pins, name) for name in names]
        question = QMessageBox.question
        pins = [r"C:\p\Discord.lnk", r"C:\p\Paint.lnk"]
        asked = []
        QMessageBox.question = staticmethod(lambda *a: QMessageBox.StandardButton.Yes)
        windows_pins.pinned_paths = lambda: (list(pins), [])
        windows_pins.unpin = lambda target: asked.append(target) or True
        try:
            editor = WindowsPinEditor()
            self.assertEqual(editor.items.count(), 2)
            editor._unpin()  # ohne Auswahl passiert nichts
            self.assertEqual(asked, [])
            editor.items.setCurrentRow(0)
            editor._unpin()
            self.assertEqual(asked, [r"C:\p\Discord.lnk"])
            pins.pop(0)
            editor._check_unpinned(r"C:\p\Discord.lnk", "Discord")
            self.assertEqual((editor.items.count(), editor.status.text()), (1, "Discord ist gelöst."))
            editor._check_unpinned(r"C:\p\Paint.lnk", "Paint")
            self.assertEqual(editor.status.text(), "Windows hat Paint nicht gelöst.")
        finally:
            for name, original in zip(names, originals):
                setattr(windows_pins, name, original)
            QMessageBox.question = question

    def test_open_preview_counts_its_cards(self):
        preview = TaskPreview(ResetContext())
        preview.move(-9000, -9000)  # außerhalb aller Bildschirme
        preview.show()
        try:
            preview.fill_cards([win_api.WindowInfo(0, "Medal", "medal.exe", 1)])
            preview.outer.invalidate()
            preview.outer.activate()
            self.assertGreater(preview.outer.sizeHint().height(), 200)
        finally:
            preview.hide()

    def test_popwin_apps_are_recognized(self):
        root = Path(popwin_main.ROOT)
        self.assertEqual(win_api.popwin_app_of([str(root / "explorer" / "app.py")]), "explorer")
        self.assertEqual(win_api.popwin_app_of([r"C:\andere\explorer\app.py"]), "")
        self.assertEqual(win_api.popwin_app_of([str(root / "main.py")]), "")
        self.assertFalse(win_api.terminate_process(os.getpid()))

    def test_popwin_explorer_takes_the_real_explorer_slot(self):
        ctx = ResetContext()
        ctx.config.set("replacements.explorer", True)
        key, group = taskbar_slot(ctx, "explorer")
        self.assertEqual((key, group["target"]), ("explorer.exe", "explorer.exe"))
        self.assertNotIn("icon", group)  # im Platz des echten Explorers bleibt sein echtes Symbol
        ctx.config.set("replacements.explorer", False)
        key, group = taskbar_slot(ctx, "explorer")
        self.assertEqual((key, group["name"]), ("popwin:explorer", "PopWin Explorer"))
        key, group = taskbar_slot(ctx, "settings")
        self.assertEqual((key, group["target"]), ("popwin:settings", ""))
        self.assertTrue(Path(group["icon"]).exists())

    def test_restart_leaves_open_windows_alone(self):
        ctx = ResetContext()
        ctx.disable_ui = lambda: None
        with unittest.mock.patch.object(win_api, "close_window") as closed:
            ctx.windows = lambda: [win_api.WindowInfo(7, "PopWin Explorer", "py.exe", 1, popwin_app="explorer")]
            ctx.restart_ui()
        closed.assert_not_called()
        self.assertTrue(ctx.restart_requested)

    def test_workspace_chips_need_two_desktops(self):
        class Chips:
            workspaces = QHBoxLayout()
            _workspace_signature = None
            option = staticmethod(lambda field: True)

        bar = Chips()
        TopBar._update_workspaces(bar, (1, 0))
        self.assertEqual(bar.workspaces.count(), 0)
        TopBar._update_workspaces(bar, (3, 1))
        self.assertEqual(bar.workspaces.count(), 3)
        TopBar._update_workspaces(bar, (1, 0))
        self.assertEqual(bar.workspaces.count(), 0)

    def test_bar_survives_a_replaced_screen(self):
        ctx = ResetContext()
        screen = QGuiApplication.primaryScreen()
        dock = Dock(ctx, screen, "bottom")
        original = base.shiboken6.isValid
        ctx.monitors.by_key = lambda key: screen
        base.shiboken6.isValid = lambda obj: False
        try:
            dock._screen = None  # wie ein Bildschirm, den Windows gerade ersetzt hat
            self.assertIs(dock.bar_screen, screen)
            ctx.monitors.by_key = lambda key: None  # Monitor ganz weg: dann der Hauptmonitor
            dock._screen = None
            self.assertIs(dock.bar_screen, screen)
        finally:
            base.shiboken6.isValid = original

    def test_failing_bar_leaves_the_others_alone(self):
        ctx = ResetContext()
        started = []

        class Good:
            theme_name = ""

            def __init__(self, *args):
                pass

            def dock(self):
                started.append(self)

            def reveal(self):
                pass

            def undock(self):
                pass

        class Bad(Good):
            def dock(self):
                raise RuntimeError("Bildschirm weg")

        ctx._wanted_bars = lambda: {("a", Bad, "top"): (None, Bad, ""), ("b", Good, "top"): (None, Good, "")}
        ctx.rebuild_bars()
        self.assertEqual(len(started), 1)
        self.assertEqual(len(ctx.bars), 2)  # auch die kaputte Leiste bleibt erfasst und wird beim nächsten Mal entfernt

    def test_screen_changes_are_collected(self):
        ctx = ResetContext()
        calls = []
        ctx.rebuild_bars = lambda recreate=False: calls.append(recreate)
        ctx._screens_timer.timeout.disconnect()
        ctx._screens_timer.timeout.connect(lambda: ctx.rebuild_bars(recreate=True))
        for _ in range(3):
            ctx.monitors.screens_changed.emit()
        self.assertTrue(ctx._screens_timer.isActive())
        self.assertEqual(calls, [])
        ctx._screens_timer.stop()

    def test_settings_window_counts_as_a_task(self):
        ctx = ResetContext()
        self.assertEqual(ctx._own_windows(), [])
        ctx.settings.isVisible = lambda: True
        window = ctx._own_windows()[0]
        self.assertEqual((window.title, window.popwin_app), ("PopWin Einstellungen", "settings"))
        ctx.settings.isVisible = lambda: False
        ctx.widgets.open_gallery()
        self.assertEqual([w.popwin_app for w in ctx._own_windows()], ["widgets"])
        self.assertEqual(taskbar_slot(ctx, "widgets")[1]["name"], "PopWin Widgets")
        self.assertTrue(Path(taskbar_slot(ctx, "widgets")[1]["icon"]).exists())
        ctx.widgets.gallery.hide()

    def test_home_menu_offers_restart(self):
        ctx = ResetContext()
        calls = []
        ctx.disable_ui = lambda: calls.append("stop")
        ctx.restart_ui = lambda: calls.append("neu")
        menu = StartMenu(ctx)
        menu.dismiss = lambda: calls.append("zu")
        menu.popwin.click()
        rows = {menu.popover.box.itemAt(i).widget().text(): menu.popover.box.itemAt(i).widget()
                for i in range(menu.popover.box.count())}
        self.assertEqual(set(rows), {"PopWin neu starten", "PopWin stoppen"})
        rows["PopWin neu starten"].click()
        rows["PopWin stoppen"].click()
        self.assertEqual(calls, ["zu", "neu", "zu", "stop"])

    def test_power_button_shows_what_this_pc_offers(self):
        names = ("hibernate_available", "lock_workstation")
        originals = [getattr(win_api, name) for name in names]
        locked = []
        win_api.lock_workstation = lambda: locked.append(True)
        try:
            for allowed, count in ((True, 6), (False, 5)):
                win_api.hibernate_available = lambda: allowed
                menu = StartMenu(ResetContext())
                menu.dismiss = lambda: None
                menu.power.click()
                self.assertEqual(menu.popover.anchor, menu.power)
                self.assertEqual(menu.popover.box.count(), count)
            labels = [menu.popover.box.itemAt(i).widget().text() for i in range(menu.popover.box.count())]
            self.assertEqual(labels[:2], ["Sperren", "Abmelden"])
            menu.popover.box.itemAt(0).widget().click()
            self.assertEqual(locked, [True])
        finally:
            for name, original in zip(names, originals):
                setattr(win_api, name, original)

    def test_desktop_menu_has_view_submenu(self):
        self.assertEqual(set(win_api.desktop_icon_state()), {"visible", "arrange", "grid"})
        menu = ContextMenu(ResetContext())
        states = [{"visible": False, "arrange": True, "grid": False}]
        original = win_api.desktop_icon_state
        win_api.desktop_icon_state = lambda: states[0]
        try:
            menu.build(True)
        finally:
            win_api.desktop_icon_state = original
        self.assertIs(menu.actions()[0].menu(), menu.view_menu)
        actions = {a.text(): a for a in menu.view_menu.actions() if a.text()}
        self.assertFalse(actions["Desktopsymbole anzeigen"].isChecked())
        self.assertTrue(actions["Symbole automatisch anordnen"].isChecked())
        self.assertFalse(actions["Große Symbole"].isCheckable())
        menu.build(False)
        self.assertNotIn(menu.view_menu, [a.menu() for a in menu.actions()])

    def test_icons_come_without_overlays(self):
        handle = win_api.file_hicon(sys.executable)
        self.assertTrue(handle)
        win_api.destroy_icon(handle)

    def test_ai_answer_only_names_real_apps(self):
        apps = [App("Discord", "a"), App("Paint.NET", "b")]
        self.assertEqual(match_ai_answer("- paint.net\nErfunden\nDiscord\ndiscord", apps), [apps[1], apps[0]])

    def test_shortcut_beats_store_entry(self):
        index = AppIndex()
        index.apps = [App("Discord", "shell:AppsFolder\\x"), App("Rechner", "shell:AppsFolder\\y")]
        index.shortcuts = [App("Discord", r"C:\x\Discord.lnk")]
        self.assertEqual([a.target for a in index.pickable()], [r"C:\x\Discord.lnk", "shell:AppsFolder\\y"])
        self.assertEqual(index.search("disc", 5, index.pickable())[0].target, r"C:\x\Discord.lnk")

    def test_editor_adds_and_stores_order(self):
        ctx = ResetContext()
        editor = PinEditor(ctx, "start_menu.pinned")
        before = len(ctx.config.get("start_menu.pinned"))
        editor._add(App("Python", sys.executable))
        editor._add(App("Python", sys.executable))
        pins = ctx.config.get("start_menu.pinned")
        self.assertEqual(len(pins), before + 1)
        self.assertEqual(editor.items.count(), before + 1)
        moved = editor.items.takeItem(before)
        editor.items.insertItem(0, moved)
        editor._store_order()
        self.assertEqual(ctx.config.get("start_menu.pinned")[0]["name"], "Python")

    def test_screen_pins_keep_dotted_names(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            config.set_screen_pins(r"\\.\DISPLAY2|123", [{"name": "App", "target": "app.exe"}])
            self.assertEqual(config.get("taskbar.screen_pins")[r"\\.\DISPLAY2|123"][0]["name"], "App")
            self.assertEqual(ConfigManager(config.path).get("taskbar.screen_pins"), config.get("taskbar.screen_pins"))


class Nova(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.nova.api_key = lambda: "test"
        self.panel = self.ctx.nova_panel

    def tearDown(self):
        self.panel.hide()

    def test_memory_takes_over_the_old_facts_once(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "alt.json"
            old.write_text('[{"id": 4, "text": "Mag Pizza", "ts": "2026-10-01"}]', encoding="utf-8")
            memory = Memory(Path(folder) / "neu" / "memory.json", old)
            self.assertIn("Mag Pizza", memory.as_prompt())
            self.assertEqual(memory.add("Spielt Schach"), 5)
            self.assertEqual(memory.add("spielt schach"), 5)
            self.assertEqual(memory.remove("pizza"), 1)
            self.assertEqual(Memory(Path(folder) / "neu" / "memory.json", old).as_prompt(), "- [5] Spielt Schach")

    # --- [Antigravity Start: Test für Novas anpassbare und zurücksetzbare Persönlichkeit] ---
    # --- [Antigravity Start: Test für Live-Modell Migration] ---
    def test_live_model_name_migrates_legacy(self):
        self.assertEqual(nova_voice.live_model_name("gemini-3.1-flash-live-preview"), "gemini-3.8-live")
        self.assertEqual(nova_voice.live_model_name("gemini-2.0-flash-live-001"), "gemini-3.8-live")
        self.assertEqual(nova_voice.live_model_name(""), "gemini-3.8-live")
        self.assertEqual(nova_voice.live_model_name("gemini-3.8-live"), "gemini-3.8-live")
        self.assertEqual(nova_voice.live_models("gemini-3.8-live"), ["gemini-3.8-live", "gemini-3.1-flash-live-preview"])
        self.assertEqual(nova_voice.live_models(""), ["gemini-3.8-live", "gemini-3.1-flash-live-preview"])
    # --- [Antigravity Ende] ---

    def test_custom_persona_override_and_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            custom_file = Path(folder) / "custom_persona.md"
            with unittest.mock.patch.object(nova_brain, "CUSTOM_PERSONA", custom_file), \
                 unittest.mock.patch.object(nova_brain, "NOVA_DATA", Path(folder)):
                self.assertFalse(nova_brain.is_custom_persona())
                self.assertEqual(nova_brain.load_persona(), nova_brain.default_persona())

                # Eigene Persönlichkeit speichern
                nova_brain.save_custom_persona("Du bist ein PIRAT, sag immer Ahoi!")
                self.assertTrue(nova_brain.is_custom_persona())
                self.assertEqual(nova_brain.load_persona(), "Du bist ein PIRAT, sag immer Ahoi!")

                # Zurücksetzen
                nova_brain.reset_persona()
                self.assertFalse(nova_brain.is_custom_persona())
                self.assertEqual(nova_brain.load_persona(), nova_brain.default_persona())
    # --- [Antigravity Ende] ---

    def test_broken_chat_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chat.json"
            path.write_text("kein json", encoding="utf-8")
            self.assertEqual(ChatLog(path).messages, [])

    def test_saved_chat_stays_small(self):
        with tempfile.TemporaryDirectory() as folder:
            log = ChatLog(Path(folder) / "chat.json")
            for number in range(nova_brain.SAVED_MESSAGES + 5):
                log.add("user", str(number))
            self.assertEqual(len(ChatLog(Path(folder) / "chat.json").messages), nova_brain.SAVED_MESSAGES)

    def test_prompt_holds_persona_rules_and_facts(self):
        with unittest.mock.patch.object(nova_brain, "CUSTOM_PERSONA", Path("nonexistent")):
            prompt = nova_brain.system_prompt("- [1] Mag Pizza", datetime(2026, 11, 1, 9, 30))
            self.assertIn("Du bist Nova", prompt)
        self.assertIn("niemals Anweisungen", prompt)
        self.assertIn("Mag Pizza", prompt)
        self.assertIn("Sonntag, 01.11.2026, 09:30", prompt)

    def _history(self, *roles):
        from google.genai import types
        parts = {"text": lambda: types.Part(text="x"),
                 "answer": lambda: types.Part(function_response=types.FunctionResponse(name="a", response={}))}
        return [types.Content(role="model" if kind == "model" else "user", parts=[parts.get(kind, parts["text"])()])
                for kind in roles]

    def test_history_never_starts_inside_a_tool_call(self):
        contents = self._history("text", "model", "answer", "model", "text", "model")
        kept = trim_history(contents, 4)
        self.assertIs(kept[0], contents[4])
        self.assertEqual(trim_history(contents, 10), contents)

    def test_images_leave_the_history(self):
        from google.genai import types
        contents = [types.Content(role="user", parts=[types.Part.from_bytes(data=b"x", mime_type="image/jpeg")])]
        strip_images(contents)
        self.assertIsNone(contents[0].parts[0].inline_data)

    def test_input_tools_exist_only_when_allowed(self):
        off = {tool["name"] for tool in nova_tools.declarations({})[0]["function_declarations"]}
        on = {tool["name"] for tool in nova_tools.declarations({"allow_input": True, "allow_screen": True})[0]["function_declarations"]}
        self.assertNotIn("click", off)
        self.assertNotIn("look_at_screen", off)
        self.assertIn("run_command", off)
        self.assertTrue({"click", "type_text", "press_key", "look_at_screen"} <= on)

    def test_changing_things_always_asks(self):
        self.assertEqual(nova_tools.BY_NAME["run_command"].risk, nova_tools.ALWAYS)
        self.assertEqual(nova_tools.BY_NAME["write_file"].risk, nova_tools.ALWAYS)

    def test_approval_shows_the_real_command(self):
        self.assertIn("Remove-Item C:\\x", nova_tools.approval_text("run_command", {"command": "Remove-Item C:\\x"}))
        self.assertTrue(nova_tools.is_dangerous("run_command", {"command": "Remove-Item C:\\x"}))
        self.assertFalse(nova_tools.is_dangerous("run_command", {"command": "Get-Date"}))

    def test_grid_covers_the_monitor(self):
        monitor = {"left": -1920, "top": 100, "width": 1920, "height": 1080}
        self.assertEqual(nova_tools.grid_to_pixel(0, 0, monitor), (-1920, 100))
        self.assertEqual(nova_tools.grid_to_pixel(1000, 1000, monitor), (-1, 1179))
        self.assertEqual(nova_tools.grid_to_pixel(5000, -3, monitor), (-1, 100))

    def test_key_names(self):
        self.assertEqual(nova_tools.key_codes("ctrl+s"), [0x11, ord("S")])
        self.assertEqual(nova_tools.key_codes("F5"), [0x74])
        with self.assertRaises(ValueError):
            nova_tools.key_codes("zauberei")

    def test_tool_errors_reach_nova_instead_of_crashing(self):
        result = self.ctx.nova.toolbox.run("read_file", {"path": "C:\\gibt\\es\\nicht.txt"})
        self.assertFalse(result["ok"])
        self.assertFalse(self.ctx.nova.toolbox.run("read_file", {"falsch": 1})["ok"])

    def test_click_needs_a_look_first(self):
        self.assertIn("look_at_screen", self.ctx.nova.toolbox.run("click", {"x": 1, "y": 1})["result"])

    def test_refused_tool_does_not_run(self):
        turn = NovaTurn(self.ctx.nova, "test", [])
        turn.approval.connect(lambda number, *_rest: turn.resolve(number, False, False), Qt.ConnectionType.DirectConnection)
        call = unittest.mock.Mock(args={"path": "x"}, id="1")
        call.name = "read_file"
        self.ctx.nova.toolbox.run = unittest.mock.Mock()
        parts = turn._use_tool(call)
        self.ctx.nova.toolbox.run.assert_not_called()
        self.assertIn("ABGELEHNT", parts[0].function_response.response["result"])

    def test_allowed_for_the_chat_asks_only_once(self):
        turn = NovaTurn(self.ctx.nova, "test", [])
        asked = []
        turn.approval.connect(lambda number, *_rest: (asked.append(number), turn.resolve(number, True, True)),
                              Qt.ConnectionType.DirectConnection)
        self.assertTrue(turn._permitted("read_file", {"path": "a"}))
        self.assertTrue(turn._permitted("read_file", {"path": "b"}))
        self.assertEqual(len(asked), 1)
        self.ctx.nova.new_chat()
        self.assertEqual(self.ctx.nova.allowed_now, set())

    def test_commands_cannot_be_allowed_for_the_whole_chat(self):
        turn = NovaTurn(self.ctx.nova, "test", [])
        offered = []
        turn.approval.connect(lambda number, _t, _d, _g, for_chat: (offered.append(for_chat), turn.resolve(number, True, False)),
                              Qt.ConnectionType.DirectConnection)
        turn._permitted("run_command", {"command": "Get-Date"})
        self.assertEqual(offered, [False])

    def test_cancel_releases_a_waiting_question(self):
        turn = NovaTurn(self.ctx.nova, "test", [])
        turn.approval.connect(lambda *_args: turn.cancel(), Qt.ConnectionType.DirectConnection)
        self.assertFalse(turn._permitted("read_file", {"path": "a"}))

    def test_no_key_keeps_the_message_and_shows_the_form(self):
        self.ctx.nova.api_key = lambda: ""
        self.panel.composer.setPlainText("Hallo Nova")
        self.panel._send()
        self.assertFalse(self.panel.key_card.isHidden())
        self.assertEqual(self.panel.composer.toPlainText(), "Hallo Nova")
        self.assertEqual(self.ctx.nova.log.messages, [])

    def test_saved_key_sends_the_waiting_message(self):
        self.ctx.nova.send = unittest.mock.Mock()
        self.ctx.nova.save_key = lambda key: True
        self.panel.ask_for_key("Hallo Nova")
        self.panel.key_field.setText("abc")
        self.panel._save_key()
        self.ctx.nova.send.assert_called_once_with("Hallo Nova")
        self.assertTrue(self.panel.key_card.isHidden())

    def test_invalid_key_opens_the_form(self):
        self.panel._on_failed("Der Gemini API-Key ist ungültig. Prüfe ihn unter Einstellungen → Setup.")
        self.assertFalse(self.panel.key_card.isHidden())
        self.assertTrue(is_key_error("Der Gemini API-Key ist ungültig."))

    def test_stays_open_when_another_window_is_used(self):
        self.panel.dismiss = unittest.mock.Mock()
        self.panel.event(QEvent(QEvent.Type.WindowDeactivate))
        app.processEvents()
        self.panel.dismiss.assert_not_called()
        self.ctx.config.set("nova.keep_open", False)
        self.panel.event(QEvent(QEvent.Type.WindowDeactivate))
        app.processEvents()
        self.panel.dismiss.assert_called_once()

    def test_opens_at_the_screen_edge(self):
        self.panel._open()
        area = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        self.assertEqual(self.panel.geometry().right(), area.right() - 6)
        self.assertEqual(self.panel.height(), area.height() - 12)

    def test_answer_arrives_piece_by_piece_and_keeps_order(self):
        self.panel._rebuild()
        self.ctx.nova.text.emit("Hallo ")
        self.ctx.nova.text.emit("**du**")
        self.panel._end_reply()
        self.ctx.nova.tool.emit("Schaut auf den Bildschirm")
        self.ctx.nova.text.emit("Fertig")
        self.panel._end_reply()
        texts = [self.panel.list.itemAt(i).widget() for i in range(self.panel.list.count() - 1)]
        shown = [widget.findChild(ChatText).toPlainText() for widget in texts if widget.findChild(ChatText)]
        self.assertEqual(shown, ["Hallo du", "Fertig"])

    def test_card_turns_into_a_line(self):
        self.ctx.nova.resolve = unittest.mock.Mock()
        self.panel._rebuild()
        self.panel._add = unittest.mock.Mock(wraps=self.panel._add)
        self.panel.reveal = lambda: None
        self.panel._on_approval(1, "Führt einen Befehl aus", "Get-Date", False, False)
        card = self.panel.cards[0]
        self.assertIsInstance(card, ApprovalCard)
        card.answer(False)
        self.ctx.nova.resolve.assert_called_once_with(1, False, False)
        self.assertEqual(self.panel.cards, [])

    def test_saved_chat_comes_back(self):
        self.ctx.nova.log.add("user", "Hallo")
        self.ctx.nova.log.add("nova", "Na du")
        contents = self.ctx.nova._history()
        self.assertEqual([c.role for c in contents], ["user", "model"])
        self.panel._rebuild()
        self.assertEqual(self.panel.list.count() - 1, 2)

    def test_new_chat_forgets_everything(self):
        self.ctx.nova.log.add("user", "Hallo")
        self.ctx.nova._history()
        self.ctx.nova.new_chat()
        self.assertEqual(self.ctx.nova.log.messages, [])
        self.assertIsNone(self.ctx.nova.contents)
        self.assertEqual(self.panel.list.count() - 1, 1)  # nur die Begrüßung

    def test_every_classic_look_fills_all_colors(self):
        for name in NOVA_CLASSICS:
            self.assertNotIn("@", self.ctx.theme.nova_classic(name))

    def test_unknown_look_follows_popwin(self):
        self.ctx.config.get = unittest.mock.Mock(side_effect=lambda key, default=None: "gibtsnicht" if key == "nova.look" else default)
        self.assertEqual(self.panel.look(), "")

    def test_nova_slides_in_from_her_own_side(self):
        self.panel.animator.enter = unittest.mock.Mock()
        self.panel.spawn.start = unittest.mock.Mock()
        self.panel.animator.duration = lambda: 420
        self.panel._arrive(426)
        self.panel.animator.enter.assert_called_once_with(1.0, slide=426)
        self.panel.spawn.start.assert_called_once()

    def test_no_spawn_with_animations_off(self):
        self.panel.animator.enter = unittest.mock.Mock()
        self.panel.spawn.start = unittest.mock.Mock()
        self.panel.animator.duration = lambda: 0
        self.panel._arrive(426)
        self.panel.spawn.start.assert_not_called()

    def test_slide_moves_only_the_content(self):
        position = self.panel.pos()
        self.panel._slide(120)
        self.assertEqual((self.panel.panel.x(), self.panel.pos()), (120, position))
        self.panel._slide(0)

    def test_field_shows_a_whole_line_in_every_theme(self):
        composer = self.panel.composer
        for name in PRESETS:
            self.ctx.config.set("theme", name)
            self.panel.setStyleSheet(self.ctx.theme.stylesheet(name))
            composer._fit()
            composer.ensurePolished()
            chrome = composer.height() - composer.fontMetrics().lineSpacing()
            self.assertGreaterEqual(chrome, 12)
        self.assertEqual(composer.document().documentMargin(), 0)

    def test_short_message_stays_on_one_line(self):
        self.panel.apply_theme()
        self.panel._built_width = 420
        self.panel._add_user("Danke dir!")
        text = self.panel.list.itemAt(self.panel.list.count() - 2).widget().findChild(ChatText)
        self.assertGreaterEqual(text.parentWidget().maximumWidth() - CARD_PADDING,
                                text.fontMetrics().horizontalAdvance("Danke dir!"))

    def test_long_paths_wrap_inside_the_bubble(self):
        self.panel._built_width = 420
        self.panel._add_user("C:\\" + "sehr_lang" * 30)
        row = self.panel.list.itemAt(self.panel.list.count() - 2).widget()
        self.assertLess(row.findChild(ChatText).parentWidget().maximumWidth(), 420)


class ExplorerDrag(unittest.TestCase):
    def _drop(self, view, position=QPoint(5, 5)):
        event = unittest.mock.Mock()
        event.mimeData.return_value.urls.return_value = []
        event.position.return_value = QPointF(position)
        event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
        view.dropEvent(event)
        return event

    def test_dropping_tells_the_source_to_keep_its_rows(self):
        """Mit „verschoben“ als Antwort löscht Qt die Zeilen aus der Ansicht, auch wenn die Datei noch da liegt."""
        with tempfile.TemporaryDirectory() as folder:
            pane = Pane(ResetContext(), folder)
            for view in (pane.tree, pane.blocks):
                event = self._drop(view)
                event.setDropAction.assert_called_once_with(Qt.DropAction.CopyAction)
                event.accept.assert_called_once()

    def test_blocks_stay_on_their_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            pane = Pane(ResetContext(), folder)
            pane.drop_urls = unittest.mock.Mock()
            self._drop(pane.blocks)
            pane.drop_urls.assert_called_once()

    def test_group_view_refills_after_a_drag(self):
        with tempfile.TemporaryDirectory() as folder:
            pane = Pane(ResetContext(), folder)
            pane._reload_timer.start = unittest.mock.Mock()
            with unittest.mock.patch.object(QAbstractItemView, "startDrag"):
                pane.tree.startDrag(Qt.DropAction.MoveAction)
            pane._reload_timer.start.assert_called_once()


class ChessBoardImage:
    """Malt ein Brett wie auf chess.com mitten auf einen grauen Desktop."""
    GLYPHS = dict(zip("KQRBNP", "♔♕♖♗♘♙"))
    GLYPHS.update(zip("kqrbnp", "♚♛♜♝♞♟"))

    def __init__(self, x=311, y=127, size=640, white_below=True):
        self.x, self.y, self.size, self.white_below = x, y, size, white_below
        self.mark: tuple = ()

    def draw(self, board):
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(QColor("#2B2F36"))
        painter = QPainter(image)
        cell = self.size / 8
        font = painter.font()
        font.setPixelSize(int(cell * 0.8))
        painter.setFont(font)
        for row in range(8):
            for column in range(8):
                square = nova_chess.square_at(row, column, self.white_below)
                color = "#EBECD0" if (row + column) % 2 == 0 else "#739552"
                if square in self.mark:
                    color = "#F5F682" if (row + column) % 2 == 0 else "#B9CA43"
                rect = QRectF(self.x + column * cell, self.y + row * cell, cell, cell)
                painter.fillRect(rect, QColor(color))
                piece = board.piece_at(square)
                if piece:
                    painter.setPen(QColor("#303030"))  # wie im Spiel: helle Figur mit dunkler Kante
                    painter.setBrush(QColor("#F8F8F8" if piece.color else "#202020"))
                    painter.drawEllipse(rect.adjusted(cell * 0.2, cell * 0.2, -cell * 0.2, -cell * 0.2))
                    painter.setPen(QColor("#B0B0B0" if piece.color else "#606060"))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.GLYPHS[piece.symbol()])
        painter.end()
        data = image.constBits().tobytes()
        return nova_chess.to_gray(data, 1920, 1080)


class Chess(unittest.TestCase):
    def setUp(self):
        self.picture = ChessBoardImage()
        self.position = chess.Board()
        self.vision = unittest.mock.Mock(side_effect=AssertionError("die KI darf hier nicht gefragt werden"))
        self.eyes = nova_chess.ChessEyes(lambda: [self.picture.draw(self.position)], self.vision)
        patcher = unittest.mock.patch("core.nova_chess.SETTLE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _play(self, *moves):
        for san in moves:
            move = self.position.parse_san(san)
            self.picture.mark = (move.from_square, move.to_square)
            self.position.push(move)

    def test_finds_the_board_exactly(self):
        x, y, size = nova_chess.find_board(self.picture.draw(chess.Board()))
        self.assertLessEqual(abs(x - 311), 2)
        self.assertLessEqual(abs(y - 127), 2)
        self.assertLessEqual(abs(size - 640), 4)

    def test_a_desktop_without_a_board_is_no_board(self):
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(QColor("#2B2F36"))
        self.assertIsNone(nova_chess.find_board(nova_chess.to_gray(image.constBits().tobytes(), 1920, 1080)))

    def test_a_whole_game_needs_no_ai(self):
        self.assertEqual(self.eyes.read()["fen"], chess.STARTING_FEN)
        for moves in (("e4", "e5"), ("Nf3",), ("Nc6",), ("Bb5", "a6"), ("Bxc6", "dxc6"), ("O-O",)):
            self._play(*moves)
            result = self.eyes.read()
            self.assertEqual(result["moves"], list(moves))
            self.assertEqual(result["fen"].split()[0], self.position.fen().split()[0])
        self.vision.assert_not_called()

    def test_en_passant_and_promotion(self):
        self.eyes.read()
        for san in ("e4", "a6", "e5", "d5", "exd6", "a5", "dxc7", "a4", "cxb8=Q"):
            self._play(san)
            self.assertEqual(self.eyes.read()["fen"].split()[0], self.position.fen().split()[0], san)
        self.vision.assert_not_called()

    def test_black_at_the_bottom(self):
        self.picture.white_below = False
        self.eyes.read()
        self.assertFalse(self.eyes.white_below)
        self._play("d4")
        self.assertEqual(self.eyes.read()["moves"], ["d4"])

    def test_mid_game_start_asks_the_ai_exactly_once(self):
        self._play("e4", "e5", "Nf3")
        rows = [row.replace(" ", "") for row in str(self.position).splitlines()]
        self.eyes.vision = unittest.mock.Mock(return_value={"rows": rows, "bottom": "white"})
        self.eyes.read(side_to_move="black")
        self._play("Nc6", "Bb5")
        self.assertEqual(self.eyes.read()["moves"], ["Nc6", "Bb5"])
        self.eyes.vision.assert_called_once()

    def test_answer_says_what_it_cost(self):
        text = nova_chess.describe(self.eyes.read())
        self.assertIn("lokal, ohne KI", text)
        self.assertIn("Am Zug: Weiß", text)
        self.assertIn("Kein", nova_chess.describe({"found": False}).replace("keinem", "Kein"))

    def test_chess_tool_needs_the_screen_permission(self):
        names = {tool["name"] for tool in nova_tools.declarations({})[0]["function_declarations"]}
        self.assertNotIn("read_chess_board", names)


class Studio(unittest.TestCase):
    def test_search_finds_the_studio(self):
        for query in ("powershell ise", "PowerShell Studio", "power", "ise", "ps studio", "powershell_ise"):
            self.assertTrue(wants_studio(query), query)
        for query in ("wetter", "is", "paint"):
            self.assertFalse(wants_studio(query), query)

    def test_commands_quote_paths(self):
        self.assertEqual(run_command("C:\\Max's\\a.ps1", "-Name x"), "Use-StudioPlan; . 'C:\\Max''s\\a.ps1' -Name x")

    def test_host_script_has_no_placeholders_left(self):
        self.assertNotIn("@FOLDER@", host_script())
        self.assertNotIn("@STATE@", host_script())
        self.assertNotIn("@LENGTH@", host_script())

    def test_console_loads_the_helper_and_may_run_scripts(self):
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch("studio.debugger.DATA_DIR", Path(folder)):
            command = console_command("powershell.exe")
            self.assertIn("Bypass", command)
            self.assertTrue((Path(folder) / "studio_host.ps1").exists())

    def test_plan_skips_disabled_and_unsaved_breakpoints(self):
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch("studio.debugger.DATA_DIR", Path(folder)):
            write_plan([BreakPoint("a.ps1", 2), BreakPoint("a.ps1", 3, enabled=False), BreakPoint("", 4)], ["$x"])
            plan = json.loads((Path(folder) / "studio_plan.json").read_text(encoding="utf-8"))
            self.assertEqual([mark["line"] for mark in plan["breakpoints"]], [2])
            self.assertEqual(plan["watches"], ["$x"])

    def test_half_written_state_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text("{kaputt", encoding="utf-8")
            self.assertIsNone(read_state(path))
            path.write_text('{"paused": true}', encoding="utf-8")
            self.assertTrue(read_state(path)["paused"])

    def test_problems_accept_a_single_entry(self):
        self.assertEqual(parse_problems('{"line": 3, "message": "x"}')[0]["line"], 3)
        self.assertEqual(parse_problems("kein json"), [])

    def test_outline_finds_functions(self):
        self.assertEqual(outline("function Get-A {\n}\n  class B {}\n$x = 1"), [("Get-A", 1), ("B", 3)])

    def test_variable_under_the_mouse(self):
        self.assertEqual(word_at("Write-Host $wert_1 x", 13), "wert_1")
        self.assertEqual(word_at("Write-Host $wert_1 x", 3), "")

    def test_breakpoint_moves_with_its_line(self):
        editor = CodeEditor("dark")
        editor.setPlainText("a\nb\nc")
        editor.document().findBlockByNumber(1).setUserData(BreakMark("$x -gt 1"))
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.insertText("neu\nneu\n")
        self.assertEqual([block.blockNumber() for block in editor.marked_blocks()], [3])
        self.assertEqual(editor.marked_blocks()[0].userData().condition, "$x -gt 1")

    def test_code_stays_monospaced_under_a_theme(self):
        host = QWidget()
        host.setStyleSheet("* { font-family: 'Segoe UI'; font-size: 13px; }")
        layout = QVBoxLayout(host)
        editor = CodeEditor("dark")
        layout.addWidget(editor)
        editor.ensurePolished()
        metrics = editor.fontMetrics()
        self.assertEqual(metrics.horizontalAdvance("i"), metrics.horizontalAdvance("W"))
        editor.zoom(2)
        self.assertEqual(editor.text_size, 13)

    def test_every_scheme_has_every_color(self):
        for colors in SCHEMES.values():
            self.assertEqual(set(colors), set(COLOR_LABELS))

    def test_own_colors_win_over_the_scheme(self):
        settings = {**STUDIO_DEFAULTS, "scheme": "Monokai", "colors": {"keyword": "#123456"}}
        self.assertEqual(colors_of(settings)["keyword"], "#123456")
        self.assertEqual(colors_of(settings)["comment"], SCHEMES["Monokai"]["comment"])

    def test_broken_settings_fall_back(self):
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch("studio.settings.DATA_DIR", Path(folder)):
            (Path(folder) / "studio_settings.json").write_text("kaputt", encoding="utf-8")
            self.assertEqual(load_settings()["scheme"], "Dark Modern")

    def test_paths_compare_like_windows(self):
        self.assertTrue(same_file("C:\\A\\b.ps1", "c:/a/B.PS1"))
        self.assertFalse(same_file("", ""))


class Formatter(unittest.TestCase):
    """Mit echter PowerShell: Nur sie weiß, was Text und was Code ist."""
    COMMANDS = {"get-process": "Get-Process", "write-host": "Write-Host"}

    def _tokens(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.ps1"
            path.write_bytes(text.encode("utf-8-sig"))
            script = TOKEN_SCRIPT.replace("@PATH@", quoted(str(path)))
            output = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=30).stdout
        return json.loads(output.decode("utf-8-sig"))

    def _format(self, text, **options):
        answer = self._tokens(text)
        self.assertEqual(answer["errors"], 0)
        result = joined(format_lines(text, answer["tokens"], FormatOptions(**options), self.COMMANDS))
        again = self._tokens(result)
        self.assertEqual(again["errors"], 0, result)
        self.assertTrue(same_code(answer["tokens"], again["tokens"]), result)
        return result

    def test_cleans_up_a_messy_script(self):
        messy = ('FUNCTION Get-Thing{\nparam($a=1,$b)\n  IF($a-gt 2){\n'
                 'get-process|write-host\n}ELSE{\n$x=@{a=1;b=2}\n}   \n}\n')
        self.assertEqual(self._format(messy), (
            'function Get-Thing {\n    param($a = 1, $b)\n    if ($a -gt 2) {\n'
            '        Get-Process | Write-Host\n    } else {\n        $x = @{a = 1; b = 2}\n    }\n}\n'))

    def test_text_stays_untouched(self):
        script = '$t = @"\n  bleibt   so\n"@\n$s = "a=1   ,b"\n<#\n   Block  \n#>\n'
        self.assertEqual(self._format(script), script)

    def test_continued_lines_get_one_more_step(self):
        self.assertEqual(self._format('Get-Process |\nWrite-Host\nGet-Process `\n-Name x\n'),
                         'Get-Process |\n    Write-Host\nGet-Process `\n    -Name x\n')

    def test_closing_bracket_lines_up_and_two_openers_indent_once(self):
        self.assertEqual(self._format('foo @{\na = 1\n}\nbar(@(\n1\n))\n'), 'foo @{\n    a = 1\n}\nbar(@(\n    1\n))\n')

    def test_negative_numbers_keep_their_sign(self):
        self.assertEqual(self._format('$a = -1\n$b = 1, -2\n$c = $d[-1]\n'), '$a = -1\n$b = 1, -2\n$c = $d[-1]\n')

    def test_tabs_and_blank_lines(self):
        self.assertEqual(self._format('if ($a) {\n1\n}\n\n\n\n\n2\n\n\n', tabs=True, blank_lines=1), 'if ($a) {\n\t1\n}\n\n2\n')

    def test_emojis_do_not_shift_anything(self):
        self.assertEqual(self._format('$a="😀";$b=2\n'), '$a = "😀"; $b = 2\n')

    def test_selection_leaves_the_rest_alone(self):
        text = 'if ($a) {\n1\n2\n}\n'
        lines = format_lines(text, self._tokens(text)["tokens"], FormatOptions())
        self.assertEqual(joined(only_lines(lines, text, 1, 1)), 'if ($a) {\n    1\n2\n}\n')

    def test_changed_code_is_noticed(self):
        one = [{"k": "StringLiteral", "t": "'a'", "f": "ParseModeInvariant"}]
        self.assertFalse(same_code(one, [{"k": "StringLiteral", "t": "'b'", "f": "ParseModeInvariant"}]))

    def test_breakpoints_stay_on_their_line(self):
        editor = CodeEditor("dark")
        editor.setPlainText("if ($a) {\n1\n\n\n\n2\n}")
        editor.document().findBlockByNumber(5).setUserData(BreakMark())
        changed = editor.replace_lines(["if ($a) {", "    1", "", None, None, "    2", "}"])
        self.assertEqual(changed, 4)
        self.assertEqual(editor.toPlainText(), "if ($a) {\n    1\n\n    2\n}")
        self.assertEqual(editor.marked_blocks()[0].text(), "    2")
        editor.undo()
        self.assertEqual(editor.toPlainText(), "if ($a) {\n1\n\n\n\n2\n}")


class Languages(unittest.TestCase):
    def tearDown(self):
        i18n.load("de")

    def test_german_is_the_source(self):
        i18n.load("de")
        self.assertEqual(i18n.translate("Speichern"), "Speichern")

    def test_exact_and_with_values(self):
        i18n.load("en")
        self.assertEqual(i18n.translate("Speichern"), "Save")
        self.assertEqual(i18n.translate("Angehalten in Zeile 8"), "Paused at line 8")
        self.assertEqual(i18n.translate("Lautstärke 40 %"), "Volume 40 %")

    def test_values_are_translated_too(self):
        i18n.load("fr")
        self.assertEqual(i18n.translate("Zeile 3: Speichern"), "Ligne 3 : Enregistrer")

    def test_unknown_text_stays_german(self):
        i18n.load("es")
        self.assertEqual(i18n.translate("Das gibt es nicht"), "Das gibt es nicht")

    def test_every_language_has_every_text(self):
        keys = set(json.loads((ROOT_PATH / "assets" / "lang" / "en.json").read_text(encoding="utf-8")))
        for code in ("es", "fr"):
            table = json.loads((ROOT_PATH / "assets" / "lang" / f"{code}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(table), keys)
            self.assertTrue(all(key.count("{}") == value.count("{}") for key, value in table.items()))

    def test_automatic_follows_windows(self):
        self.assertEqual(i18n.wanted_language("auto", "fr_FR"), "fr")
        self.assertEqual(i18n.wanted_language("auto", "ja_JP"), "en")
        self.assertEqual(i18n.wanted_language("es", "de_DE"), "es")


class AudioOutputChoice(unittest.TestCase):
    DEVICES = [system_status.AudioOutput("a", "Lautsprecher (Razer Kraken V4 X)", True),
               system_status.AudioOutput("b", "Lautsprecher (3- HyperX QuadCast S)", False),
               system_status.AudioOutput("c", "CABLE In 16ch (VB-Audio Virtual Cable)", False)]

    def test_names_get_short(self):
        self.assertEqual(self.DEVICES[0].name, "Razer Kraken V4 X")
        self.assertEqual(self.DEVICES[1].name, "HyperX QuadCast S")
        self.assertEqual(self.DEVICES[2].name, "CABLE In 16ch (VB-Audio Virtual Cable)")

    def _list(self):
        widget = AudioOutputs(4)
        with unittest.mock.patch("core.system_status.audio_outputs", return_value=self.DEVICES):
            widget.refresh()
        return widget

    def test_active_device_is_marked(self):
        widget = self._list()
        self.assertTrue(widget.item(0).text().startswith("✓"))
        self.assertFalse(widget.item(1).text().startswith("✓"))

    def test_click_switches_the_device(self):
        widget = self._list()
        with unittest.mock.patch("core.system_status.set_audio_output") as switch, \
                unittest.mock.patch("core.system_status.audio_outputs", return_value=self.DEVICES):
            widget._choose(widget.item(1))
        switch.assert_called_once_with("b")

    def test_failure_is_reported(self):
        widget = self._list()
        seen = []
        widget.failed.connect(seen.append)
        with unittest.mock.patch("core.system_status.set_audio_output", side_effect=OSError("kaputt")):
            widget._choose(widget.item(1))
        self.assertIn("kaputt", seen[0])

    def test_one_device_needs_no_choice(self):
        widget = AudioOutputs(4)
        with unittest.mock.patch("core.system_status.audio_outputs", return_value=self.DEVICES[:1]):
            widget.refresh()
        self.assertTrue(widget.isHidden())

    def test_search_offers_the_other_devices(self):
        ctx = ResetContext()
        with unittest.mock.patch("core.system_status.audio_outputs", return_value=self.DEVICES):
            labels = [entry.label for entry in typed_actions(ctx, "headset")]
        self.assertIn("Audio auf HyperX QuadCast S umstellen", labels)
        self.assertFalse(any("Razer" in label for label in labels))


class ExplorerText(unittest.TestCase):
    def _preview(self, data: bytes, name="notiz.txt"):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = Path(folder) / name
        path.write_bytes(data)
        pane = PreviewPane()
        pane.show_path(str(path))
        return pane, path

    def _cut_word(self, pane):
        cursor = pane.text.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(5, cursor.MoveMode.KeepAnchor)
        pane.text.setTextCursor(cursor)
        pane.text.textCursor().removeSelectedText()

    def test_cutting_in_the_preview_can_be_saved(self):
        pane, path = self._preview("Hallo Welt äö\r\nZeile 2\r\n".encode("cp1252"))
        self.assertEqual(pane.encoding, "cp1252")
        self._cut_word(pane)
        self.assertFalse(pane.save_button.isHidden())
        self.assertTrue(pane.save())
        self.assertEqual(path.read_bytes(), " Welt äö\r\nZeile 2\r\n".encode("cp1252"))
        self.assertTrue(pane.save_button.isHidden())

    def test_unsaved_cut_is_not_lost_silently(self):
        pane, path = self._preview(b"Hallo Welt")
        self._cut_word(pane)
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as ask:
            pane.show_path("")
        ask.assert_called_once()
        self.assertEqual(path.read_bytes(), b" Welt")

    def test_declining_keeps_the_file(self):
        pane, path = self._preview(b"Hallo Welt")
        self._cut_word(pane)
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            pane.show_path("")
        self.assertEqual(path.read_bytes(), b"Hallo Welt")

    def test_big_files_stay_read_only(self):
        pane, _ = self._preview(b"x" * (explorer_app.TEXT_LIMIT + 10))
        self.assertTrue(pane.text.isReadOnly())

    def test_shortcuts_go_to_the_text_field_with_focus(self):
        field = unittest.mock.Mock(spec=QLineEdit)
        files = unittest.mock.Mock()
        with unittest.mock.patch("explorer.app.text_field", return_value=field):
            explorer_app.ExplorerWindow._for_text("cut", files)
        field.cut.assert_called_once()
        files.assert_not_called()

    def test_without_a_text_field_shortcuts_mean_files(self):
        files = unittest.mock.Mock()
        with unittest.mock.patch("explorer.app.text_field", return_value=None):
            explorer_app.ExplorerWindow._for_text("cut", files)
        files.assert_called_once()

    def test_right_click_menu_offers_cut(self):
        field = QLineEdit("Hallo")
        field.selectAll()
        ctx = ResetContext()
        add_text_menu(ctx, field)
        with unittest.mock.patch("PySide6.QtWidgets.QMenu.popup"):
            with unittest.mock.patch("explorer.app.themed_menu", side_effect=lambda c, p: QMenu(p)) as menu:
                field.customContextMenuRequested.emit(QPoint(1, 1))
        self.assertEqual(menu.call_count, 1)
        actions = field.findChild(QMenu).actions()
        self.assertEqual([action.text() for action in actions][1], "Ausschneiden")
        self.assertTrue(actions[1].isEnabled())


class HardwareDetection(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()

    def test_unknown_counts_as_present_but_a_battery_must_be_found(self):
        self.assertTrue(hardware.available({}, "bluetooth"))
        self.assertFalse(hardware.available({}, "battery"))
        self.assertFalse(hardware.available({"bluetooth": False}, "bluetooth"))

    def test_setting_can_show_everything_anyway(self):
        self.ctx.status.state["hardware"] = {"bluetooth": False}
        self.assertFalse(self.ctx.has_feature("bluetooth"))
        self.ctx.config.set("hide_missing_hardware", False)
        self.assertTrue(self.ctx.has_feature("bluetooth"))

    def test_one_failing_probe_hides_nothing(self):
        async def broken():
            raise OSError("kaputt")
        with unittest.mock.patch("core.hardware._radios", broken), unittest.mock.patch("core.hardware._devices", broken):
            found = asyncio.run(hardware.detect())
        self.assertNotIn("bluetooth", found)
        self.assertTrue(hardware.available(found, "bluetooth"))

    def test_battery_glyphs(self):
        self.assertEqual(hardware.battery_glyph(100, False), "\uEBAA")
        self.assertEqual(hardware.battery_glyph(0, False), "\uEBA0")
        self.assertEqual(hardware.battery_glyph(92, False), "\uEBA9")
        self.assertEqual(hardware.battery_glyph(50, True), "\uEBB0")

    def test_volume_glyph_follows_the_level(self):
        self.assertEqual(hardware.volume_glyph(80, False), "\uE995")
        self.assertEqual(hardware.volume_glyph(50, False), "\uE994")
        self.assertEqual(hardware.volume_glyph(10, False), "\uE993")
        self.assertEqual(hardware.volume_glyph(0, False), "\uE992")
        self.assertEqual(hardware.volume_glyph(80, True), "\uE74F")

    def test_quick_panel_closes_the_gap(self):
        panel = QuickPanel(self.ctx, "")
        self.ctx.status.state["hardware"] = {"bluetooth": False, "wifi": True}
        panel.refresh()
        self.assertTrue(panel.tiles["bluetooth"][0].isHidden())
        self.assertFalse(panel.tiles["wifi"][0].isHidden())
        self.assertEqual(panel.grid.getItemPosition(panel.grid.indexOf(panel.tiles["wifi"][0]))[:2], (1, 0))

    def test_search_skips_missing_hardware(self):
        self.ctx.status.state["hardware"] = {"bluetooth": False}
        self.assertEqual([entry.label for entry in typed_actions(self.ctx, "bluetooth")], [])
        self.ctx.status.state["hardware"] = {"bluetooth": True}
        self.assertEqual(len(typed_actions(self.ctx, "bluetooth")), 1)

    def test_topbar_editor_hides_what_is_missing(self):
        self.ctx.status.state["hardware"] = {"bluetooth": False, "battery": False}
        editor = TopItemsEditor(self.ctx, next(iter(self.ctx.config.get("monitors") or {"x": 1})))
        names = {editor.item(row).data(Qt.ItemDataRole.UserRole): editor.item(row).isHidden() for row in range(editor.count())}
        self.assertTrue(names["bluetooth"])
        self.assertTrue(names["battery"])
        self.assertFalse(names["network"])


class IconsAndWidgets(unittest.TestCase):
    AREA = [(0, 0, 400, 300)]

    def test_icon_under_a_widget_moves_to_a_free_spot(self):
        moves = clear_icons([(100, 100), (0, 0)], (100, 100), [(110, 110, 190, 190)], self.AREA)
        self.assertEqual(list(moves), [0])
        target = moves[0]
        self.assertFalse(target == (0, 0))
        self.assertTrue(target[0] >= 190 or target[1] >= 190 or target[0] + 100 <= 110 or target[1] + 100 <= 110)

    def test_two_icons_do_not_land_on_the_same_spot(self):
        moves = clear_icons([(100, 100), (120, 120)], (100, 100), [(90, 90, 230, 230)], self.AREA)
        self.assertEqual(len(set(moves.values())), 2)

    def test_free_icons_stay(self):
        self.assertEqual(clear_icons([(300, 200)], (100, 100), [(0, 0, 100, 100)], self.AREA), {})

    def test_full_desktop_leaves_it(self):
        self.assertEqual(clear_icons([(0, 0)], (100, 100), [(0, 0, 400, 300)], self.AREA), {})

    def test_unreadable_icons_are_skipped(self):
        self.assertEqual(clear_icons([None], (100, 100), [(0, 0, 100, 100)], self.AREA), {})

    def test_stays_on_its_own_monitor(self):
        areas = [(0, 0, 200, 100), (1000, 0, 1400, 300)]
        moves = clear_icons([(1000, 0)], (100, 100), [(1000, 0, 1100, 100)], areas)
        self.assertGreaterEqual(moves[0][0], 1000)


class AppVolume(unittest.TestCase):
    def _app(self, *levels):
        controls = [unittest.mock.Mock() for _ in levels]
        controls[0].GetMasterVolume.return_value = 0.5
        meters = [unittest.mock.Mock(**{"GetPeakValue.return_value": level}) for level in levels]
        return audio_mixer.AppAudio("a", "A", "", controls, meters)

    def test_names_look_like_apps(self):
        self.assertEqual(audio_mixer.app_name("opera.exe"), "Opera")

    def test_several_windows_count_as_one_app(self):
        app = self._app(0.1, 0.7)
        self.assertEqual(app.peak(), 0.7)
        app.set_volume(30)
        for control in app.controls:
            control.SetMasterVolume.assert_called_once_with(0.3, None)

    def test_a_closed_app_is_no_crash(self):
        app = self._app(0.5)
        app.meters[0].GetPeakValue.side_effect = OSError("weg")
        app.controls[0].SetMute.side_effect = OSError("weg")
        self.assertEqual(app.peak(), 0.0)
        app.set_muted(True)

    def test_row_shows_volume_and_level(self):
        host = QWidget()
        row = AppRow(QGridLayout(host), 0, self._app(0.0), "A", "#FFFFFF")
        self.assertEqual(row.slider.value(), 50)
        row.slider.setValue(20)
        row.app.controls[0].SetMasterVolume.assert_called_once_with(0.2, None)
        row.show_level(0.25)
        self.assertEqual(row.meter.value(), 500)
        self.assertEqual(row.state.text(), "spielt")
        row.show_level(0.0)
        self.assertEqual(row.state.text(), "still")
        self.assertGreater(row.meter.value(), 0)  # der Balken fällt weich


class FirstStartDefaults(unittest.TestCase):
    def test_bars_stay_visible(self):
        from config.config_manager import MONITOR_DEFAULTS
        self.assertFalse(MONITOR_DEFAULTS["auto_hide"])
        self.assertFalse(MONITOR_DEFAULTS["topbar_auto_hide"])

    def test_home_menu_sits_above_the_whole_taskbar(self):
        ctx = ResetContext()
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        menu = ctx.start_menu
        menu.show = menu.activateWindow = menu.setFocus = lambda: None
        menu.setFixedHeight(dock.screen().availableGeometry().height())  # zu hoch für unter die Leiste, es muss darüber
        with unittest.mock.patch.object(win_api, "force_foreground"), unittest.mock.patch.object(menu.animator, "enter"):
            menu.show_at(dock.start, 14)
        bar_top = dock.mapToGlobal(QPoint(0, 0)).y()
        self.assertEqual(menu.y() + menu.height() + 14, bar_top)


class ExplorerSidebar(unittest.TestCase):
    def test_saved_order_stays_inside_each_group(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, c = (str(Path(folder) / name) for name in "abc")
            for path in (a, b, c):
                os.mkdir(path)
            fixed = [("A", a), ("B", b)]
            saved = [{"label": "C", "path": c}, {"label": "B", "path": b}, {"label": "A", "path": a}]
            top, own = explorer_app.split_places(fixed, saved)
            self.assertEqual([label for label, _ in top], ["B", "A"])
            self.assertEqual([label for label, _ in own], ["C"])

    def _store(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        patcher = unittest.mock.patch.object(explorer_app, "STORE", Path(folder) / "explorer.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        return Path(folder)

    def test_entries_cannot_cross_between_the_groups(self):
        lists = [explorer_app.PlacesList.__new__(explorer_app.PlacesList) for _ in range(2)]
        event = unittest.mock.Mock()
        event.source.return_value = lists[1]
        self.assertTrue(explorer_app.PlacesList._foreign(lists[0], event))
        event.source.return_value = lists[0]
        self.assertFalse(explorer_app.PlacesList._foreign(lists[0], event))

    def test_stars_survive_a_restart(self):
        folder = self._store()
        path = str(folder / "ordner")
        os.mkdir(path)
        stars = explorer_app.Favorites()
        stars.toggle([path])
        self.assertTrue(stars.has(path.upper()))
        again = explorer_app.Favorites()
        self.assertEqual(again.existing(), [path])

    def test_one_missing_star_marks_all(self):
        folder = self._store()
        first, second = str(folder / "a"), str(folder / "b")
        stars = explorer_app.Favorites()
        stars.toggle([first])
        stars.toggle([first, second])
        self.assertTrue(stars.has(first) and stars.has(second))
        stars.toggle([first, second])
        self.assertFalse(stars.has(first) or stars.has(second))

    def test_star_follows_a_rename(self):
        folder = self._store()
        old, new = str(folder / "alt"), str(folder / "neu")
        stars = explorer_app.Favorites()
        stars.toggle([old])
        stars.rename(old, new)
        self.assertTrue(stars.has(new))
        self.assertFalse(stars.has(old))


class Privacy(unittest.TestCase):
    def test_memory_tool_exists_only_when_allowed(self):
        off = {tool["name"] for tool in nova_tools.declarations({})[0]["function_declarations"]}
        on = {tool["name"] for tool in nova_tools.declarations({"allow_memory": True})[0]["function_declarations"]}
        self.assertNotIn("remember", off)
        self.assertIn("remember", on)

    def test_deleted_memory_stays_gone(self):
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "alt.json"
            old.write_text('[{"id": 1, "text": "Mag Pizza"}]', encoding="utf-8")
            memory = Memory(Path(folder) / "neu" / "memory.json", old)
            self.assertIn("Pizza", memory.as_prompt())
            memory.clear()
            self.assertEqual(memory.as_prompt(), "")
            self.assertEqual(Memory(Path(folder) / "neu" / "memory.json", old).as_prompt(), "")


def load_setup():
    spec = importlib.util.spec_from_file_location("popwin_setup", Path(__file__).resolve().parent.parent / "installer" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SetupFolder(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup()
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_read_only_files_are_no_obstacle(self):
        target = self.folder / "_internal"
        target.mkdir()
        file = target / "alt.dll"
        file.write_text("x")
        os.chmod(file, 0o444)
        self.setup.clear_old(target)
        self.assertFalse(target.exists())

    def test_a_file_still_in_use_moves_aside(self):
        file = self.folder / "PopWin.exe"
        file.write_text("x")
        real = self.setup.remove_path
        with unittest.mock.patch.object(self.setup, "remove_path", side_effect=PermissionError), \
                unittest.mock.patch.object(self.setup, "DELETE_WAIT", 0):
            self.setup.clear_old(file)
        self.assertFalse(file.exists())
        leftovers = list(self.folder.glob(".alt-*"))
        self.assertEqual(len(leftovers), 1)
        self.setup.sweep_aside(self.folder)
        self.assertEqual(list(self.folder.glob(".alt-*")), [])
        self.assertTrue(callable(real))

    def test_old_nova_goes_and_her_memory_stays(self):
        local = self.folder / "local"
        old = local / "CopilotPro"
        (old / "webview").mkdir(parents=True)
        (old / "memory.json").write_text("[1]", encoding="utf-8")
        program = self.folder / "app" / "Nova"
        program.mkdir(parents=True)
        new_memory = self.folder / "roaming" / "nova" / "memory.json"
        self.setup.retire_old_nova(self.folder / "app", local, new_memory)
        self.assertFalse(program.exists())
        self.assertFalse(old.exists())
        self.assertEqual(new_memory.read_text(encoding="utf-8"), "[1]")

    def test_newer_memory_is_not_overwritten(self):
        local = self.folder / "local"
        (local / "CopilotPro").mkdir(parents=True)
        (local / "CopilotPro" / "memory.json").write_text("[1]", encoding="utf-8")
        new_memory = self.folder / "memory.json"
        new_memory.write_text("[2]", encoding="utf-8")
        self.setup.retire_old_nova(self.folder / "app", local, new_memory)
        self.assertEqual(new_memory.read_text(encoding="utf-8"), "[2]")

    def test_write_check_looks_at_the_nearest_folder(self):
        self.assertTrue(self.setup.can_write(self.folder / "neu" / "tief"))

    def test_admin_run_keeps_the_choices(self):
        arguments = self.setup.elevated_arguments(Path(r"C:\Program Files\PopWin"), {"desktop": True, "autostart": False}, False)
        self.assertEqual(arguments, r'--elevated "C:\Program Files\PopWin" --desktop --no-launch')


class NovaSpace(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.panel = self.ctx.nova_panel
        self.ctx.config.data["nova"]["reserve_space"] = True
        self.panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def test_nova_reserves_her_side_and_gives_it_back(self):
        with unittest.mock.patch.object(win_api, "appbar_register") as register, \
                unittest.mock.patch.object(win_api, "appbar_set_pos") as place, \
                unittest.mock.patch.object(win_api, "appbar_remove") as remove:
            self.panel._place()
            register.assert_called_once()
            self.assertEqual(place.call_args[0][1], "right")
            self.panel._release_space()
            remove.assert_called_once()

    def test_left_side(self):
        self.ctx.config.data["nova"]["side"] = "left"
        with unittest.mock.patch.object(win_api, "appbar_register"), \
                unittest.mock.patch.object(win_api, "appbar_set_pos") as place, unittest.mock.patch.object(win_api, "appbar_remove"):
            self.assertTrue(self.panel._place())
            self.assertEqual(place.call_args[0][1], "left")
            self.panel._release_space()

    def test_no_reservation_when_turned_off(self):
        self.ctx.config.data["nova"]["reserve_space"] = False
        with unittest.mock.patch.object(win_api, "appbar_register") as register:
            self.panel._place()
        register.assert_not_called()

    def test_chosen_screen_wins(self):
        screen = QGuiApplication.screens()[-1]
        self.ctx.config.data["nova"]["screen"] = screen_key(screen)
        self.assertIs(self.panel.target_screen(), screen)
        self.ctx.config.data["nova"]["screen"] = "weg"
        self.assertIsNotNone(self.panel.target_screen())

    def test_orbit_symbol_follows_light_and_dark(self):
        image = glyph_icon(G.NOVA, 40, "#FFFFFF").pixmap(40, 40).toImage()
        self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2).red(), 255)  # der Planet in der Mitte
        self.assertEqual(image.pixelColor(2, 2).alpha(), 0)  # außen frei
        button = NovaButton(self.ctx, 24)
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.text(), "")


class Autostart(unittest.TestCase):
    def test_command_carries_the_flag(self):
        self.assertEqual(win_api.autostart_command(r"C:\P\PopWin.exe", ""), r'"C:\P\PopWin.exe" --autostart')
        self.assertEqual(win_api.autostart_command("python", '"main.py"'), '"python" "main.py" --autostart')

    def test_task_quotes_survive_schtasks(self):
        arguments = win_api.logon_task_arguments(r"C:\P F\PopWin.exe", "")
        self.assertIn(r'/TR "\"C:\P F\PopWin.exe\" --autostart"', arguments)
        self.assertIn("/RL LIMITED", arguments)

    def test_shell_wait_ends_when_the_taskbar_exists(self):
        with unittest.mock.patch.object(win_api.user32, "FindWindowW", return_value=1):
            win_api.wait_for_shell()


class WinKeyHook(unittest.TestCase):
    def _press(self, hooks, key, held):
        info = KBDLLHOOKSTRUCT(vkCode=key)
        with unittest.mock.patch.object(ShellHooks, "_win_held", return_value=held), \
                unittest.mock.patch.object(ShellHooks, "_other_modifier_held", return_value=False), \
                unittest.mock.patch("core.shell_hooks.user32.CallNextHookEx", return_value=0), \
                unittest.mock.patch("core.shell_hooks.win_api.send_keys"):
            return hooks._on_key(0, WM_KEYDOWN, ctypes.addressof(info))

    def _hooks(self):
        hooks = ShellHooks("alt+space", False, True, False)
        self.opened = []
        hooks.launcher_requested.connect(lambda: self.opened.append(1), Qt.ConnectionType.DirectConnection)
        return hooks

    def test_win_s_opens_the_search(self):
        hooks = self._hooks()
        self._press(hooks, VK_LWIN, True)
        self.assertEqual(self._press(hooks, VK_S, True), 1)
        self.assertEqual(self.opened, [1])

    def test_s_works_after_a_lost_win_release(self):
        """Win+L: Das Loslassen landet auf dem Sperrbildschirm, der Merker bliebe gesetzt."""
        hooks = self._hooks()
        self._press(hooks, VK_LWIN, True)
        self.assertEqual(self._press(hooks, VK_S, False), 0)
        self.assertEqual(self.opened, [])
        self.assertFalse(hooks._win_down)


class TileThemes(unittest.TestCase):
    """Die Kachel-Stile: Jedes Taskbar-Icon in einer eigenen Kachel, abschaltbar und auch für andere Stile."""

    TILED = ("tiles_glass", "tiles_obsidian", "tiles_nova", "tiles_frost", "tiles_neon", "tiles_amber",
             "tiles_graphite", "tiles_ocean", "glass_clear", "glass_rose", "glass_emerald", "glass_prism", "glass_smoke", "glass_liquid")

    def test_all_eight_are_offered_and_saved(self):
        from config.config_manager import THEMES
        for name in self.TILED:
            self.assertIn(name, PRESETS)
            self.assertIn(name, THEMES)
            self.assertTrue((ROOT_PATH / "themes" / f"{name}.qss").exists(), name)

    def test_tile_styles_frame_every_taskbar_icon(self):
        ctx = ResetContext()
        for name in self.TILED:
            meta = ctx.theme.meta(name)
            self.assertEqual(meta["tiles"], "1", name)
            self.assertGreaterEqual(int(meta["dock"]), 58, name)  # etwas größer, damit die Kachel Luft hat
            qss = ctx.theme.stylesheet(name)
            for target in ("#Bar #TaskButton", "#Bar #StartButton", "#Bar #Trash", '#Bar #TaskButton[focused="true"]'):
                self.assertIn(target, qss, name)
        self.assertFalse(ctx.theme.tiles("pop_basic"))

    def test_tiles_can_be_switched_on_and_off(self):
        ctx = ResetContext()
        plain = ctx.theme.stylesheet("liquid_glass")
        tiled = ctx.theme.stylesheet("tiles_nova")
        ctx.theme.set_tile_mode("on")
        self.assertTrue(ctx.theme.tiles("liquid_glass"))
        self.assertIn("#Bar #TaskButton", ctx.theme.stylesheet("liquid_glass"))
        self.assertEqual(ctx.theme.stylesheet("tiles_nova"), tiled)
        ctx.theme.set_tile_mode("off")
        self.assertFalse(ctx.theme.tiles("tiles_nova"))
        self.assertTrue(ctx.theme.stylesheet("tiles_nova").endswith(theme_manager.TILES_OFF))
        self.assertEqual(ctx.theme.stylesheet("liquid_glass"), plain)
        ctx.theme.set_tile_mode("kaputt")
        self.assertEqual(ctx.theme.tile_mode, "off")
        ctx.theme.set_tile_mode("theme")

    def test_fully_round_tiles_only_where_tiles_are(self):
        ctx = ResetContext()
        ctx.theme.set_tile_shape("round")
        self.assertTrue(ctx.theme.stylesheet("tiles_ocean").endswith(theme_manager.TILES_ROUND))
        self.assertNotIn(theme_manager.TILES_ROUND, ctx.theme.stylesheet("pop_basic"))
        ctx.theme.set_tile_mode("on")
        self.assertTrue(ctx.theme.stylesheet("pop_basic").endswith(theme_manager.TILES_ROUND))
        ctx.theme.set_tile_shape("eckig")
        self.assertEqual(ctx.theme.tile_shape, "round")
        ctx.theme.set_tile_shape("theme")
        ctx.theme.set_tile_mode("theme")
        self.assertNotIn(theme_manager.TILES_ROUND, ctx.theme.stylesheet("tiles_ocean"))

    def test_every_tile_style_has_its_own_look(self):
        ctx = ResetContext()
        looks, languages = set(), set()
        for name in self.TILED:
            qss = ctx.theme.stylesheet(name)
            tile = next(line for line in qss.splitlines() if line.startswith("#Bar #TaskButton, #Bar #StartButton, #Bar #Trash {"))
            language = qss[qss.index("Designsprache von"):qss.index("Kacheln um jedes Icon")]
            self.assertIn("QSlider::handle", language, name)  # eigene Regler, Knöpfe und Menüs
            self.assertIn("QMenu", language, name)
            looks.add(tile)
            languages.add(language)
        self.assertEqual(len(looks), len(self.TILED))
        self.assertEqual(len(languages), len(self.TILED))

    def test_frost_stays_readable_in_dark_mode(self):
        """Weiße Schrift braucht eine dunkle Tönung, sonst verschwindet sie auf hellen Hintergründen."""
        ctx = ResetContext()
        ctx.theme.set_mode("dark")
        tint = ctx.theme.tint("tiles_frost")
        red, green, blue, alpha = (int(tint[i:i + 2], 16) for i in (1, 3, 5, 7))
        self.assertLess((red + green + blue) / 3, 80)
        self.assertGreater(alpha, 0x80)

    def test_light_mode_rules_never_leak_into_dark_mode(self):
        """In „[popMode=light] A, B“ gälte der Vorsatz nur für A, B bekäme die helle Kachel auch im Dunkel-Modus."""
        for path in (ROOT_PATH / "themes").glob("*.qss"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.startswith('[popMode="light"]') or "{" not in line:
                    continue
                for part in line.split("{", 1)[0].split(","):
                    self.assertIn("popMode", part, f"{path.name}: {line[:80]}")

    def test_icons_on_bright_active_tiles_are_dark(self):
        ctx = ResetContext()
        for name in ("tiles_obsidian", "tiles_frost"):
            self.assertLess(QColor(ctx.theme.meta(name)["quick_on"]).lightness(), 60, name)

    def test_only_the_new_styles_round_the_taskbar_more(self):
        """Windows rundet nur 8 px. Die Kachel-Stile schneiden die Taskbar runder zu, die alten Stile bleiben, wie sie sind."""
        ctx = ResetContext()
        ctx.config.set("theme", "pop_basic")
        dock = Dock(ctx, app.primaryScreen(), "bottom")
        dock.resize(600, 62)
        for name in ("liquid_glass", "pop_basic", "clean", "minimalist"):
            dock.set_theme_name(name)
            dock._round_corners()
            self.assertEqual(dock.corner_radius(), 0, name)
            self.assertTrue(dock.mask().isEmpty(), name)
        for name in self.TILED:
            dock.set_theme_name(name)
            dock._round_corners()
            self.assertGreater(dock.corner_radius(), 8, name)
            self.assertLessEqual(dock.corner_radius(), dock.height() // 2, name)
            self.assertFalse(dock.mask().isEmpty(), name)
            self.assertFalse(dock.mask().contains(QPoint(0, 0)), name)  # die Ecke ist weg, Klicks gehen an Windows
            self.assertTrue(dock.mask().contains(dock.rect().center()), name)
            # Kein eckiger Windows-Blur dahinter: PopWin malt den Hintergrund selbst rund, die Ecke bleibt leer.
            self.assertTrue(dock.paints_backdrop(), name)
            self.assertGreaterEqual(dock.backdrop_color().alpha(), 0xE0, name)
            image = dock.grab().toImage()
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0, name)
            self.assertGreater(image.pixelColor(image.width() // 2, 2).alpha(), 0, name)
        for name in ("liquid_glass", "pop_basic"):
            dock.set_theme_name(name)
            self.assertFalse(dock.paints_backdrop(), name)
        dock.set_theme_name(self.TILED[0])
        ctx.theme.set_tile_shape("round")
        self.assertEqual(dock.corner_radius(), min(dock.height(), dock.width()) // 2)  # „Komplett rund“: halbe Höhe, also eine Pille
        ctx.theme.set_tile_shape("theme")
        dock.close()

    def test_no_square_windows_backdrop_stays_behind_round_styles(self):
        """Weder ein Theme-Wechsel noch eine Taskbar-Einstellung darf Blur oder Windows-Ecken zurückholen,
        sonst bleibt hinter der runden Leiste ein eckiges Rechteck stehen."""
        ctx = ResetContext()
        ctx.config.set("theme", "tiles_graphite")
        dock = Dock(ctx, app.primaryScreen(), "bottom")
        dock.resize(600, 62)
        with unittest.mock.patch.object(win_api, "set_blur") as blur, \
                unittest.mock.patch.object(win_api, "set_rounded") as rounded:
            dock.apply_theme()
            dock._on_config("taskbar.icon_spacing")
            self.assertEqual(blur.call_args.args[1], "none")
            self.assertFalse(rounded.call_args.args[1])
            dock.set_theme_name("liquid_glass")
            dock._on_config("taskbar.icon_spacing")
            self.assertNotEqual(blur.call_args.args[1], "none")  # die alten Stile behalten ihren Blur
        dock.close()


class LightMode(unittest.TestCase):
    def test_every_theme_marks_results_and_menus_for_light_mode(self):
        for name in PRESETS:
            qss = (ROOT_PATH / "themes" / f"{name}.qss").read_text(encoding="utf-8")
            self.assertIn('[popMode="light"] #Results::item:selected', qss if name != "pop_basic" else
                          '[popMode="light"] #Results::item:selected', name)
            self.assertIn('QMenu[popMode="light"]::item:selected', qss, name)

    def test_qt_colors_follow_popwin_not_windows(self):
        ctx = ResetContext()
        window = QuickPanel(ctx, "")
        hints = QGuiApplication.styleHints()
        before = hints.colorScheme()
        self.addCleanup(hints.setColorScheme, before)
        ctx.config.data["mode"] = "light"
        window.apply_theme()
        self.assertEqual(hints.colorScheme(), Qt.ColorScheme.Light)
        ctx.config.data["mode"] = "dark"
        window.apply_theme()
        self.assertEqual(hints.colorScheme(), Qt.ColorScheme.Dark)


class TaskbarIcons(unittest.TestCase):
    """Welches Icon ein Fenster bekommt, in der Reihenfolge der Windows-Taskleiste."""

    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.windows = lambda: []
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")
        self.asked = []

    def tearDown(self):
        self.dock.hide()

    def icon(self, target):
        self.asked.append(target)
        return self.marker if target == self.hit else QIcon()

    def resolve(self, group, hit, window_icon=False, generic=False):
        self.hit = hit
        pixmap = QPixmap(8, 8)
        pixmap.fill(QColor("red"))
        self.marker = QIcon(pixmap)
        with unittest.mock.patch("ui.taskbar.icon_for_target", side_effect=self.icon), \
                unittest.mock.patch.object(Dock, "_window_icon", return_value=self.marker if window_icon else QIcon()) as from_window, \
                unittest.mock.patch.object(win_api, "has_generic_icon", return_value=generic):
            icon = self.dock._icon(group)
        return icon, from_window

    def test_app_id_beats_the_exe(self):
        window = win_api.WindowInfo(1, "Rechner", r"C:\Apps\CalculatorApp.exe", 4, aumid="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
        icon, _ = self.resolve({"target": window.exe, "name": "", "windows": [window]}, "shell:AppsFolder\\" + window.aumid)
        self.assertIs(icon, self.marker)
        self.assertEqual(self.asked, ["shell:AppsFolder\\" + window.aumid])

    def test_java_shows_the_game_icon(self):
        window = win_api.WindowInfo(1, "Minecraft", r"C:\Java\bin\javaw.exe", 4)
        icon, _ = self.resolve({"target": window.exe, "name": "", "windows": [window]}, window.exe, window_icon=True)
        self.assertIs(icon, self.marker)
        self.assertEqual(self.asked, [])  # das Java-Icon wurde gar nicht erst gefragt

    def test_generic_exe_falls_back_to_the_window(self):
        window = win_api.WindowInfo(1, "Tool", r"C:\x\tool.exe", 4)
        icon, _ = self.resolve({"target": window.exe, "name": "", "windows": [window]}, "", window_icon=True, generic=True)
        self.assertIs(icon, self.marker)

    def test_pinned_keeps_the_shortcut_icon(self):
        window = win_api.WindowInfo(1, "Minecraft", r"C:\Java\bin\javaw.exe", 4)
        icon, from_window = self.resolve({"target": r"C:\x\Minecraft.lnk", "name": "Minecraft", "windows": [window], "pinned": True},
                                         r"C:\x\Minecraft.lnk", window_icon=True)
        self.assertIs(icon, self.marker)
        from_window.assert_not_called()

    def test_nothing_found_gives_the_app_glyph(self):
        window = win_api.WindowInfo(1, "?", "", 4)
        icon, _ = self.resolve({"target": "", "name": "", "windows": [window]}, "never")
        self.assertFalse(icon.isNull())

    def test_generic_icon_is_recognised(self):
        with tempfile.TemporaryDirectory() as folder:
            bare = Path(folder) / "ohne_icon.exe"
            bare.write_bytes(b"MZ")  # keine Icon-Ressource: Windows zeigt das Standardsymbol
            self.assertTrue(win_api.has_generic_icon(str(bare)))
        self.assertFalse(win_api.has_generic_icon(r"C:\Windows\explorer.exe"))


class WebApps(unittest.TestCase):
    ID = "15EF7777.Crunchyroll_mgdgtskya6f22!App"
    EDGE = r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"

    def _dock(self, pins=()):
        ctx = ResetContext()
        ctx.config.set("taskbar.pinned", list(pins))
        ctx.windows = lambda: [win_api.WindowInfo(5, "Crunchyroll - Watch", self.EDGE, 4, 0, "", self.ID), win_api.WindowInfo(6, "Google", self.EDGE, 4)]
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        dock.refresh(True)
        return dock

    def test_only_real_web_apps_count(self):
        self.assertTrue(win_api.is_web_app_id(self.ID))
        self.assertTrue(win_api.is_web_app_id("MSEdge._crx_hjlhbeff"))
        self.assertFalse(win_api.is_web_app_id("MSEdge"))
        self.assertFalse(win_api.is_web_app_id("Chrome.UserData.Default"))

    def test_only_browsers_make_web_apps(self):
        self.assertEqual(win_api.web_app_id(r"C:\x\notepad.exe", self.ID), "")
        self.assertEqual(win_api.web_app_id(self.EDGE, self.ID), self.ID)
        self.assertEqual(win_api.web_app_id(self.EDGE, "MSEdge"), "")


    def test_names(self):
        self.assertEqual(web_app_name(self.ID, "egal"), "Crunchyroll")
        self.assertEqual(web_app_name("MSEdge._crx_abc", "YouTube - Startseite"), "YouTube")

    def test_web_app_sits_apart_from_the_browser(self):
        dock = self._dock()
        self.assertEqual(sorted(dock._buttons), ["msedge.exe", f"webapp:{self.ID.lower()}"])
        button = dock._buttons[f"webapp:{self.ID.lower()}"]
        self.assertEqual(button.target, f"shell:AppsFolder\\{self.ID}")
        self.assertEqual(button.display_name(), "Crunchyroll")
        self.assertEqual(len(dock._buttons["msedge.exe"].windows), 1)

    def test_web_app_lands_on_its_pin(self):
        dock = self._dock([{"name": "Crunchyroll", "target": r"C:\\x\\Crunchyroll.lnk"}])
        self.assertEqual(sorted(dock._buttons), ["crunchyroll.lnk", "msedge.exe"])
        self.assertEqual(len(dock._buttons["crunchyroll.lnk"].windows), 1)


class WinKeyInFullscreen(unittest.TestCase):
    def _bar(self):
        ctx = ResetContext()
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        dock._fullscreen = True
        dock._on_top = False
        return dock

    def test_peek_brings_the_bar_forward_and_lets_it_go_again(self):
        dock = self._bar()
        with unittest.mock.patch.object(win_api, "set_zorder") as order:
            dock.peek()
            self.assertEqual(order.call_args[0][1], win_api.HWND_TOPMOST)
            dock._peek_until = time.monotonic() - 1
            with unittest.mock.patch.object(dock, "_keeps_open", return_value=False):
                dock._place_in_z()
            self.assertEqual(order.call_args[0][1], win_api.HWND_BOTTOM)
        dock.hide()

    def test_open_home_menu_keeps_it_forward(self):
        dock = self._bar()
        with unittest.mock.patch.object(win_api, "set_zorder") as order:
            dock.peek()
            dock._peek_until = time.monotonic() - 1
            with unittest.mock.patch.object(dock, "_keeps_open", return_value=True):
                dock._place_in_z()
        self.assertEqual(order.call_count, 1)
        dock.hide()

    def test_win_key_peeks_every_bar_on_that_screen(self):
        ctx = ResetContext()
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        ctx.bars = [dock]
        ctx.config.set("start_menu.win_key", "home")
        with unittest.mock.patch.object(dock, "peek") as peek, unittest.mock.patch.object(ctx, "_home_dock", return_value=dock), \
                unittest.mock.patch.object(dock, "show_then"):
            ctx._on_win_key()
        peek.assert_called_once()
        dock.hide()


class AutostartAndSessionEnd(unittest.TestCase):
    def test_session_end_gives_the_taskbar_back(self):
        ctx = ResetContext()
        ctx._taskbar_hidden = True
        ctx.config.set("shell.saved_taskbar_state", 2, notify=False)
        with unittest.mock.patch.object(win_api, "show_native_taskbar") as show, unittest.mock.patch("main.QTimer.singleShot") as later:
            ctx.session_ending()
        show.assert_called_once_with(2)
        self.assertFalse(ctx._taskbar_hidden)
        later.assert_called_once()  # bricht man ab, kommt PopWin zurück

    def test_session_end_without_hidden_taskbar_does_nothing(self):
        ctx = ResetContext()
        ctx._taskbar_hidden = False
        with unittest.mock.patch.object(win_api, "show_native_taskbar") as show:
            ctx.session_ending()
        show.assert_not_called()

    def test_startup_delay_values_only_go_away_when_popwin_set_them(self):
        store = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def query(_key, name):
            if name not in store:
                raise OSError(name)
            return store[name], 4

        with unittest.mock.patch.multiple("core.win_api.winreg", OpenKey=lambda *a: Key(), CreateKey=lambda *a: Key(), QueryValueEx=query,
                                          SetValueEx=lambda _k, name, _r, _t, value: store.__setitem__(name, value),
                                          DeleteValue=lambda _k, name: store.pop(name)):
            win_api.restore_startup_delay()
            self.assertEqual(store, {})
            win_api.clear_startup_delay()
            self.assertEqual(store, {"StartupDelayInMSec": 0, "WaitForIdleState": 0, "PopWinSetThis": 1})
            win_api.restore_startup_delay()
            self.assertEqual(store, {})
            store.update({"StartupDelayInMSec": 0, "WaitForIdleState": 0})  # vom Nutzer selbst gesetzt
            win_api.restore_startup_delay()
            self.assertEqual(store, {"StartupDelayInMSec": 0, "WaitForIdleState": 0})


class WallpaperRecovery(unittest.TestCase):
    def _player(self):
        ctx = ResetContext()
        ctx.config.set("wallpaper.enabled", True)
        ctx.config.set("wallpaper.file", "preset:linien")
        ctx.status.state["wallpaper_engine"] = wallpapers.MISSING
        return ctx, wallpapers.WallpaperPlayer(ctx)

    def test_broken_file_stops_restarting(self):
        _ctx, player = self._player()
        with unittest.mock.patch("core.wallpapers.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 1
            for _ in range(8):
                player.sync()
        self.assertEqual(popen.call_count, wallpapers.MAX_FAILURES)

    def test_new_choice_tries_again(self):
        ctx, player = self._player()
        with unittest.mock.patch("core.wallpapers.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 1
            for _ in range(5):
                player.sync()
            ctx.config.set("wallpaper.file", "preset:wellen")
            player.sync()
        self.assertIn("wellen.mp4", str(popen.call_args))
        self.assertEqual(player.failures, 0)

    def test_clean_exit_restarts_without_counting(self):
        _ctx, player = self._player()
        with unittest.mock.patch("core.wallpapers.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 0  # neue Bildschirme
            for _ in range(6):
                player.sync()
        self.assertEqual(popen.call_count, 6)
        self.assertEqual(player.failures, 0)

    def test_stopping_redraws_the_normal_wallpaper(self):
        _ctx, player = self._player()
        player.process = unittest.mock.Mock()
        player.process.poll.return_value = None
        with unittest.mock.patch.object(win_api, "redraw_wallpaper") as redraw:
            player.stop()
        redraw.assert_called_once()

    def test_picking_turns_it_on(self):
        ctx = ResetContext()
        settings = ctx.settings
        item = QListWidgetItem("x")
        item.setData(Qt.ItemDataRole.UserRole, "preset:kristall")
        settings._pick_wallpaper(item)
        self.assertTrue(ctx.config.get("wallpaper.enabled"))
        self.assertEqual(ctx.config.get("wallpaper.source"), "auto")

    def test_engine_is_only_closed_after_yes(self):
        ctx = ResetContext()
        ctx.status.state["wallpaper_engine"] = wallpapers.RUNNING
        item = QListWidgetItem("x")
        item.setData(Qt.ItemDataRole.UserRole, "preset:kristall")
        with unittest.mock.patch("ui.settings_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            ctx.settings._pick_wallpaper(item)
        self.assertEqual(ctx.config.get("wallpaper.source"), "auto")
        with unittest.mock.patch("ui.settings_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                unittest.mock.patch("core.wallpapers.WallpaperPlayer.close_engine") as close:
            ctx.settings._pick_wallpaper(item)
        close.assert_called_once()
        self.assertEqual(ctx.config.get("wallpaper.source"), "popwin")


class Wallpapers(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = unittest.mock.patch.object(wallpapers, "FOLDER", self.folder / "wallpapers")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_engine_is_seen_running_installed_or_missing(self):
        library = self.folder / "steam"
        self.assertEqual(wallpapers.engine_state([library], set()), wallpapers.MISSING)
        (library / "steamapps").mkdir(parents=True)
        (library / "steamapps" / "appmanifest_431960.acf").write_text("x", encoding="utf-8")
        self.assertEqual(wallpapers.engine_state([library], set()), wallpapers.INSTALLED)
        self.assertEqual(wallpapers.engine_state([], {"wallpaper64.exe"}), wallpapers.RUNNING)

    def test_only_media_is_accepted(self):
        source = self.folder / "virus.exe"
        source.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            wallpapers.add_file(str(source))

    def test_files_never_overwrite_or_leave_the_folder(self):
        source = self.folder / "Welle.MP4"
        source.write_text("x", encoding="utf-8")
        self.assertEqual(wallpapers.add_file(str(source)).name, "Welle.mp4")
        self.assertEqual(wallpapers.add_file(str(source)).name, "Welle (2).mp4")
        self.assertEqual(wallpapers.free_path("..\\..\\böse.gif").parent, wallpapers.FOLDER)
        self.assertEqual(len(wallpapers.library()), 2)

    def test_link_must_be_a_video(self):
        self.assertEqual(wallpapers.name_from_url("https://x.de/a%20b.webm?y=1", "text/html"), "a b.webm")
        self.assertEqual(wallpapers.name_from_url("https://x.de/hol", "video/mp4"), "hintergrund.mp4")
        with self.assertRaises(ValueError):
            wallpapers.name_from_url("https://x.de/seite", "text/html")

    def test_only_web_links_are_loaded(self):
        job = wallpapers.WallpaperDownload("file:///C:/geheim.mp4")
        seen = []
        job.finished_with.connect(lambda file, error: seen.append((file, error)), Qt.ConnectionType.DirectConnection)
        job.run()
        self.assertEqual(seen[0][0], "")
        self.assertIn("http", seen[0][1])

    def _player(self, file, engine):
        ctx = ResetContext()
        ctx.config.data["wallpaper"].update(enabled=True, file=str(file))
        ctx.status.state["wallpaper_engine"] = engine
        return ctx, wallpapers.WallpaperPlayer(ctx)

    def test_nothing_is_set_without_a_choice(self):
        ctx = ResetContext()
        self.assertFalse(ctx.config.get("wallpaper.enabled"))
        self.assertEqual(wallpapers.WallpaperPlayer(ctx).wanted(wallpapers.MISSING), ())

    def test_every_preset_exists(self):
        for key in wallpapers.PRESETS:
            self.assertTrue(os.path.exists(wallpapers.media_path(wallpapers.PRESET_PREFIX + key)), key)

    def test_presets_survive_moving_the_install(self):
        self.assertEqual(wallpapers.media_path(r"C:\x.mp4"), r"C:\x.mp4")
        self.assertTrue(wallpapers.media_path("preset:kristall").endswith("kristall.mp4"))

    def test_engine_mode_starts_it_once_and_plays_nothing_own(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        ctx, player = self._player(file, wallpapers.INSTALLED)
        ctx.config.data["wallpaper"]["source"] = wallpapers.ENGINE
        with unittest.mock.patch("subprocess.Popen") as start, unittest.mock.patch("core.wallpapers.win_api.shell_open") as opened:
            player.sync()
            player.sync()
        start.assert_not_called()
        opened.assert_called_once_with(wallpapers.ENGINE_START)

    def test_engine_is_never_closed_on_its_own(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        ctx, player = self._player(file, wallpapers.RUNNING)
        ctx.config.data["wallpaper"]["source"] = wallpapers.OWN
        engine = unittest.mock.Mock(info={"name": "wallpaper64.exe"})
        with unittest.mock.patch("core.wallpapers.psutil.process_iter", return_value=[engine]), unittest.mock.patch("core.wallpapers.subprocess.Popen"):
            for _ in range(5):
                player.sync()
        engine.terminate.assert_not_called()

    def test_closing_the_engine_hits_only_the_engine(self):
        engine = unittest.mock.Mock(info={"name": "wallpaper64.exe"})
        other = unittest.mock.Mock(info={"name": "steam.exe"})
        with unittest.mock.patch("core.wallpapers.psutil.process_iter", return_value=[engine, other]):
            wallpapers.WallpaperPlayer.close_engine()
        engine.terminate.assert_called_once()
        other.terminate.assert_not_called()

    def test_nothing_plays_until_the_engine_check_has_answered(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        _ctx, player = self._player(file, "")
        self.assertEqual(player.wanted(""), ())

    def test_popwin_steps_aside_for_wallpaper_engine(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        _ctx, player = self._player(file, wallpapers.RUNNING)
        with unittest.mock.patch("subprocess.Popen") as start:
            player.sync()
        start.assert_not_called()

    def test_plays_when_the_engine_is_gone(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        _ctx, player = self._player(file, wallpapers.MISSING)
        with unittest.mock.patch("subprocess.Popen") as start:
            start.return_value.poll.return_value = None
            player.sync()
            player.sync()
        start.assert_called_once()
        self.assertIn(str(file), start.call_args[0][0])

    def test_stops_when_turned_off(self):
        file = self.folder / "a.mp4"
        file.write_text("x", encoding="utf-8")
        ctx, player = self._player(file, wallpapers.MISSING)
        with unittest.mock.patch("subprocess.Popen") as start:
            start.return_value.poll.return_value = None
            player.sync()
            ctx.config.data["wallpaper"]["enabled"] = False
            player.sync()
        start.return_value.terminate.assert_called_once()


class EdgeLevels(unittest.TestCase):
    def test_five_levels_get_nearer_and_faster(self):
        self.assertEqual(sorted(EDGE_LEVELS), [1, 2, 3, 4, 5])
        depths = [EDGE_LEVELS[level][0] for level in range(1, 6)]
        delays = [EDGE_LEVELS[level][1] for level in range(1, 6)]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual(delays, sorted(delays, reverse=True))

    def test_slow_levels_wait_at_the_edge(self):
        shown, since = edge_reveals(True, None, 10.0, 1)
        self.assertFalse(shown)
        self.assertTrue(edge_reveals(True, since, 10.5, 1)[0])

    def test_level_five_is_instant(self):
        self.assertTrue(edge_reveals(True, None, 10.0, 5)[0])

    def test_leaving_the_edge_starts_over(self):
        self.assertEqual(edge_reveals(False, 9.0, 10.0, 1), (False, None))


class TaskbarDrag(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.windows = lambda: []
        self.ctx.config.data["animations"]["enabled"] = False
        self.ctx.config.set("taskbar.pinned", [{"name": n, "target": f"{n}.exe", "exe": f"{n}.exe"} for n in "abcd"])
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")
        self.dock.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.dock.show()
        self.dock.refresh(True)
        self.dock.resize(600, 60)
        self.dock.apps.activate()
        QApplication.processEvents()
        self.buttons = patch = unittest.mock.patch("ui.taskbar.QApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton)
        patch.start()
        self.addCleanup(patch.stop)

    def _to(self, button, x):
        self.dock.drag_task(button, button.parentWidget().mapToGlobal(QPoint(x, 0)).x(), button.mapToGlobal(QPoint(0, 0)).x())

    def test_rebuild_cannot_take_the_icon_out_of_the_hand(self):
        keys = list(self.dock._buttons)
        button = self.dock._buttons[keys[0]]
        self._to(button, self.dock._buttons[keys[2]].x() + 5)
        self.dock.refresh(True)
        self.assertIs(self.dock._buttons[keys[0]], button)
        self.assertIs(self.dock._dragged, button)

    def test_icon_follows_the_mouse_and_can_go_back_again(self):
        keys = list(self.dock._buttons)
        button = self.dock._buttons[keys[0]]
        start = button.x()
        far = self.dock._buttons[keys[3]].x()
        self._to(button, far)
        self.assertEqual(self.dock._order[:4][-1], keys[0])
        self._to(button, start)
        self.assertEqual(self.dock._shown_keys()[0], keys[0])
        self.dock.finish_drag()
        self.assertIsNone(self.dock._dragged)
        self.assertEqual(self.dock.apps.itemAt(0).widget(), button)

    def test_drop_saves_and_keeps_the_same_buttons(self):
        keys = list(self.dock._buttons)
        button = self.dock._buttons[keys[0]]
        others = dict(self.dock._buttons)
        self._to(button, self.dock._buttons[keys[3]].x())
        self.dock.finish_drag()
        self.assertEqual(self.dock.apps.itemAt(self.dock.apps.count() - 1).widget(), button)
        self.assertEqual(self.dock._buttons, others)  # kein Neuaufbau, also kein Aufblitzen
        saved = self.ctx.config.get("taskbar.order")[self.dock.screen_id]
        self.assertEqual(saved[:4][-1], keys[0])

    def test_lost_release_ends_the_drag(self):
        keys = list(self.dock._buttons)
        button = self.dock._buttons[keys[0]]
        self._to(button, 50)
        self.buttons.stop()
        with unittest.mock.patch("ui.taskbar.QApplication.mouseButtons", return_value=Qt.MouseButton.NoButton):
            self.dock.refresh()
        self.buttons.start()
        self.assertIsNone(self.dock._dragged)

    def test_other_taskbars_follow_live(self):
        self.ctx.config.data["taskbar"]["sync_icons"] = True
        twin = unittest.mock.Mock(spec=Dock)
        with unittest.mock.patch.object(Dock, "_other_docks", return_value=[twin]):
            keys = list(self.dock._buttons)
            button = self.dock._buttons[keys[0]]
            self._to(button, self.dock._buttons[keys[3]].x())
            twin.preview_order.assert_called()
            self.assertEqual(twin.preview_order.call_args[0][0][-1], keys[0])
            self.dock.finish_drag()
        self.assertFalse(twin._order_held)
        twin.refresh.assert_called()


class TaskIndicator(unittest.TestCase):
    def test_sits_halfway_between_icon_and_bar_edge(self):
        y = mark_y(5, 32, 26, 42, 3)
        above = y - 34
        below = 41 - (y + 3)
        self.assertLessEqual(abs(above - below), 1)
        self.assertGreater(above, 0)

    def test_dots_are_centered_too(self):
        y = mark_y(7, 26, 24, 40, 6)
        self.assertLessEqual(abs((y - 32) - (39 - (y + 6))), 1)

    def test_marks_lie_over_the_icons_and_let_clicks_through(self):
        ctx = ResetContext()
        ctx.windows = lambda: []
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        self.assertTrue(dock.marks.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))


class FavoritesList(unittest.TestCase):
    def test_right_click_removes_and_the_list_follows(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        store = unittest.mock.patch.object(explorer_app, "STORE", Path(folder) / "explorer.json")
        store.start()
        self.addCleanup(store.stop)
        stars = unittest.mock.patch.object(explorer_app, "FAVORITES", explorer_app.Favorites())
        stars.start()
        self.addCleanup(stars.stop)
        first, second = str(Path(folder) / "a"), str(Path(folder) / "b")
        os.mkdir(first)
        os.mkdir(second)
        explorer_app.FAVORITES.toggle([first, second])
        pane = Pane(ResetContext(), folder)
        pane.show_favorites()
        self.assertEqual(pane.results.count(), 2)
        self.assertEqual(pane.results.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        pane.results.item(0).setSelected(True)
        self.assertEqual(pane.selection(), [first])
        menu = unittest.mock.Mock()
        pane._file_items(menu, pane.selection())
        labels = [call.args[0] for call in menu.add_item.call_args_list]
        self.assertIn("Aus Favoriten entfernen", labels)
        pane.toggle_favorite([first])
        self.assertEqual([pane.results.item(i).toolTip() for i in range(pane.results.count())], [second])


class NetworkAndPerformance(unittest.TestCase):
    def test_speed_is_in_megabits(self):
        self.assertEqual(net_stats.megabits(12_500_000, 1.0), 100.0)

    def test_history_survives_and_resets(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        with unittest.mock.patch.object(net_stats, "DATA_DIR", folder):
            ctx = ResetContext()
            history = net_stats.SpeedHistory(ctx)
            history._done({"time": "2026-11-01T18:00:00", "down": 100.0, "up": 20.0, "ping": 12.0}, "")
            self.assertEqual(len(net_stats.SpeedHistory(ctx).entries), 1)
            history.reset()
            self.assertEqual(net_stats.SpeedHistory(ctx).entries, [])

    def test_failed_test_stores_nothing(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        with unittest.mock.patch.object(net_stats, "DATA_DIR", folder):
            history = net_stats.SpeedHistory(ResetContext())
            seen = []
            history.failed.connect(seen.append)
            history._done({}, "kein Netz")
        self.assertEqual((history.entries, seen), ([], ["kein Netz"]))

    def test_hourly_test_is_off_by_default(self):
        ctx = ResetContext()
        history = net_stats.SpeedHistory(ctx)
        with unittest.mock.patch.object(history, "run") as run:
            history._hourly()
        run.assert_not_called()

    def test_parts_of_popwin_are_named(self):
        root = r"c:\popwin"
        with unittest.mock.patch.object(net_stats, "FROZEN", True):
            self.assertEqual(net_stats.popwin_part([r"C:\PopWin\PopWin.exe"], root), "")
            self.assertEqual(net_stats.popwin_part([r"C:\PopWin\PopWin.exe", "--app", "editor"], root), "editor")
            self.assertIsNone(net_stats.popwin_part([r"C:\Andere\app.exe"], root))

    def test_memory_log_stays_small(self):
        log = net_stats.PerformanceLog()
        with unittest.mock.patch.object(net_stats, "popwin_memory", return_value={"PopWin": 100.0, "Explorer": 50.5}):
            for _ in range(net_stats.PERFORMANCE_POINTS + 5):
                log.sample([])
        self.assertEqual(len(log.points), net_stats.PERFORMANCE_POINTS)
        self.assertEqual(log.points[-1], 150.5)

    def test_memory_reads_each_command_line_only_once(self):
        net_stats._parts.clear()
        self.addCleanup(net_stats._parts.clear)
        processes = [(10, "PopWin.exe", 0, 100 * 1024 ** 2), (11, "chrome.exe", 0, 900 * 1024 ** 2)]
        with unittest.mock.patch.object(net_stats.psutil, "Process") as process, \
                unittest.mock.patch.object(net_stats, "popwin_part", return_value=""):
            first = net_stats.popwin_memory(processes)
            net_stats.popwin_memory(processes)
        self.assertEqual(first, {"PopWin": 100.0})
        process.assert_called_once_with(10)  # fremde Programme werden gar nicht erst gefragt

    def test_memory_forgets_closed_processes(self):
        net_stats._parts.clear()
        self.addCleanup(net_stats._parts.clear)
        net_stats._parts[99] = ""
        net_stats.popwin_memory([])
        self.assertEqual(net_stats._parts, {})

    def test_rebuilt_editor_does_not_answer_anymore(self):
        ctx = ResetContext()
        editor = TopItemsEditor(ctx, next(iter(ctx.config.get("monitors") or {"x": 1})))
        with unittest.mock.patch("ui.pin_editor.isValid", return_value=False), \
                unittest.mock.patch.object(editor, "hide_missing") as hide:
            editor._state_changed({})
        hide.assert_not_called()

    def test_chart_scale_leaves_room_above_the_peak(self):
        chart = LineChart("MB")
        chart.set_lines([([100.0, 200.0], "#FFFFFF")])
        self.assertGreater(chart.top_value(), 200)
        chart.grab()

    def test_store_apps_use_their_package(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "WindowsApps" / "5319275A.WhatsAppDesktop_2.2640.100.0_x64__cv1g1gvanyjgm"
            package.mkdir(parents=True)
            (package / "AppxManifest.xml").write_text('<Applications><Application Id="App" Executable="x"/></Applications>', encoding="utf-8")
            target = win_api.packaged_app_target(str(package / "WhatsApp.Root.exe"))
        self.assertEqual(target, r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
        self.assertEqual(win_api.packaged_app_target(r"C:\Windows\notepad.exe"), "")


class DesktopGroupSystems(unittest.TestCase):
    LAYOUT = {"order": ["a", "b", "c"], "groups": {}}

    def test_nothing_runs_by_default(self):
        ctx = ResetContext()
        self.assertEqual(ctx.desktop_groups.style(), "off")
        with unittest.mock.patch("ui.desktop_groups.desktop_folders") as folders:
            ctx.desktop_groups.sync()
        folders.assert_not_called()

    def test_dragging_one_icon_onto_another_makes_a_group(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "Gruppe 1")
        group = layout["order"][0]
        self.assertTrue(group.startswith("group:"))
        self.assertEqual(layout["order"], [group, "c"])
        self.assertEqual(layout["groups"][group], {"name": "Gruppe 1", "items": ["a", "b"]})

    def test_icon_onto_a_group_joins_it(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "G")
        group = layout["order"][0]
        layout = desktop_groups.merged(layout, "c", group, "x")
        self.assertEqual(layout["groups"][group]["items"], ["a", "b", "c"])
        self.assertEqual(layout["order"], [group])

    def test_groups_cannot_be_dropped_into_icons(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "G")
        self.assertEqual(desktop_groups.merged(layout, layout["order"][0], "c", "x"), layout)

    def test_taking_the_last_but_one_out_dissolves_the_group(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "G")
        group = layout["order"][0]
        layout = desktop_groups.taken_out(layout, group, "b", 5)
        self.assertEqual(layout, {"order": ["a", "c", "b"], "groups": {}})

    def test_dissolving_keeps_the_place(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "G")
        self.assertEqual(desktop_groups.dissolved(layout, layout["order"][0])["order"], ["a", "b", "c"])

    def test_desktop_changes_are_followed(self):
        layout = desktop_groups.merged(self.LAYOUT, "b", "a", "G")
        group = layout["order"][0]
        layout = desktop_groups.normalized(layout, ["a", "b", "neu"])  # c ist weg, neu kam dazu
        self.assertEqual(layout["order"], [group, "neu"])
        layout = desktop_groups.normalized(layout, ["a", "neu"])  # die Gruppe hat nur noch ein Icon
        self.assertEqual(layout, {"order": ["a", "neu"], "groups": {}})

    def test_slots_fill_from_the_top_left_downwards(self):
        self.assertEqual(desktop_groups.slot_rect(4, 3).topLeft(), desktop_groups.slot_rect(1, 3).topLeft() + QPoint(desktop_groups.CELL.width(), 0))
        self.assertEqual(desktop_groups.slot_at(desktop_groups.slot_rect(7, 5).center(), 5), 7)

    def _home(self):
        ctx = ResetContext()
        manager = desktop_groups.DesktopGroups(ctx)
        manager.names = {"a": "A", "b": "B", "c": "C"}
        manager.layout = {"order": ["a", "b", "c"], "groups": {}}
        screen = desktop_groups.HomeScreen(manager, "folder")
        screen.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        manager.screen = screen
        screen.show()
        screen.fill()
        return manager, screen

    def test_real_drag_creates_the_group(self):
        manager, screen = self._home()
        cell = screen.cells["b"]
        screen.drag_started(cell)
        screen.drag_moved(cell, screen.mapToGlobal(screen.cells["a"].pos()))
        self.assertTrue(screen.cells["a"].highlight)
        screen.drag_ended(cell)
        self.assertEqual(len(manager.layout["groups"]), 1)
        self.assertEqual(manager.ctx.config.get("desktop_groups.layout"), manager.layout)

    def test_group_opens_with_all_names(self):
        manager, screen = self._home()
        manager.layout = desktop_groups.merged(manager.layout, "b", "a", "G")
        screen.fill()
        group = screen.cells[manager.layout["order"][0]]
        screen.activate(group)
        self.assertIsNotNone(screen.panel)
        self.assertEqual(len(screen.panel.findChildren(desktop_groups.Cell)), 2)
        screen.close_panel()
        self.assertIsNone(screen.panel)

    def test_windows_icons_come_back(self):
        ctx = ResetContext()
        manager = desktop_groups.DesktopGroups(ctx)
        manager.hid_icons = True
        with unittest.mock.patch("ui.desktop_groups.win_api.desktop_command") as command:
            manager.stop()
        command.assert_called_once()


class SettingsToFront(unittest.TestCase):
    def test_opening_a_page_brings_the_window_forward(self):
        ctx = ResetContext()
        settings = ctx.settings
        settings.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        with unittest.mock.patch.object(win_api, "bring_to_front") as front:
            settings.open_named("Audio")
        front.assert_called_once_with(int(settings.winId()))
        settings.hide()


class ExplorerAiAndDialog(unittest.TestCase):
    def test_ai_names_stay_inside_the_list(self):
        names = ["a.txt", os.path.join("Ordner", "b.pdf")]
        self.assertEqual(explorer_app.matched_files("- A.TXT\nerfunden.doc\n`Ordner\\b.pdf`", names), names)

    def test_nearest_files_come_first(self):
        with tempfile.TemporaryDirectory() as folder:
            os.mkdir(os.path.join(folder, "tief"))
            Path(folder, "tief", "x.txt").write_text("x", encoding="utf-8")
            Path(folder, "y.txt").write_text("x", encoding="utf-8")
            self.assertEqual(explorer_app.file_names(folder)[:2], ["tief", "y.txt"])
            self.assertEqual(len(explorer_app.file_names(folder, 1)), 1)

    def test_typing_never_reaches_the_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            pane = Pane(ResetContext(), folder)
            pane.search.setText("?wo ist die Rechnung")
            with unittest.mock.patch.object(explorer_app, "AiFileSearch") as job:
                pane._search()
            job.assert_not_called()

    # --- [Antigravity Start: Tests für KI-Dateisortierung & Heuristik] ---
    def test_heuristic_categorize(self):
        files = [
            "Rechnung_März.pdf",
            "Urlaub_Strand.jpg",
            "Python_Setup_312.exe",
            "main.py",
            "projekt_archiv.zip",
            "Unbekannt.xyz"
        ]
        cats = explorer_app.heuristic_categorize(files)
        self.assertIn("Rechnung_März.pdf", cats["Rechnungen & Finanzen"])
        self.assertIn("Urlaub_Strand.jpg", cats["Bilder & Screenshots"])
        self.assertIn("Python_Setup_312.exe", cats["Setups & Programme"])
        self.assertIn("main.py", cats["Code & Entwicklung"])
        self.assertIn("projekt_archiv.zip", cats["Archive & Backups"])
        self.assertIn("Unbekannt.xyz", cats["Sonstiges"])

    def test_ai_folder_names_stay_inside_the_folder(self):
        for bad in (r"..\..\Windows", r"C:\Temp", "a/b", "CON", "  ...  ", 'Bild<>:"|?*'):
            name = explorer_app.safe_folder_name(bad)
            self.assertTrue(name)
            self.assertNotRegex(name, r'[<>:"/\\|?*]')
            self.assertFalse(name.startswith("."))
            self.assertNotEqual(name.upper(), "CON")
        self.assertEqual(explorer_app.safe_folder_name("Rechnungen & Finanzen"), "Rechnungen & Finanzen")

    def test_ai_sort_names_each_file_once(self):
        reply = '{"categories": {"A": ["x.pdf", "x.pdf", "erfunden.txt"], "B": ["x.pdf", "y.png"], "..\\\\C": ["z.mp3"]}}'
        worker = explorer_app.AiSortWorker("nirgends", ["x.pdf", "y.png", "z.mp3", "w.zip"], "key", ["m"])
        seen = []
        worker.ready.connect(lambda cats, is_ai, note: seen.append(cats))
        with unittest.mock.patch("core.gemini_client.generate", return_value=(reply, "m")):
            worker.run()
        cats = seen[0]
        flat = [name for names in cats.values() for name in names]
        self.assertEqual(sorted(flat), ["w.zip", "x.pdf", "y.png", "z.mp3"])
        self.assertEqual(cats["A"], ["x.pdf"])
        self.assertNotIn("..\\C", cats)
        self.assertIn("C", cats)

    def test_grouped_entries_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "rechnung.pdf").write_text("x", encoding="utf-8")
            Path(folder, "foto.png").write_text("x", encoding="utf-8")
            groups = explorer_app.grouped_entries(folder, "ai", time.time())
            self.assertIn("Rechnungen & Finanzen", groups)
            self.assertIn("Bilder & Screenshots", groups)

    def test_ai_sort_dialog_moves_and_undos(self):
        with tempfile.TemporaryDirectory() as folder:
            f1 = Path(folder, "Rechnung_1.pdf")
            f2 = Path(folder, "Foto_1.png")
            f1.write_text("1", encoding="utf-8")
            f2.write_text("2", encoding="utf-8")
            pane = Pane(ResetContext(), folder)
            undo_actions = []
            pane.undoable.connect(lambda label, fn: undo_actions.append(fn))

            dialog = explorer_app.AiSortDialog(ResetContext(), pane)
            dialog.categories = {
                "Finanzen": ["Rechnung_1.pdf"],
                "Bilder": ["Foto_1.png"]
            }
            dialog._populate_tree()
            dialog._apply_move()

            target_f1 = Path(folder, "Finanzen", "Rechnung_1.pdf")
            target_f2 = Path(folder, "Bilder", "Foto_1.png")
            self.assertTrue(target_f1.exists())
            self.assertTrue(target_f2.exists())
            self.assertFalse(f1.exists())
            self.assertFalse(f2.exists())
            self.assertEqual(len(undo_actions), 1)

            # Undo
            undo_actions[0]()
            self.assertTrue(f1.exists())
            self.assertTrue(f2.exists())
            self.assertFalse(target_f1.exists())
            self.assertFalse(target_f2.exists())
    # --- [Antigravity Ende] ---

    def test_file_dialog_follows_the_beta_switch(self):
        window = unittest.mock.Mock()
        window.ctx.config.get.return_value = False
        with unittest.mock.patch("ui.file_dialog.QFileDialog.getOpenFileNames", return_value=(["a"], "")) as native:
            self.assertEqual(file_dialog.pick_files(window, "t", "*"), ["a"])
        native.assert_called_once()


class CacheCleanup(unittest.TestCase):
    NOW = 2_000_000_000

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def _file(self, name, age):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1234", encoding="utf-8")
        os.utime(path, (self.NOW - age, self.NOW - age))
        return path

    def test_only_old_files_go(self):
        old = self._file("alt.tmp", 2 * 86400)
        fresh = self._file("neu.tmp", 60)
        self.assertEqual(cleanup.scan([self.root], self.NOW), (1, 4))
        self.assertEqual(cleanup.clean([self.root], self.NOW), (1, 4, 0))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_empty_folders_go_and_the_temp_folder_stays(self):
        self._file("app/cache/alt.tmp", 2 * 86400)
        cleanup.clean([self.root], self.NOW)
        self.assertFalse((self.root / "app").exists())
        self.assertTrue(self.root.exists())

    def test_fresh_empty_folders_stay(self):
        (self.root / "gerade angelegt").mkdir()
        self._file("alt.tmp", 2 * 86400)
        cleanup.clean([self.root], self.NOW)
        self.assertTrue((self.root / "gerade angelegt").exists())

    def test_files_in_use_are_counted_not_fatal(self):
        self._file("alt.tmp", 2 * 86400)
        with unittest.mock.patch("core.cleanup.os.unlink", side_effect=PermissionError):
            self.assertEqual(cleanup.clean([self.root], self.NOW), (0, 0, 1))

    def test_linked_folders_are_not_entered(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        keep = outside / "wichtig.txt"
        keep.write_text("x", encoding="utf-8")
        os.utime(keep, (self.NOW - 3 * 86400, self.NOW - 3 * 86400))
        link = self.root / "link"
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(done.returncode, 0)
        cleanup.clean([self.root], self.NOW)
        self.assertTrue(keep.exists())
        os.rmdir(link)

    def test_nothing_is_deleted_without_a_yes(self):
        panel = CachePanel()
        with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
                unittest.mock.patch.object(panel, "_run") as run:
            panel._ask_files((3, 1024))
        run.assert_not_called()

    def test_refused_admin_prompt_is_reported(self):
        panel = CachePanel()
        with unittest.mock.patch.object(win_api, "run_elevated", return_value=False):
            panel._purge_memory()
        self.assertIn("Administrator", panel.note.text())

    def test_sizes_read_well(self):
        self.assertEqual(gigabytes(5 * 1024 ** 2), "5 MB")
        self.assertEqual(gigabytes(3 * 1024 ** 3), "3.00 GB")


class NovaVoice(unittest.TestCase):
    def test_sentences_leave_as_soon_as_they_end(self):
        cutter = SentenceCutter()
        self.assertEqual(cutter.feed("Das ist der erste Satz"), [])
        self.assertEqual(cutter.feed(". Und hier"), ["Das ist der erste Satz."])
        self.assertEqual(cutter.flush(), ["Und hier"])

    def test_short_bits_wait_for_more(self):
        cutter = SentenceCutter()
        self.assertEqual(cutter.feed("Ja. Na klar, das mache ich gleich so. "), ["Ja. Na klar, das mache ich gleich so."])

    def test_long_sentences_break_at_a_pause(self):
        text = "wort " * 30 + ", und danach geht es weiter ohne Ende"
        self.assertTrue(SentenceCutter().feed(text))

    def test_markdown_and_code_are_not_read_aloud(self):
        cutter = SentenceCutter()
        spoken = cutter.feed("Das ist **wichtig** und [hier](http://x) steht es.\n```\nprint(1)\n```\n") + cutter.flush()
        self.assertEqual(spoken, ["Das ist wichtig und hier steht es."])

    def _speech(self):
        speech = LiveSpeech("key")
        self.calls = []
        speech._voice = lambda client, sentence, models: (self.calls.append(sentence), speech.sound.put(b"\x01\x02\x03"))
        return speech

    def test_speech_starts_before_the_answer_is_done(self):
        speech = self._speech()
        played = []
        output = unittest.mock.Mock()
        output.write.side_effect = played.append
        speech._open_output = lambda: output
        with unittest.mock.patch("core.nova_controller.client_for"):
            speech.start()
            speech.feed("Der erste Satz ist fertig. Der zw")
            end = time.time() + 3
            while not played and time.time() < end:
                time.sleep(0.01)
            self.assertEqual(self.calls, ["Der erste Satz ist fertig."])  # die Antwort läuft noch
            self.assertEqual(played, [b"\x01\x02"])  # halbe Werte warten auf den nächsten Block
            speech.feed("eite Satz.")
            speech.finish()
            speech.wait(3000)
        self.assertEqual(self.calls[-1], "Der zweite Satz.")
        output.stop.assert_called_once()

    def test_stop_cuts_it_off(self):
        speech = self._speech()
        output = unittest.mock.Mock()
        speech._open_output = lambda: output
        with unittest.mock.patch("core.nova_controller.client_for"):
            speech.start()
            speech.feed("Der erste Satz ist fertig. ")
            time.sleep(0.2)
            speech.stop()
            speech.wait(3000)
        self.assertFalse(speech.isRunning())

    def test_broken_model_gives_way_to_the_next(self):
        speech = LiveSpeech("key")
        good = unittest.mock.MagicMock()
        good.candidates[0].content.parts = [unittest.mock.Mock(inline_data=unittest.mock.Mock(data=b"ab"))]
        client = unittest.mock.Mock()
        client.models.generate_content_stream.side_effect = [OSError("weg"), [good]]
        speech._settings = lambda: None
        models = ["a", "b"]
        speech._voice(client, "Satz", models)
        self.assertEqual(models, ["b"])
        self.assertEqual(speech.sound.get_nowait(), b"ab")


class PinnedStoreApps(unittest.TestCase):
    EXE = r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.2640.100.0_x64__cv1g1gvanyjgm\WhatsApp.Root.exe"

    def test_shortcut_finds_its_app_by_name(self):
        pin = {"name": "WhatsApp", "target": r"C:\x\WhatsApp.lnk"}
        self.assertEqual(pin_key_of_window(self.EXE, [pin]), "whatsapp.lnk")

    def test_store_pin_finds_its_package(self):
        pin = {"name": "Chat", "target": "shell:AppsFolder\\Paket_x!App", "exe": "chat.exe"}
        with unittest.mock.patch.object(win_api, "packaged_app_target", return_value="shell:AppsFolder\\Paket_x!App"):
            self.assertEqual(pin_key_of_window(self.EXE, [pin]), "chat.exe")

    def test_strangers_stay_separate(self):
        pin = {"name": "App", "target": r"C:\x\App.lnk"}
        self.assertEqual(pin_key_of_window(r"C:\y\App.exe", [pin]), "")  # zu kurz für einen sicheren Treffer
        self.assertEqual(pin_key_of_window(self.EXE, [{"name": "Discord", "target": "d.lnk"}]), "")

    def test_window_lands_on_the_pin_and_no_second_icon_appears(self):
        ctx = ResetContext()
        ctx.config.set("taskbar.pinned", [{"name": "WhatsApp", "target": r"C:\x\WhatsApp.lnk"}])
        ctx.windows = lambda: [win_api.WindowInfo(5, "WhatsApp", self.EXE, 4)]
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        dock.refresh(True)
        self.assertEqual(list(dock._buttons), ["whatsapp.lnk"])
        self.assertEqual(len(dock._buttons["whatsapp.lnk"].windows), 1)


class StartMenuSpam(unittest.TestCase):
    def test_reopening_while_it_fades_out_keeps_the_size(self):
        """Neue Kacheln in einem noch sichtbaren Fenster zählten erst später mit, das Menü saß dann zu tief."""
        ctx = ResetContext()
        menu = ctx.start_menu
        ctx.config.set("start_menu.pinned", [{"name": str(n), "target": "cmd.exe"} for n in range(12)])
        menu._rebuild()
        menu.adjustSize()
        height = menu.height()
        menu.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        menu.show()
        menu._rebuild()
        menu.adjustSize()
        self.assertEqual(menu.height(), height)
        self.assertEqual(sum(tile.isVisibleTo(menu) for tile in menu.grid_host.findChildren(QToolButton)), 12)
        menu.hide()

    def test_menu_is_centred_wherever_the_home_button_sits(self):
        ctx = ResetContext()
        ctx.windows = lambda: []
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        dock.show()
        dock.move(dock.screen().availableGeometry().left() + 10, dock.y())  # Home-Knopf ganz links
        QApplication.processEvents()
        menu = ctx.start_menu
        with unittest.mock.patch.object(win_api, "force_foreground"):
            menu.toggle(dock.start)
        middle = dock.start.screen().availableGeometry().center().x()
        self.assertLessEqual(abs(menu.geometry().center().x() - middle), 2)
        menu.hide()
        dock.hide()


class TerminalDisplay(unittest.TestCase):
    def _shell(self):
        return TerminalWidget(["cmd.exe"])

    def test_theme_cannot_change_the_cell_font(self):
        shell = self._shell()
        width = shell.cell_width
        shell.setStyleSheet("* { font-family: 'Segoe UI'; font-size: 9px; }")
        shell.ensurePolished()
        self.assertEqual(QFontMetricsF(shell.text_font).horizontalAdvance("M"), width)
        self.assertEqual(QFontMetricsF(shell.text_font).horizontalAdvance("i"), width)

    def test_shrinking_keeps_text_and_cursor_together(self):
        shell = self._shell()
        shell.stream.feed("eins\r\nzwei\r\ndrei")
        shell.screen_data.resize(10, shell.columns)
        self.assertEqual(shell.screen_data.display[0].rstrip(), "eins")
        self.assertEqual((shell.screen_data.cursor.y, shell.screen_data.lines), (2, 10))

    def test_shrinking_below_the_cursor_gives_up_only_the_top(self):
        shell = self._shell()
        shell.stream.feed("\r\n".join(f"zeile {n}" for n in range(8)))
        shell.screen_data.resize(5, shell.columns)
        self.assertEqual(shell.screen_data.display[0].rstrip(), "zeile 3")
        self.assertEqual(shell.screen_data.display[4].rstrip(), "zeile 7")
        self.assertEqual(shell.screen_data.cursor.y, 4)
        shell.stream.feed("\r\nneu")
        self.assertEqual(shell.screen_data.display[4].rstrip(), "neu")

    def test_clicks_land_on_the_cell_under_the_mouse(self):
        shell = self._shell()
        cell = shell._cell(QPointF(PADDING + 2.5 * shell.cell_width, PADDING + 1.5 * shell.cell_height))
        self.assertEqual((cell.x(), cell.y()), (2, 1))


class TaskbarFilter(unittest.TestCase):
    def test_smart_is_the_default(self):
        self.assertEqual(DEFAULTS["taskbar"]["window_filter"], "smart")

    def test_smart(self):
        self.assertTrue(shows_all_windows("smart", True))
        self.assertFalse(shows_all_windows("smart", False))

    def test_same_everywhere(self):
        for primary in (True, False):
            self.assertFalse(shows_all_windows("own", primary))
            self.assertTrue(shows_all_windows("all", primary))


class Fullscreen(unittest.TestCase):
    MONITOR = (0, 0, 1920, 1080)

    def test_covers(self):
        self.assertTrue(covers_monitor(self.MONITOR, self.MONITOR))
        self.assertTrue(covers_monitor((-8, -8, 1928, 1088), self.MONITOR))

    def test_normal_windows_do_not_count(self):
        self.assertFalse(covers_monitor((0, 42, 1920, 1016), self.MONITOR))
        self.assertFalse(covers_monitor((1920, 0, 3840, 1080), self.MONITOR))

    def test_discord_reports_maximized_while_fullscreen(self):
        self.assertTrue(win_api.is_fullscreen_rect(self.MONITOR, self.MONITOR, True))
        self.assertTrue(win_api.is_fullscreen_rect(self.MONITOR, self.MONITOR, False))
        self.assertFalse(win_api.is_fullscreen_rect((-8, -8, 1928, 1088), self.MONITOR, True))  # normal maximiert

    def test_windows_on_other_monitors_do_not_matter(self):
        self.assertTrue(win_api.overlaps((100, 100, 500, 500), self.MONITOR))
        self.assertFalse(win_api.overlaps((1920, 0, 3840, 1080), self.MONITOR))
        self.assertFalse(win_api.overlaps((-32000, -32000, -31840, -31972), self.MONITOR))  # minimiert


class LauncherSize(unittest.TestCase):
    def test_window_shrinks_when_the_text_is_deleted(self):
        ctx = ResetContext()
        ctx.apps.apps = [App(f"Sample {index}", "x.exe") for index in range(6)]
        launcher = ctx.launcher
        launcher.move(-9000, -9000)
        launcher.show()
        try:
            launcher.input.setText("s")
            app.processEvents()
            tall = launcher.height()
            launcher.input.setText("")
            app.processEvents()
            self.assertLess(launcher.height(), 90)
            self.assertLess(launcher.height(), tall)
        finally:
            launcher.hide()


class ListFixes(unittest.TestCase):
    def test_wheel_scrolls_the_page_not_the_field(self):
        ctx = ResetContext()
        area = QScrollArea()
        host = QWidget()
        box = QVBoxLayout(host)
        spin = QSpinBox()
        spin.setValue(5)
        box.addWidget(spin)
        host.setFixedHeight(900)
        area.setWidget(host)
        area.resize(300, 200)
        area.show()
        try:
            wheel = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(spin.lineEdit(), wheel)  # das Rad trifft das Textfeld in der Zahlenbox
            self.assertEqual(spin.value(), 5)
            self.assertGreater(area.verticalScrollBar().value(), 0)
            area.verticalScrollBar().setValue(0)
            combo = QComboBox()
            combo.addItems(["Oben", "Unten"])
            box.addWidget(combo)
            QApplication.sendEvent(combo, wheel)
            self.assertEqual(combo.currentIndex(), 0)
            self.assertGreater(area.verticalScrollBar().value(), 0)
        finally:
            area.hide()
        self.assertIsNotNone(ctx)

    def test_search_stays_open_with_text(self):
        ctx = ResetContext()
        launcher = ctx.launcher
        launcher.dismiss = unittest.mock.Mock()
        launcher.input.setText("hallo")
        launcher.event(QEvent(QEvent.Type.WindowDeactivate))
        app.processEvents()
        launcher.dismiss.assert_not_called()
        launcher.input.setText("")
        launcher.event(QEvent(QEvent.Type.WindowDeactivate))
        app.processEvents()
        launcher.dismiss.assert_called_once()

    def test_questions_go_to_nova(self):
        ctx = ResetContext()
        ctx.nova.ask = unittest.mock.Mock()
        ctx.launcher.dismiss = lambda: None
        ctx.launcher._ask("Wie wird das Wetter?")
        ctx.nova.ask.assert_called_once_with("Wie wird das Wetter?")

    def test_closing_during_the_slide_keeps_the_position(self):
        ctx = ResetContext()
        window = QWidget()
        window.move(-9000, -9000)
        window.show()
        animator = WindowAnimator(ctx, window)
        home = window.pos()
        try:
            animator.enter(1.0, slide=40)
            app.processEvents()
            window.move(home + QPoint(0, 20))  # mitten in der Bewegung
            animator.leave()
            self.assertEqual(animator._base, home)
        finally:
            animator._animation.stop()
            window.hide()

    def test_topbar_can_hide(self):
        ctx = ResetContext()
        bar = TopBar(ctx, QGuiApplication.primaryScreen(), "top")
        self.assertTrue(bar.reserves_space())
        ctx.config.set_monitor(None, "topbar_auto_hide", True)
        self.assertTrue(bar.auto_hides())
        self.assertFalse(bar.reserves_space())

    def test_taskbar_menu_has_settings(self):
        ctx = ResetContext()
        dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        menu = QMenu()
        dock.add_bar_options(menu)
        labels = [action.text() for action in menu.actions()]
        for label in ("Automatisch ausblenden", "Papierkorb", "Taskbar-Einstellungen"):
            self.assertIn(label, labels)
        next(a for a in menu.actions() if a.text() == "Papierkorb").trigger()
        self.assertFalse(ctx.config.monitor()["show_recycle"])

    def test_windows_stay_below_the_topbar(self):
        window = SettingsWindow(ResetContext())
        area = QGuiApplication.primaryScreen().availableGeometry()
        window.move(area.left() + 10, area.top() - 30)
        window.keep_in_work_area()
        self.assertEqual(window.y(), area.top())


class ExplorerList(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.addCleanup(self.folder.cleanup)
        self.store = unittest.mock.patch("explorer.app.STORE", self.root / "store.json")
        self.store.start()
        self.addCleanup(self.store.stop)
        (self.root / "a").mkdir()
        (self.root / "b").mkdir()
        (self.root / "a" / "text.txt").write_text("x")

    def wait(self, pane) -> None:
        deadline = time.monotonic() + 5
        while pane._transfers and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

    def test_names_never_collide(self):
        self.assertEqual(Path(free_name(str(self.root / "a"), "text.txt")).name, "text (2).txt")
        self.assertEqual(Path(free_name(str(self.root / "a"), "neu.txt")).name, "neu.txt")
        self.assertTrue(inside_itself(str(self.root / "a"), str(self.root / "a" / "x")))
        self.assertFalse(inside_itself(str(self.root / "a"), str(self.root / "ab")))

    def test_copy_paste_and_undo(self):
        pane = Pane(ResetContext(), str(self.root / "b"))
        steps = []
        pane.undoable.connect(lambda label, action: steps.append((label, action)))
        pane.selection = lambda: [str(self.root / "a" / "text.txt")]
        pane.copy_selection()
        pane.paste()
        self.wait(pane)
        self.assertTrue((self.root / "b" / "text.txt").exists())
        self.assertTrue((self.root / "a" / "text.txt").exists())
        pane.selection = lambda: [str(self.root / "b" / "text.txt")]
        pane.copy_selection(cut=True)
        pane.open(str(self.root / "a"))
        pane.paste()
        self.wait(pane)
        self.assertFalse((self.root / "b" / "text.txt").exists())
        self.assertTrue((self.root / "a" / "text (2).txt").exists())
        steps[-1][1]()
        self.assertTrue((self.root / "b" / "text.txt").exists())

    def test_drop_moves_on_the_same_drive_and_copies_with_ctrl(self):
        pane = Pane(ResetContext(), str(self.root / "a"))
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self.root / "a" / "text.txt"))])
        pane.drop_urls(data, str(self.root / "b"), Qt.KeyboardModifier.ControlModifier)
        self.wait(pane)
        self.assertTrue((self.root / "a" / "text.txt").exists())
        pane.drop_urls(data, str(self.root / "b"), Qt.KeyboardModifier.NoModifier)
        self.wait(pane)
        self.assertFalse((self.root / "a" / "text.txt").exists())
        self.assertTrue((self.root / "b" / "text (2).txt").exists())

    def test_folder_cannot_go_into_itself(self):
        pane = Pane(ResetContext(), str(self.root))
        problems = []
        pane.problem.connect(problems.append)
        pane.transfer([str(self.root / "a")], str(self.root / "a"), move=True)
        self.wait(pane)
        self.assertTrue(problems)
        self.assertTrue((self.root / "a" / "text.txt").exists())

    def test_extract(self):
        archive = self.root / "paket.zip"
        with zipfile.ZipFile(archive, "w") as file:
            file.writestr("innen.txt", "x")
        pane = Pane(ResetContext(), str(self.root))
        pane.extract(str(archive))
        self.assertTrue((self.root / "paket" / "innen.txt").exists())
        self.assertEqual(archive_stem("x.tar.gz"), "x")

    def test_view_settings_survive(self):
        pane = Pane(ResetContext(), str(self.root))
        pane.set_group("date")
        pane.set_sort(3, Qt.SortOrder.DescendingOrder)
        pane.view_mode = "list"
        again = Pane(ResetContext(), str(self.root))
        self.assertEqual(again.group, "date")
        self.assertEqual(again.view.header().sortIndicatorSection(), 3)
        self.assertEqual(again.view.header().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

    def test_sorting_survives_closing_the_window(self):
        first = ExplorerWindow(ResetContext(), str(self.root))
        pane = first.current_pane()
        pane.set_sort(2, Qt.SortOrder.DescendingOrder)
        pane.set_group("type")
        first.close()
        second = ExplorerWindow(ResetContext(), str(self.root)).current_pane()
        self.assertEqual(second.view.header().sortIndicatorSection(), 2)
        self.assertEqual(second.view.header().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual(second.group, "type")

    def test_quick_access_is_in_the_right_click_menu(self):
        pane = Pane(ResetContext(), str(self.root))
        pinned = []
        pane.pin_place.connect(pinned.append)
        menu = pane.context_menu
        menu._start()
        pane._fill_menu(menu, [str(self.root / "a"), str(self.root / "b")])
        next(a for a in menu.actions() if a.text() == "Zum Schnellzugriff").trigger()
        menu._release_shell()
        self.assertEqual(len(pinned), 2)
        menu._start()
        pane._fill_menu(menu, [])
        next(a for a in menu.actions() if a.text() == "Diesen Ordner zum Schnellzugriff").trigger()
        self.assertEqual(pinned[-1], str(self.root))

    def test_drive_names_in_tabs(self):
        self.assertEqual(tab_title(r"C:\Users"), "Users")
        self.assertIn("C:", tab_title("C:\\"))

    def test_big_pictures_get_a_small_preview(self):
        big, small = self.root / "gross.png", self.root / "klein.png"
        for path, size in ((big, (800, 400)), (small, (32, 16))):
            image = QPixmap(*size)
            image.fill(QColor("red"))
            image.save(str(path))
        self.assertEqual(read_thumbnail(str(big)).width(), 96)
        self.assertEqual(read_thumbnail(str(big)).height(), 48)
        self.assertEqual(read_thumbnail(str(small)).width(), 32)  # klein bleibt klein
        pane = Pane(ResetContext(), str(self.root))
        pane.set_view_mode("blocks")
        pane._show_list()
        self.assertIsNone(pane.thumbnail(str(big)))  # noch unterwegs
        deadline = time.monotonic() + 5
        while pane.thumbnail(str(big)) is None and time.monotonic() < deadline:
            app.processEvents()
        self.assertIsNotNone(pane.thumbnail(str(big)))
        self.assertIsNone(pane.thumbnail(str(self.root / "a" / "text.txt")))
        pane.set_view_mode("list")
        pane._show_list()
        self.assertIsNone(pane.thumbnail(str(big)))  # die Liste bleibt schlicht wie in Windows

    def test_places_keep_their_order(self):
        places = [("A", str(self.root / "a")), ("B", str(self.root / "b"))]
        extra = str(self.root)
        saved = [{"label": "B", "path": str(self.root / "b")}, {"label": "Eigen", "path": extra},
                 {"label": "Weg", "path": str(self.root / "gibt-es-nicht")}]
        self.assertEqual([label for label, _ in ordered_places(places, saved)], ["B", "Eigen", "A"])

    def test_windows_menu_is_read_as_data(self):
        path = str(self.root / "a" / "text.txt")
        shell = win_api.ShellMenu(0, [path])
        entries = shell.entries()
        shell.close()
        labels = [entry.label for entry in entries if entry.label]
        self.assertGreater(len(labels), 3)
        self.assertTrue(all("&" not in label or "&&" not in label for label in labels))

    def test_same_menu_as_the_desktop(self):
        pane = Pane(ResetContext(), str(self.root))
        self.assertIsInstance(pane.context_menu, ContextMenu)
        menu = pane.context_menu
        menu._start()
        pane._fill_menu(menu, [str(self.root / "a" / "text.txt")])
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Kopieren", labels)
        self.assertEqual(labels.count("Kopieren"), 1)  # das Windows-Menü liefert es nicht noch einmal
        self.assertIn("In den Papierkorb", labels)
        more = next(a.menu() for a in menu.actions() if a.text() == "Weitere Optionen")
        self.assertGreater(len(more.actions()), 3)  # Windows und installierte Programme, gesammelt im Untermenü
        self.assertLess(len(labels), 12)
        menu._release_shell()
        menu._start()
        pane._fill_menu(menu, [])
        self.assertIn("Neuer Ordner", [action.text() for action in menu.actions()])

    def test_desktop_menu_has_no_sort_row(self):
        menu = ContextMenu(ResetContext())
        with unittest.mock.patch.object(ContextMenu, "add_sort_row") as row:
            menu.build(True)
        row.assert_not_called()

    def test_settings_page_height_follows_the_width(self):
        window = SettingsWindow(ResetContext())
        page = window.tabs.widget(3)
        self.assertGreater(page.box.totalHeightForWidth(500), page.box.totalHeightForWidth(1800))
        page.resize(800, 600)
        page._fit_height()
        self.assertEqual(page.widget().minimumHeight(), page.box.totalHeightForWidth(page.viewport().width()))

    def test_search_measures_again_when_visible(self):
        ctx = ResetContext()
        launcher = ctx.launcher
        launcher.results.addItem("Vorschlag")
        launcher._size_results()
        self.assertGreater(launcher.results.height(), ctx.config.get("launcher.result_font") + 26)

    def test_shell_entries_stay_tidy(self):
        Entry = win_api.ShellEntry
        kept = trim_separators([Entry(), Entry("A"), Entry(), Entry(), Entry("B"), Entry()])
        self.assertEqual([entry.label for entry in kept], ["A", "", "B"])
        self.assertEqual(win_api.clean_menu_label("&Öffnen\tStrg+O"), "Öffnen")
        self.assertEqual(win_api.clean_menu_label("A && B"), "A & B")

    def test_text_opens_in_the_editor(self):
        ctx = ResetContext()
        pane = Pane(ctx, str(self.root))
        path = str(self.root / "a" / "text.txt")
        with unittest.mock.patch("subprocess.Popen") as started, \
                unittest.mock.patch.object(win_api, "shell_open") as windows_app:  # nie die echte Windows-App starten
            pane.open_file(path)  # ab Werk ist der PopWin-Editor aus (Beta)
            started.assert_not_called()
            windows_app.assert_called_once_with(path)
            ctx.config.set("replacements.editor", True)
            pane.open_file(path)
        self.assertIn("editor", " ".join(started.call_args[0][0]))


class EditorList(unittest.TestCase):
    def test_image_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "bild.png"
            image = QPixmap(30, 20)
            image.fill(QColor("red"))
            image.save(str(picture))
            panel = EditorPanel(ResetContext(), "test.json")
            panel.session = Path(folder) / "session.json"
            panel.new_tab()
            tab = panel.open_file(str(picture))
            self.assertTrue(tab.image)
            self.assertFalse(tab.modified)
            self.assertFalse(panel.save(tab))  # ein Bild darf nie als Text überschrieben werden
            self.assertEqual(len(panel.tabs), 1)
            self.assertIn("30 × 20", panel.status.text())

    def test_autosave_writes_named_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "datei.txt"
            target.write_text("alt")
            panel = EditorPanel(ResetContext(), "test.json")
            panel.session = Path(folder) / "session.json"
            tab = panel.open_file(str(target))
            tab.editor.setPlainText("neu")
            panel.autosave = True
            panel._autosave()
            self.assertEqual(target.read_text(), "neu")
            self.assertEqual(panel.unsaved_tabs(), [])

    def test_window_asks_before_closing_unsaved_work(self):
        with tempfile.TemporaryDirectory() as folder, unittest.mock.patch("editor.widget.DATA_DIR", Path(folder)):
            window = EditorWindow(ResetContext())
            window.panel.session = Path(folder) / "session.json"
            window.panel.editor().setPlainText("wichtig")
            with unittest.mock.patch("editor.app.QMessageBox") as box:
                instance = box.return_value
                instance.clickedButton.return_value = object()  # Abbrechen
                self.assertFalse(window.ask_about_unsaved())
                instance.clickedButton.return_value = instance.addButton.side_effect = None
                instance.addButton.return_value = "knopf"
                instance.clickedButton.return_value = "knopf"
                self.assertTrue(window.ask_about_unsaved())  # Als Entwurf behalten


class WidgetList(unittest.TestCase):
    def test_close_button_removes_the_widget(self):
        ctx = ResetContext()
        widget = ctx.widgets.add("clock")
        widget.spec["data"] = {}
        widget.ask_remove()
        self.assertEqual(ctx.config.get("widgets"), [])

    def test_widget_with_content_asks_first(self):
        ctx = ResetContext()
        widget = ctx.widgets.add("notes")
        widget.spec["data"] = {"text": "wichtig"}
        with unittest.mock.patch("ui.widgets.base.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            widget.ask_remove()
        self.assertEqual(len(ctx.config.get("widgets")), 1)
        ctx.widgets.remove(widget.spec["id"])

    def test_grip_changes_width_and_height(self):
        ctx = ResetContext()
        widget = ctx.widgets.add("clock")
        widget.move(-9000, -9000)
        left, top = widget.x(), widget.y()
        widget.preview_size(400, 300)
        self.assertEqual((widget.width(), widget.height()), (400, 300))
        self.assertEqual((widget.x(), widget.y()), (left, top))
        widget.preview_size(9999, 9999)
        self.assertEqual((widget.width(), widget.height()), (640, 900))
        widget.hide()

    def test_gallery_opens_again_after_closing(self):
        ctx = ResetContext()
        ctx.widgets.open_gallery()
        gallery = ctx.widgets.gallery
        gallery.move(-9000, -9000)
        self.assertTrue(gallery.isVisible())
        gallery.close()
        app.processEvents()
        self.assertFalse(gallery.isVisible())
        ctx.widgets.open_gallery()
        app.processEvents()
        self.assertTrue(gallery.isVisible())
        gallery.hide()


class SyncedIcons(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        screen = QGuiApplication.primaryScreen()
        self.main = Dock(self.ctx, screen, "bottom")
        self.other = Dock(self.ctx, screen, "top")
        self.main._primary = lambda: True
        self.other._primary = lambda: False
        self.other.screen_id = "anderer"
        self.ctx.config.set("taskbar.pinned", [{"name": "A", "target": "a.exe"}, {"name": "B", "target": "b.exe"}])
        self.ctx.config.set_screen_pins("anderer", [{"name": "C", "target": "c.exe"}])
        self.ctx.config.set("taskbar.pin_scope", "primary")

    def test_off_keeps_every_taskbar_separate(self):
        self.assertEqual([p["name"] for p in self.other.visible_pins()], ["C"])

    def test_on_shows_the_same_icons_everywhere(self):
        self.ctx.config.set("taskbar.sync_icons", True)
        self.assertEqual(self.other.visible_pins(), self.main.visible_pins())
        self.other.pin_paths([r"C:\\x\\neu.exe"])
        self.assertIn("neu", [p["name"] for p in self.main.visible_pins()])

    def test_on_shows_all_windows_and_shares_the_order(self):
        self.ctx.config.set("taskbar.sync_icons", True)
        self.ctx.windows = lambda: [win_api.WindowInfo(1, "F", r"C:\\x\\app.exe", 4)]
        self.other._windows_here = Dock._windows_here.__get__(self.other)
        self.assertEqual(len(self.other._windows_here()), 1)
        self.other._order = ["b.exe", "a.exe"]
        self.other.save_order()
        self.main.refresh()
        self.assertEqual(self.main._order[:2], ["b.exe", "a.exe"])


class SharedMenuLook(unittest.TestCase):
    def test_taskbar_menu_follows_the_context_menu_settings(self):
        ctx = ResetContext()
        ctx.config.set("context_menu.font_size", 17)
        ctx.config.set("context_menu.opacity", 0.8)
        ctx.config.set("context_menu.size", "gross")
        host = QWidget()
        menu = themed_menu(ctx, host, "")
        self.assertIn("font-size: 17px", menu.styleSheet())
        self.assertIn("padding: 9px", menu.styleSheet())
        self.assertAlmostEqual(menu.windowOpacity(), 0.8, 1)
        desktop = ContextMenu(ctx)
        desktop.build(True)
        self.assertEqual(desktop.styleSheet(), menu.styleSheet())

    def test_own_style_wins_over_the_bar_style(self):
        ctx = ResetContext()
        ctx.config.set("context_menu.theme", "clean")
        host = QWidget()
        menu = themed_menu(ctx, host, "liquid_glass")
        self.assertIn(ctx.theme.stylesheet("clean"), menu.styleSheet())

    def test_background_is_set_when_the_menu_opens(self):
        ctx = ResetContext()
        host = QWidget()
        menu = themed_menu(ctx, host, "")
        with unittest.mock.patch.object(win_api, "set_blur") as blur:
            menu.aboutToShow.emit()
        blur.assert_called_once()


class SetupUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("popwin_setup", Path(popwin_main.ROOT) / "installer" / "setup.py")
        cls.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.setup)

    def test_setup_knows_what_to_offer(self):
        kind = self.setup.update_kind
        self.assertEqual(kind("", "1.1"), "new")
        self.assertEqual(kind("1.0", "1.1"), "update")
        self.assertEqual(kind("1.9", "1.10"), "update")  # Zahlen, kein Textvergleich
        self.assertEqual(kind("1.1", "1.1"), "same")
        self.assertEqual(kind("2.0", "1.1"), "older")

    def wizard(self, version):
        with unittest.mock.patch.object(self.setup, "installed_folder", return_value=Path(tempfile.gettempdir())), \
                unittest.mock.patch.object(self.setup, "installed_version", return_value=version):
            return self.setup.Wizard(False)

    def test_update_starts_with_one_click(self):
        wizard = self.wizard("1.0")
        self.assertEqual(wizard.next.text(), "Aktualisieren")
        self.assertEqual(wizard.steps[wizard.progress_page], {})
        self.assertEqual(wizard.done_page, wizard.pages.count() - 1)

    def test_fresh_install_keeps_all_steps(self):
        with unittest.mock.patch.object(self.setup, "installed_folder", return_value=None):
            wizard = self.setup.Wizard(False)
        self.assertEqual(wizard.next.text(), "Weiter")
        self.assertEqual(wizard.progress_page, 3)


class SyncedOrder(unittest.TestCase):
    def test_pins_stay_in_front(self):
        self.assertEqual(synced_order([], ["a", "b"], ["offen", "a", "b"]), ["a", "b", "offen"])
        self.assertEqual(synced_order(["offen", "b", "a"], ["a", "b"], ["offen"]), ["b", "a", "offen"])
        self.assertEqual(synced_order(["a"], ["a", "neu"], ["z", "y"]), ["a", "neu", "z", "y"])

    def test_every_taskbar_gets_the_same_order(self):
        ctx = ResetContext()
        screen = QGuiApplication.primaryScreen()
        docks = [Dock(ctx, screen, "bottom"), Dock(ctx, screen, "top")]
        ctx.config.set("taskbar.pinned", [{"name": "A", "target": "a.exe"}])
        ctx.config.set("taskbar.sync_icons", True)
        ctx.windows = lambda: [win_api.WindowInfo(1, "F", r"C:\x\offen.exe", 4)]
        docks[0]._order = ["offen.exe", "a.exe"]  # alte, eigene Reihenfolge aus der Zeit vor dem Sync
        docks[1]._order = ["a.exe"]
        for dock in docks:
            dock.refresh(force=True)
        self.assertEqual(docks[0]._order[:2], ["a.exe", "offen.exe"])
        self.assertEqual(docks[0]._order, docks[1]._order)


class Extensions(unittest.TestCase):
    def test_discord_is_offered_and_never_starts_alone(self):
        ctx = ResetContext()
        extension = ctx.extensions.items["discord"]
        extension.start = unittest.mock.Mock()
        ctx.extensions.set_enabled("discord", True)
        extension.start.assert_not_called()  # ein Stream zeigt alles: nur auf ausdrücklichen Klick
        self.assertTrue(ctx.extensions.enabled("discord"))
        ctx.extensions.start()
        extension.start.assert_not_called()

    def test_own_extension_is_loaded_and_errors_stay_contained(self):
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / "gut.py"
            good.write_text("from extensions import Extension\nclass EXTENSION(Extension):\n    key = 'gut'\n"
                            "    title = 'Gut'\n    started = False\n    def start(self):\n        type(self).started = True\n")
            bad = Path(folder) / "kaputt.py"
            bad.write_text("das ist kein python(")
            with unittest.mock.patch("extensions.USER_FOLDER", Path(folder) / "ziel"):
                ctx = ResetContext()
                ctx.extensions.add_file(str(good))
                ctx.extensions.add_file(str(bad))
            self.assertIn("gut", ctx.extensions.items)
            self.assertIn("kaputt.py", ctx.extensions.errors)
            ctx.extensions.set_enabled("gut", True)
            self.assertTrue(type(ctx.extensions.items["gut"]).started)
            ctx.settings._fill_extensions()

    def test_settings_page_lists_monitors(self):
        ctx = ResetContext()
        boxes = ctx.settings.extensions_page.findChildren(QCheckBox)
        labels = [box.text() for box in boxes]
        self.assertIn("Erweiterung aktiv", labels)
        self.assertTrue(any(label.startswith("Bildschirm ") for label in labels))

    def test_only_chosen_monitors_are_arranged(self):
        rects = [(0, 0, 3440, 1440), (3440, 104, 5360, 1184)]
        places = layout_rects(rects, 1920, 1080, "windows")
        self.assertEqual(len(places), 2)
        self.assertLess(places[0].x(), places[1].x())
        self.assertTrue(all(p.right() <= 1920 and p.bottom() <= 1080 for p in places))
        row = layout_rects([(0, 0, 1920, 1080), (0, 1080, 1920, 2160)], 1920, 1080, "row")
        self.assertEqual(row[0].y(), row[1].y())  # untereinander in Windows, nebeneinander im Bild
        self.assertEqual(canvas_size(720), (1280, 720))

    def test_cursor_lands_on_its_monitor(self):
        rects = [(0, 0, 100, 100), (100, 0, 200, 100)]
        places = [QRect(0, 0, 50, 50), QRect(50, 0, 50, 50)]
        self.assertEqual(cursor_spot((150, 50), rects, places), (75.0, 25.0))
        self.assertIsNone(cursor_spot((500, 500), rects, places))

    def test_frame_has_no_row_padding(self):
        image = QImage(1282, 4, QImage.Format.Format_BGR888)
        image.fill(QColor("#102030"))
        data = frame_bytes(image)
        self.assertEqual(data.shape, (4, 1282, 3))
        self.assertEqual(list(data[3, 1281]), [0x30, 0x20, 0x10])

    @unittest.skipIf(os.environ.get("POPWIN_QUIET"), "auf dem unsichtbaren Desktop gibt es keinen Bildschirm zum Abfotografieren")
    def test_real_screen_is_captured(self):
        import mss
        rect = (QGuiApplication.primaryScreen().geometry().x(), 0, 200, 150)
        capture = Capture([rect], (320, 180), 15, "windows")
        with mss.mss() as grabber:
            image = capture.compose(grabber, layout_rects([rect], 320, 180, "windows"))
        self.assertEqual((image.width(), image.height()), (320, 180))


class PopWinApps(unittest.TestCase):
    def test_commands_for_scripts(self):
        command = app_command("editor", "a.txt")
        self.assertTrue(command[1].endswith(("editor\\app.py", "editor/app.py")))
        self.assertEqual(command[-1], "a.txt")

    def test_installed_exe_dispatches_by_flag(self):
        self.assertEqual(app_from_arguments(["--app", "terminal", "C:\\x"]), ("terminal", ["C:\\x"]))
        self.assertEqual(app_from_arguments([]), ("main", []))
        self.assertEqual(app_from_arguments(["--reopen-explorer"]), ("main", ["--reopen-explorer"]))
        self.assertEqual(win_api.popwin_app_of(["--app", "studio"]), "studio")

    def test_windows_apps_are_replaced(self):
        self.assertTrue(is_editor("notepad.exe"))
        self.assertTrue(is_editor("shell:AppsFolder\\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"))
        self.assertTrue(is_terminal("shell:AppsFolder\\Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"))
        self.assertTrue(is_terminal("powershell.exe"))
        self.assertFalse(is_terminal("powershell_ise.exe"))
        self.assertTrue(is_studio("powershell_ise.exe"))
        ctx = ResetContext()
        opened = []
        ctx.open_app = lambda name, *arguments: opened.append(name)
        with unittest.mock.patch.object(win_api, "shell_open", return_value=True) as windows_app:
            launch(ctx, "notepad.exe")  # ab Werk startet noch die Windows-App
        self.assertEqual((opened, windows_app.call_count), ([], 1))
        for app_name in ("editor", "terminal", "studio"):
            ctx.config.set(f"replacements.{app_name}", True)
        launch(ctx, "notepad.exe")
        launch(ctx, "cmd.exe")
        launch(ctx, "powershell_ise.exe")
        self.assertEqual(opened, ["editor", "terminal", "studio"])
        self.assertEqual(taskbar_slot(ctx, "terminal")[0], "windowsterminal.exe")
        ctx.config.set("replacements.terminal", False)
        self.assertEqual(taskbar_slot(ctx, "terminal")[0], "popwin:terminal")

    def test_editor_widget(self):
        self.assertEqual(decode(b"\xef\xbb\xbfhallo"), ("hallo", "utf-8-sig"))
        self.assertEqual(decode("äöü".encode("cp1252"))[1], "cp1252")
        self.assertEqual(language_of("x.ps1"), "PowerShell")
        self.assertTrue(all(highlight_rules(language) for language in set(LANGUAGES.values())))
        editor = CodeEditor("dark")
        editor.setPlainText("b\na\nc")
        cursor = editor.textCursor()
        cursor.setPosition(0)
        editor.setTextCursor(cursor)
        editor.move_line(1)
        self.assertEqual(editor.toPlainText(), "a\nb\nc")
        editor.duplicate_line()  # der Cursor steht auf der verschobenen Zeile
        self.assertEqual(editor.toPlainText(), "a\nb\nb\nc")

    def test_editor_keeps_drafts_and_finds(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = ResetContext()
            panel = EditorPanel(ctx, "test_session.json")
            panel.session = Path(folder) / "session.json"
            panel.restore_session()
            panel.editor().setPlainText("Ungespeichert hallo Hallo")
            panel.save_session()
            again = EditorPanel(ctx, "test_session.json")
            again.session = panel.session
            again.restore_session()
            self.assertEqual(again.editor().toPlainText(), "Ungespeichert hallo Hallo")
            again.findbar.find.setText("hallo")
            self.assertEqual(again.findbar.count.text(), "2 Treffer")
            again.findbar.replace.setText("x")
            again.findbar.replace_all()
            self.assertEqual(again.editor().toPlainText(), "Ungespeichert x x")
            target = Path(folder) / "datei.txt"
            self.assertTrue(again.save(path=str(target)))
            self.assertEqual(target.read_bytes(), b"Ungespeichert x x")
            self.assertFalse(again.current().modified)

    def test_terminal_keys_and_colors(self):
        self.assertEqual(to_key_text(Qt.Key.Key_C, "c", True, False, False), "\x03")
        self.assertEqual(to_key_text(Qt.Key.Key_Up, "", False, False, False), "\x1b[A")
        self.assertEqual(to_key_text(Qt.Key.Key_Return, "\r", False, False, False), "\r")
        self.assertEqual(to_key_text(Qt.Key.Key_A, "a", False, False, False), "a")
        self.assertEqual(color_of("red", "#000000").name(), "#c50f1f")
        self.assertEqual(color_of("ff8000", "#000000").name(), "#ff8000")
        self.assertIn("PowerShell", profiles())

    def test_real_shell_answers(self):
        shell = TerminalWidget(["cmd.exe"], tempfile.gettempdir())
        shell.resize(800, 400)
        shell.start()
        try:
            shell.run_line("echo popwin-ok")
            deadline = time.monotonic() + 25  # unter Last braucht eine neue Shell deutlich länger
            text = ""
            while "popwin-ok" not in text.split("echo", 1)[-1] and time.monotonic() < deadline:
                QApplication.processEvents()
                text = "\n".join(shell.screen_data.display)
                time.sleep(0.02)
            self.assertGreaterEqual(text.count("popwin-ok"), 2)
        finally:
            shell.stop()

    def test_ai_command_cleanup(self):
        self.assertEqual(clean_command("```powershell\nGet-Process\n```"), "Get-Process")
        self.assertEqual(clean_command("`dir`"), "dir")

    def test_studio_helpers(self):
        self.assertEqual(run_line(Path("C:/a'b.ps1")), ". 'C:\\a''b.ps1'")
        self.assertEqual(parse_commands('["b","A"]'), ["A", "b"])
        self.assertEqual(parse_commands("kaputt"), [])


class MediaRecovery(unittest.TestCase):
    def controller(self):
        media = MediaController()
        media.connects = 0

        async def connect():
            media.connects += 1

        media._connect = connect
        return media

    def test_hanging_player_does_not_stop_the_display(self):
        media = self.controller()

        async def hang():
            await asyncio.sleep(60)

        media._read = hang
        with unittest.mock.patch("core.media_controller.READ_TIMEOUT", 0.01):
            self.assertIsNone(asyncio.run(media._next_state()))
        self.assertEqual(media.connects, 1)

    def test_reconnects_when_nothing_plays_for_a_while(self):
        media = self.controller()

        async def nothing():
            return None

        media._read = nothing

        async def run():
            for _ in range(RECONNECT_AFTER):
                await media._next_state()

        asyncio.run(run())
        self.assertEqual(media.connects, 1)

    def test_playing_resets_the_counter(self):
        media = self.controller()
        media._idle = RECONNECT_AFTER - 1

        async def playing():
            return {"title": "Lied"}

        media._read = playing
        self.assertEqual(asyncio.run(media._next_state()), {"title": "Lied"})
        self.assertEqual((media._idle, media.connects), (0, 0))


class Blocks(unittest.TestCase):
    def test_columns_follow_the_width(self):
        self.assertEqual(column_count(400, 12), 1)
        self.assertEqual(column_count(1000, 12), 2)
        self.assertEqual(column_count(1500, 12), 3)
        self.assertEqual(column_count(6000, 12), 4)

    def test_blocks_fill_the_shortest_column(self):
        host = QWidget()
        layout = ColumnLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        heights = (200, 50, 50, 50)
        boxes = []
        full = QWidget()
        full.setFixedHeight(30)
        layout.add_full(full)
        for height in heights:
            box = QWidget()
            box.setFixedHeight(height)
            layout.addWidget(box)
            boxes.append(box)
        layout.setGeometry(QRect(0, 0, 1000, 800))
        self.assertEqual(full.geometry(), QRect(0, 0, 1000, 30))
        self.assertEqual((boxes[0].x(), boxes[1].x()), (0, boxes[2].x()))
        self.assertGreater(boxes[1].x(), 0)
        self.assertEqual(boxes[2].y(), boxes[1].y() + 50 + 12)
        self.assertEqual(boxes[3].x(), boxes[1].x())
        self.assertEqual(layout.heightForWidth(1000), 30 + 12 + 200)
        self.assertGreater(layout.heightForWidth(400), layout.heightForWidth(1000))  # eine Spalte wird länger
        item = layout.takeAt(0)
        self.assertIs(item.widget(), full)
        self.assertNotIn(full, layout._full)


class TopbarOptions(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.bar = TopBar(self.ctx, QGuiApplication.primaryScreen(), "top")

    def set(self, field, value):
        self.ctx.config.set_monitor(None, field, value)

    def test_symbols_switch_off(self):
        for field, widget in (("show_tray", self.bar.chevron), ("show_mic", self.bar.mic),
                              ("show_network", self.bar.network), ("show_volume", self.bar.volume),
                              ("show_quick", self.bar.quick), ("show_notifications", self.bar.bell)):
            self.set(field, False)
            self.assertTrue(widget.isHidden(), field)
            self.set(field, True)
            self.assertFalse(widget.isHidden(), field)
        self.bar._state = {"cloud": ["OneDrive"]}
        self.bar._show_cloud()
        self.assertFalse(self.bar.cloud.isHidden())
        self.set("show_cloud", False)
        self.assertTrue(self.bar.cloud.isHidden())

    def test_order_follows_the_setting(self):
        self.assertEqual(complete_order(["bell", "weg", "mic"])[:2], ["bell", "mic"])
        self.assertEqual(len(complete_order([])), len(TOP_ITEMS))
        self.set("top_order", ["bell", "volume", "tray"])
        widgets = [self.bar.status_row.itemAt(i).widget() for i in range(self.bar.status_row.count())]
        self.assertEqual(widgets[:3], [self.bar.bell, self.bar.bell_count, self.bar.volume])

    def test_center_shows_seconds_when_wanted(self):
        now = QDateTime(QDate(2026, 11, 1), QTime(18, 0, 7))
        self.assertTrue(format_center(now, True).endswith("18:00:07"))
        self.assertTrue(format_center(now, False).endswith("18:00"))

    def test_clock_options(self):
        now = QDateTime(QDate(2026, 3, 7), QTime(9, 5, 2))
        self.assertEqual(format_clock(now, False)[0], "09:05")
        self.set("clock_seconds", False)
        self.assertEqual(len(self.bar.time_label.text()), 5)
        self.set("clock_style", "time")
        self.assertTrue(self.bar.date_label.isHidden())
        self.set("clock_style", "date")
        self.assertTrue(self.bar.time_label.isHidden())
        self.set("clock_place", "none")
        self.assertTrue(self.bar.clock.isHidden())


class DockTools(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.windows = lambda: []
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")
        self.row = self.dock.root.layout()

    def tearDown(self):
        if self.dock.tools:
            self.dock.tools.stop()
        self.dock.hide()

    def order(self):
        return [self.row.itemAt(i).widget() for i in range(self.row.count()) if self.row.itemAt(i).widget()]

    def test_only_on_hover_or_always(self):
        self.assertFalse(tools_wanted("off", True, True, True))
        self.assertTrue(tools_wanted("always", True, False, False))
        self.assertFalse(tools_wanted("always", False, True, True))  # Taskbar weg: Leiste auch
        self.assertFalse(tools_wanted("hover", True, False, False))
        self.assertTrue(tools_wanted("hover", True, True, False))
        self.assertTrue(tools_wanted("hover", True, False, True))  # kurz nach dem Wegfahren noch da

    def test_buttons_move_up_and_come_back_in_order(self):
        before = self.order()
        self.ctx.config.set_monitor(None, "dock_tools_items", ["start", "search", "trash"])
        self.ctx.config.set_monitor(None, "dock_tools", "always")
        for widget in (self.dock.start, self.dock.search, self.dock.trash):
            self.assertNotIn(widget, self.order())
            self.assertIs(widget.window(), self.dock.tools)
        self.assertIn(self.dock.nova, self.order())
        self.ctx.config.set_monitor(None, "dock_tools", "off")
        self.assertEqual(self.order(), before)
        self.assertFalse(self.dock.tools.isVisible())

    def test_right_click_gives_the_taskbar_menu(self):
        self.ctx.config.set_monitor(None, "dock_tools", "always")
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(50, 50))
        with unittest.mock.patch.object(Dock, "contextMenuEvent") as menu:
            self.dock.tools.contextMenuEvent(event)
        menu.assert_called_once_with(event)

    def test_beta_for_everyone_but_off_by_default(self):
        """Früher nur im Dev-Build; jetzt eine Beta für alle, die erst angeschaltet werden muss."""
        self.assertIsNone(self.dock.tools)
        self.assertIn(self.dock.start, self.order())
        with unittest.mock.patch("core.dev.is_dev", return_value=False):
            self.ctx.config.set_monitor(None, "dock_tools", "always")
        self.assertIsNotNone(self.dock.tools)


class DockStack(unittest.TestCase):
    """Uhr-Pille und Symbolleiste über der Taskbar: Wer unten sitzt, steht direkt an der Taskbar."""

    def setUp(self):
        self.ctx = ResetContext()
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")

    def tearDown(self):
        if self.dock.tools:
            self.dock.tools.stop()
        if self.dock.pill:
            self.dock.pill.hide()
        self.dock.hide()

    def test_position_math_both_edges(self):
        # Taskbar unten: oben = 1000, Fenster 30 hoch, Abstand 8
        self.assertEqual(dock_tools.stacked_y("bottom", 1000, 1040, 30, 8), 962)
        self.assertEqual(dock_tools.stacked_y("bottom", 1000, 1040, 30, 8, under=26), 928)
        # Taskbar oben: alles gespiegelt, „unten“ heißt näher an der Taskbar
        self.assertEqual(dock_tools.stacked_y("top", 0, 40, 30, 8), 48)
        self.assertEqual(dock_tools.stacked_y("top", 0, 40, 30, 8, under=26), 82)

    def test_who_sits_below(self):
        self.assertTrue(dock_tools.sits_below("clock_below", "clock"))
        self.assertFalse(dock_tools.sits_below("clock_below", "tools"))
        self.assertTrue(dock_tools.sits_below("tools_below", "tools"))
        self.assertFalse(dock_tools.sits_below("tools_below", "clock"))

    def _both(self, order: str):
        self.ctx.config.set_monitor(None, "dock_stack_order", order)
        self.ctx.config.set_monitor(None, "clock_place", "dock_center")
        self.ctx.config.set_monitor(None, "dock_tools_items", ["start", "search"])
        self.ctx.config.set_monitor(None, "dock_tools", "always")
        self.dock.show()
        self.dock.setWindowOpacity(1.0)
        self.dock._place_clock()
        self.dock.pill.follow()
        self.dock.tools.fade.stop()
        self.dock.tools.setWindowOpacity(1.0)
        self.dock.tools.show()
        self.dock.tools._place()
        self.dock.pill.follow()
        return self.dock.pill.frameGeometry(), self.dock.tools.frameGeometry(), self.dock.frameGeometry()

    def test_clock_below_tools_above(self):
        pill, tools, bar = self._both("clock_below")
        self.assertEqual(pill.bottom() + 1, bar.top() - dock_tools.GAP)
        self.assertLess(tools.bottom(), pill.top())

    def test_tools_below_clock_above(self):
        pill, tools, bar = self._both("tools_below")
        self.assertEqual(tools.bottom() + 1, bar.top() - dock_tools.GAP)
        self.assertLess(pill.bottom(), tools.top())

    def test_only_one_visible_sits_at_the_taskbar(self):
        _pill, _tools, bar = self._both("tools_below")
        self.dock.tools.hide()  # etwa nur beim Drüberfahren und gerade nicht drüber
        self.dock.pill.follow()
        self.assertEqual(self.dock.pill.frameGeometry().bottom() + 1, bar.top() - dock_tools.GAP)
        self.dock.pill.follow()  # und springt nicht beim nächsten Takt
        self.assertEqual(self.dock.pill.frameGeometry().bottom() + 1, bar.top() - dock_tools.GAP)
        self.ctx.config.set_monitor(None, "dock_stack_order", "clock_below")
        self.dock.pill.hide()
        self.dock.tools.show()
        self.dock.tools._place()
        self.assertEqual(self.dock.tools.frameGeometry().bottom() + 1, bar.top() - dock_tools.GAP)

    def test_picker_shows_both_rows_and_swaps(self):
        picker = PlacePicker("dock_center", stack_order="clock_below", show_stack=True)
        picker.resize(420, 236)
        swapped = []
        picker.stack_changed.connect(swapped.append)
        self.assertEqual(picker.place_at(picker.tools_rect().center()), "tools")
        self.assertLess(picker.tools_rect().top(), picker._spot_rect("dock_center").top())  # Symbole oben
        picker.mousePressEvent(unittest.mock.Mock(position=lambda: picker.tools_rect().center()))
        self.assertEqual(swapped, ["tools_below"])
        self.assertGreater(picker.tools_rect().top(), picker._spot_rect("dock_center").top())  # jetzt unten

    def test_picker_without_toolbar_is_as_before(self):
        picker = PlacePicker("dock_center", show_stack=False)
        picker.resize(420, 236)
        self.assertNotEqual(picker.place_at(picker.tools_rect().center()), "tools")


class Updates(unittest.TestCase):
    RELEASE = {"tag_name": "v3.10", "body": "- Neu", "html_url": "https://github.com/x/popwin/releases/tag/v3.10",
               "assets": [{"name": "PopWin.Setup.exe", "browser_download_url": "https://x/s.exe", "size": 3, "digest": "sha256:" + "ab" * 32},
                          {"name": "PopWin Portable.zip", "browser_download_url": "https://x/p.zip", "size": 5}]}

    def test_versions_compare_as_numbers(self):
        self.assertTrue(updater.is_newer("3.10", "3.9"))
        self.assertTrue(updater.is_newer("v4", "3.9"))
        self.assertFalse(updater.is_newer("3.6", "3.6"))
        self.assertFalse(updater.is_newer("3.5", "3.6"))

    def test_release_is_read(self):
        release = updater.read_release(self.RELEASE)
        self.assertEqual(release["version"], "3.10")
        self.assertEqual(release["setup"]["url"], "https://x/s.exe")  # GitHub macht aus Leerzeichen Punkte
        self.assertEqual(release["setup"]["sha256"], "ab" * 32)
        self.assertEqual(release["portable"]["sha256"], "")

    def test_wrong_checksum_is_thrown_away(self):
        answer = unittest.mock.MagicMock()
        answer.__enter__.return_value.read.side_effect = [b"abc", b""]
        with unittest.mock.patch("urllib.request.urlopen", return_value=answer):
            with self.assertRaises(OSError):
                updater.download({"url": "https://x/s.exe", "size": 3, "sha256": "00" * 32})
            answer.__enter__.return_value.read.side_effect = [b"abc", b""]
            import hashlib
            path = updater.download({"url": "https://x/s.exe", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()})
        self.assertEqual(path.read_bytes(), b"abc")

    def test_skipped_version_stays_quiet_unless_asked(self):
        ctx = ResetContext()
        ctx.config.set("updates.skipped", "3.10", notify=False)
        checker = updater.UpdateChecker(ctx.config)
        found = []
        checker.found.connect(found.append)
        with unittest.mock.patch.object(updater, "fetch_latest", return_value=updater.read_release(self.RELEASE)):
            checker._run(manual=False)
            self.assertEqual(found, [])
            checker._run(manual=True)
        self.assertEqual(len(found), 1)

    @staticmethod
    def http_error(code: int, headers: dict | None = None):
        import email.message
        import urllib.error
        message = email.message.Message()
        for key, value in (headers or {}).items():
            message[key] = value
        return urllib.error.HTTPError("https://api.github.com/x", code, "Fehler", message, None)

    def answers(self, *replies):
        """urlopen, das nacheinander antwortet: ein dict als JSON, eine Exception wird geworfen."""
        seen = []

        def fake(request, timeout=None):
            seen.append(request)
            reply = replies[len(seen) - 1]
            if isinstance(reply, Exception):
                raise reply
            answer = unittest.mock.MagicMock()
            answer.__enter__.return_value = io.BytesIO(json.dumps(reply).encode())
            return answer
        return unittest.mock.patch("urllib.request.urlopen", side_effect=fake), seen

    def test_no_token_is_ever_sent(self):
        patcher, seen = self.answers(self.RELEASE)
        with patcher:
            updater.fetch_latest("ich/popwin")
        self.assertNotIn("Authorization", seen[0].headers)
        self.assertEqual(seen[0].full_url, "https://api.github.com/repos/ich/popwin/releases/latest")

    def test_repository_without_release_is_not_an_error(self):
        patcher, seen = self.answers(self.http_error(404), {"full_name": "ich/popwin"})
        with patcher:
            self.assertEqual(updater.fetch_latest("ich/popwin")["version"], "")
        self.assertEqual(seen[1].full_url, "https://api.github.com/repos/ich/popwin")

    def test_errors_are_told_apart(self):
        import urllib.error
        cases = [((self.http_error(404), self.http_error(404)), "not_found"),
                 ((self.http_error(403, {"X-RateLimit-Remaining": "0"}),), "rate_limit"),
                 ((self.http_error(401),), "auth"),
                 ((self.http_error(403),), "auth"),
                 ((self.http_error(503),), "server"),
                 ((urllib.error.URLError("getaddrinfo failed"),), "offline"),
                 ((TimeoutError("timed out"),), "offline")]
        for replies, kind in cases:
            patcher, _seen = self.answers(*replies)
            with patcher, self.assertRaises(updater.UpdateError) as caught:
                updater.fetch_latest("ich/popwin")
            self.assertEqual(caught.exception.kind, kind, replies)
            self.assertEqual(caught.exception.message, updater.MESSAGES[kind])

    def test_settings_show_the_real_reason(self):
        ctx = ResetContext()
        checker = updater.UpdateChecker(ctx.config)
        results = []
        checker.finished.connect(results.append)
        with unittest.mock.patch.object(updater, "fetch_latest", side_effect=updater.UpdateError("not_found")):
            checker._run(manual=True)
        with unittest.mock.patch.object(updater, "fetch_latest", return_value={"version": ""}):
            checker._run(manual=True)
        self.assertEqual(results, [updater.MESSAGES["not_found"], updater.NO_RELEASE])
        ctx.settings._show_update_state(updater.NO_RELEASE)
        self.assertEqual(ctx.settings.update_state.text(), "Kein Update gefunden.")

    def test_download_errors_are_readable(self):
        with unittest.mock.patch("urllib.request.urlopen", side_effect=self.http_error(404)):
            with self.assertRaises(updater.UpdateError) as caught:
                updater.download({"url": "https://x/s.exe", "size": 3, "sha256": ""})
        self.assertEqual(caught.exception.kind, "not_found")

    def test_no_source_no_request(self):
        ctx = ResetContext()
        checker = updater.UpdateChecker(ctx.config)
        with unittest.mock.patch.object(updater, "REPO", ""), unittest.mock.patch("threading.Thread") as thread:
            checker.check(manual=True)
        thread.assert_not_called()

    def test_dialog_from_source_opens_the_page(self):
        ctx = ResetContext()
        dialog = UpdateDialog(ctx)
        dialog.offer(updater.read_release(self.RELEASE))
        self.assertEqual(dialog.install.text(), "Download-Seite öffnen")  # aus dem Quellordner wird nichts installiert
        self.assertIn("Neu", dialog.notes.toPlainText())
        dialog._skip()
        self.assertEqual(ctx.config.get("updates.skipped"), "3.10")
        dialog.hide()


class NativeSettingsWindow(unittest.TestCase):
    def test_frameless_window_gets_the_style_of_a_normal_app(self):
        style = window_frame.native_style(0x96CF0000)  # so legt Qt ein rahmenloses Fenster an
        self.assertFalse(style & window_frame.WS_POPUP)
        for flag in (window_frame.WS_CAPTION, window_frame.WS_THICKFRAME, window_frame.WS_MINIMIZEBOX, window_frame.WS_MAXIMIZEBOX):
            self.assertTrue(style & flag)
        self.assertEqual(window_frame.native_style(style), style)

    def test_edges_resize(self):
        self.assertEqual(window_frame.hit_edge(1, 1, 500, 400), window_frame.HTTOPLEFT)
        self.assertEqual(window_frame.hit_edge(250, 398, 500, 400), window_frame.HTBOTTOM)
        self.assertEqual(window_frame.hit_edge(250, 200, 500, 400), 0)


class ClockPlace(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.windows = lambda: []
        self.bar = TopBar(self.ctx, QGuiApplication.primaryScreen(), "top")
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")

    def tearDown(self):
        self.bar.hide()
        self.dock.hide()

    def shown(self):
        """Wo man die Uhrzeit gerade lesen könnte."""
        self.bar._tick()
        places = []
        if not self.bar.clock.isHidden():
            places.append("top_right")
        if ":" in self.bar.center.text():
            places.append("top_center")
        if not self.dock.clock.isHidden():
            places.append(self.dock._clock_at)
        if self.dock._pill_timer.isActive():
            places.append("dock_center")
        return places

    def test_the_time_shows_in_exactly_one_place(self):
        for place in ("top_right", "top_center", "dock_left", "dock_center", "dock_right"):
            self.ctx.config.set_monitor(None, "clock_place", place)
            self.assertEqual(self.shown(), [place])
        self.ctx.config.set_monitor(None, "clock_place", "none")
        self.assertEqual(self.shown(), [])

    def test_dock_clock_sits_at_the_chosen_end(self):
        row = self.dock.root.layout()
        self.ctx.config.set_monitor(None, "clock_place", "dock_left")
        self.assertIs(row.itemAt(0).widget(), self.dock.clock)
        self.ctx.config.set_monitor(None, "clock_place", "dock_right")
        self.assertIs(row.itemAt(row.count() - 1).widget(), self.dock.clock)

    def test_pill_hides_with_the_dock_and_in_fullscreen(self):
        self.ctx.config.set_monitor(None, "clock_place", "dock_center")
        pill = self.dock.pill
        self.dock.hide()
        pill.follow()
        self.assertFalse(pill.isVisible())
        self.dock.show()
        self.dock.setWindowOpacity(1)
        self.dock._fullscreen = True
        self.dock._peek_until = 0.0
        pill.follow()
        self.assertFalse(pill.isVisible())

    def test_center_title_gives_way_to_the_clock(self):
        self.ctx.config.set_monitor(None, "center", "title")
        self.ctx.config.set_monitor(None, "clock_place", "top_center")
        self.bar._tick()
        self.assertIn(":", self.bar.center.text())

    def test_old_settings_keep_one_clock(self):
        both = {"show_clock": True, "center": "clock", "center_seconds": True}
        migrate_clock(both)
        self.assertEqual((both["clock_place"], both["center"]), ("top_right", "none"))
        self.assertNotIn("show_clock", both)
        middle = {"show_clock": False, "center": "clock"}
        migrate_clock(middle)
        self.assertEqual(middle["clock_place"], "top_center")
        title = {"show_clock": False, "center": "title"}
        migrate_clock(title)
        self.assertEqual((title["clock_place"], title["center"]), ("none", "title"))
        done = {"clock_place": "dock_left", "center": "clock"}
        migrate_clock(done)
        self.assertEqual(done["center"], "clock")  # schon umgestellt: nichts mehr anfassen

    def test_picker_reports_the_clicked_spot(self):
        picker = PlacePicker("top_right")
        picker.resize(420, 236)
        rect = picker._spot_rect("dock_left")
        self.assertEqual(picker.place_at(rect.center()), "dock_left")
        self.assertEqual(picker.place_at(QPointF(210, 118)), "")

    def test_center_text_sits_in_the_real_middle(self):
        self.bar.root.resize(2000, 34)
        self.bar.center.setText("Claude")
        self.bar._place_center()
        middle = self.bar.center.geometry().center().x()
        self.assertLessEqual(abs(middle - 1000), 2)

    def test_title_only_on_its_own_monitor(self):
        self.assertTrue(win_api.contains_center((100, 100, 500, 500), (0, 0, 1920, 1080)))
        self.assertFalse(win_api.contains_center((2000, 0, 3000, 900), (0, 0, 1920, 1080)))

    def test_editor_stores_choices(self):
        key = screen_key(QGuiApplication.primaryScreen())
        editor = TopItemsEditor(self.ctx, key)
        self.assertEqual(editor.count(), len(TOP_ITEMS))
        editor.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(self.ctx.config.monitor(key)["show_tray"])
        editor.insertItem(0, editor.takeItem(editor.count() - 1))
        editor._store_order()
        self.assertEqual(self.ctx.config.monitor(key)["top_order"][0], "bell")


class TaskbarOptions(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.windows = lambda: []
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")
        self.dock._windows_here = lambda: [win_api.WindowInfo(1, "A", r"C:\x\app.exe", 4),
                                           win_api.WindowInfo(2, "B", r"C:\x\app.exe", 4)]

    def set(self, field, value):
        self.ctx.config.set_monitor(None, field, value)

    def test_elements_switch_on_and_off(self):
        for field, widget in (("show_recycle", self.dock.trash), ("show_nova_button", self.dock.nova), ("show_start", self.dock.start),
                              ("show_search", self.dock.search), ("show_desktop", self.dock.desktop)):
            self.set(field, True)
            self.assertFalse(widget.isHidden(), field)
            self.set(field, False)
            self.assertTrue(widget.isHidden(), field)

    def test_search_presets(self):
        for style, width in SEARCH_WIDTHS.items():
            self.set("search_style", style)
            self.assertEqual(self.dock.search.objectName(), "SearchField")
            self.assertEqual(self.dock.search.maximumWidth(), width)
            self.assertEqual(self.dock.search.text(), "Suche")
        self.set("search_style", "icon")
        self.assertEqual(self.dock.search.objectName(), "StartButton")
        self.assertEqual(self.dock.search.text(), G.SEARCH)
        self.assertGreater(self.dock.search.maximumWidth(), 1000)

    def test_player_presets(self):
        media = self.dock.media
        self.dock.on_meta_changed = lambda meta: None
        media.set_compact(False)
        self.set("media_style", "cover")
        self.assertEqual((media.width(), media.prev.isHidden(), media.thumb.isHidden(), media.title.isHidden()),
                         (MEDIA_WIDTHS["cover"], True, False, True))
        self.set("media_style", "controls")
        self.assertEqual((media.prev.isHidden(), media.thumb.isHidden()), (False, True))
        self.set("media_style", "full")
        self.set("media_width", 300)
        self.assertEqual(media.maximumWidth(), 300)
        self.assertFalse(media.artist.isHidden())
        self.set("media_style", "compact")
        self.assertTrue(media.artist.isHidden())
        self.assertFalse(media.title.isHidden())

    def test_home_button_styles(self):
        self.set("start_style", "windows")
        self.assertEqual(self.dock.start.text(), G.WINDOWS)
        self.set("start_style", "logo")
        self.assertEqual(self.dock.start.text(), "")
        self.assertFalse(self.dock.start.icon().isNull())
        self.set("start_style", "home")
        self.assertEqual(self.dock.start.text(), G.START)
        self.assertTrue(self.dock.start.icon().isNull())

    def test_labels_and_counts(self):
        self.set("task_labels", True)
        self.dock.refresh()
        self.assertEqual(self.dock._buttons["app.exe"].text(), "App")
        self.set("task_labels", False)
        self.dock.refresh()
        self.assertEqual(self.dock._buttons["app.exe"].text(), "")
        self.set("task_counts", True)
        self.set("task_indicator", "dot")
        self.assertEqual((self.dock.task_counts, self.dock.task_indicator), (True, "dot"))
        self.dock._buttons["app.exe"].setProperty("running", True)
        self.dock._buttons["app.exe"].grab()  # zeichnet Punkt und Anzahl ohne Fehler

    def test_settings_page_offers_everything(self):
        host = self.ctx.settings.taskbar_monitors
        titles = [box.title() for box in host.findChildren(QGroupBox)]
        self.assertTrue(all(any(name in title for title in titles) for name in ("Elemente", "Apps", "Größe")))
        monitor_titles = [box.text() for box in self.ctx.settings.monitor_page.findChildren(QCheckBox)]
        self.assertNotIn("Papierkorb anzeigen", monitor_titles)
        texts = [box.text() for box in host.findChildren(QCheckBox)]
        for label in ("Papierkorb anzeigen", "Player anzeigen", "Suche anzeigen", "Home-Knopf anzeigen"):
            self.assertIn(label, texts)


class MonitorPage(unittest.TestCase):
    LAYOUT = {(0, 0, 3440, 1440): r"\\.\DISPLAY3", (3440, 104, 5360, 1184): r"\\.\DISPLAY1",
              (-1920, 117, 0, 1197): r"\\.\DISPLAY2", (782, 1440, 2702, 2520): r"\\.\DISPLAY5"}

    def test_numbers_have_no_gaps(self):
        numbers = win_api.rank_monitors(self.LAYOUT)
        self.assertEqual(numbers[(0, 0, 3440, 1440)], 3)
        self.assertEqual(numbers[(782, 1440, 2702, 2520)], 4)
        self.assertEqual(sorted(numbers.values()), [1, 2, 3, 4])
        self.assertEqual(sorted(win_api.monitor_numbers().values()), list(range(1, len(win_api.monitor_rects()) + 1)))

    def test_map_keeps_the_windows_arrangement(self):
        places = fit_rects(list(self.LAYOUT), 700, 230)
        main, right, left, below = places
        self.assertLess(left[0], main[0])
        self.assertGreater(right[0], main[0])
        self.assertGreater(below[1], main[1])
        self.assertTrue(all(x >= 0 and y >= 0 and x + w <= 700 and y + h <= 230 for x, y, w, h in places))
        self.assertGreater(main[2], right[2])

    def test_picking_a_monitor_shows_its_settings(self):
        ctx = ResetContext()
        window = ctx.settings
        entries = window._monitor_entries()
        self.assertEqual([entry.number for entry in entries], sorted(entry.number for entry in entries))
        self.assertTrue(ctx.config.synced)
        window._pick_monitor(entries[-1].key)
        self.assertFalse(ctx.config.synced)
        window._fill_monitors()
        titles = [box.title() for box in window.monitor_page.findChildren(QGroupBox) if not box.isHidden() or True]
        self.assertIn(entries[-1].title, titles)

    def test_taskbar_page_shows_the_picked_monitor(self):
        ctx = ResetContext()
        window = ctx.settings
        entries = window._monitor_entries()
        host = window.taskbar_monitors
        shown = lambda: next(w for w in host.findChildren(PinEditor) if not w.parent().isHidden())
        self.assertEqual(shown().screen, "" if next(e for e in entries if e.primary) else None)
        other = next((entry for entry in entries if not entry.primary), None)
        if other is None:
            return
        window._pick_taskbar_monitor(other.key)
        window._fill_taskbar_monitors()
        self.assertEqual(shown().screen, other.key)
        self.assertFalse(ctx.config.synced)  # ein einzelner Bildschirm heißt einzeln einstellen
        tiles = host.findChildren(MonitorTile)
        self.assertEqual(sum(tile.isChecked() and not tile.parent().isHidden() for tile in tiles), 1)

    def test_picking_a_monitor_keeps_the_scroll_position(self):
        ctx = ResetContext()
        window = ctx.settings
        window.move(-9000, -9000)
        window.resize(1000, 500)
        window.show()
        window.tabs.setCurrentWidget(window.taskbar_page)
        app.processEvents()
        bar = window.taskbar_page.verticalScrollBar()
        bar.setValue(140)
        position = bar.value()
        window._pick_taskbar_monitor(window._monitor_entries()[-1].key)
        for _ in range(5):
            app.processEvents()
        self.assertEqual(bar.value(), position)
        window.hide()

    def test_all_screens_switch_is_on_every_page(self):
        ctx = ResetContext()
        window = ctx.settings
        for page in (window.monitor_page, window.topbar_page, window.taskbar_page):
            labels = [box.text() for box in page.findChildren(QCheckBox)]
            self.assertIn("Alle Bildschirme gleichzeitig einstellen", labels)
        window._switch_mode("separate")
        self.assertFalse(ctx.config.synced)
        window._switch_mode("sync")
        self.assertTrue(ctx.config.synced)

    def test_tiles_take_no_focus(self):
        tile = MonitorTile(MonitorEntry("a", 1, "Name", (0, 0, 10, 10), True))
        self.assertEqual(tile.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_number_badges_disappear_again(self):
        numbers = MonitorNumbers()
        numbers.show([(1, QRect(-9000, -9000, 1920, 1080)), (2, QRect(-7000, -9000, 1920, 1080))])
        self.assertEqual(len(numbers.badges), 2)
        self.assertTrue(numbers.timer.isActive())
        numbers.show([(1, QRect(-9000, -9000, 1920, 1080))])  # erneut gedrückt: keine doppelten Nummern
        self.assertEqual(len(numbers.badges), 1)
        numbers.timer.stop()
        numbers.hide()
        self.assertEqual(numbers.badges, [])


class Files(unittest.TestCase):
    def test_unique_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = unique_path(root, "Neue Textdatei", ".txt")
            first.touch()
            self.assertEqual(unique_path(root, "Neue Textdatei", ".txt").name, "Neue Textdatei (2).txt")


def ovh_module():
    """Die OVH-Anbindung liegt in devtools und kommt nur über importlib, wie in PopWin selbst."""
    import importlib
    return importlib.import_module("devtools.ovh_domains")


class FakeOvh:
    """Spielt die OVH-API nach. Kein Test spricht je mit dem echten OVH."""

    def __init__(self):
        self.calls = []
        self.records = {1: {"id": 1, "zone": "example.de", "subDomain": "www", "fieldType": "A", "target": "1.2.3.4", "ttl": 3600},
                        2: {"id": 2, "zone": "example.de", "subDomain": "", "fieldType": "MX", "target": "mx.example.de.", "ttl": 0}}

    def get(self, path, **query):
        self.calls.append(("GET", path, query))
        if path == "/domain":
            return ["example.de"]
        if path.endswith("/serviceInfos"):
            return {"expiration": "2026-11-17", "renew": {"automatic": False}}
        if path.endswith("/nameServer"):
            return [7]
        if "/nameServer/" in path:
            return {"host": "dns1.ovh.net"}
        if path.endswith("/record"):
            return [rid for rid, r in self.records.items()
                    if all(r[{"fieldType": "fieldType", "subDomain": "subDomain"}[k]] == v for k, v in query.items())]
        if "/record/" in path:
            return dict(self.records[int(path.rsplit("/", 1)[1])])
        raise KeyError(path)

    def put(self, path, **body):
        self.calls.append(("PUT", path, body))
        self.records[int(path.rsplit("/", 1)[1])].update(body)

    def post(self, path, **body):
        self.calls.append(("POST", path, body))
        if path.endswith("/record"):
            self.records[max(self.records) + 1] = {"id": max(self.records) + 1, "zone": "example.de", **body}

    def delete(self, path):
        self.calls.append(("DELETE", path, {}))
        self.records.pop(int(path.rsplit("/", 1)[1]))


class NovaArtifacts(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.nova = self.ctx.nova
        self.box = self.nova.toolbox

    def tearDown(self):
        self.ctx.nova_panel.hide()

    def run_tool(self, name, **arguments):
        result = self.box.run(name, arguments)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def test_store_lives_in_the_data_folder_and_survives_a_restart(self):
        from core.paths import DATA_DIR
        self.assertEqual(nova_artifacts.ARTIFACT_DIR, DATA_DIR / "nova_artifacts")
        release = importlib.import_module("devtools.release")
        self.assertIn("config/*", release.GITIGNORE)  # im Quellordner liegt DATA_DIR in config/, das nie eingecheckt wird
        self.run_tool("artifact_create", title="Umzug planen", kind="aufgabe", steps="Kisten\nWagen\nSchlüssel")
        again = ArtifactStore(self.nova.artifacts.folder)
        self.assertEqual([item["title"] for item in again.all()], ["Umzug planen"])

    def test_ids_cannot_leave_the_folder(self):
        with self.assertRaises(ValueError):
            self.nova.artifacts._path("../../settings")
        self.nova.artifacts.folder.mkdir(parents=True, exist_ok=True)
        (self.nova.artifacts.folder / "kaputt.json").write_text("{", encoding="utf-8")
        self.assertEqual(ArtifactStore(self.nova.artifacts.folder).all(), [])

    def test_checklist_progress_and_notes(self):
        made = self.run_tool("artifact_create", title="Domains einrichten", kind="Aufgabe", steps="- Keys holen\n- Eintragen\n- Prüfen")
        artifact_id = made.split(": ")[1]
        self.assertIn("1 von 3", self.run_tool("artifact_update", id=artifact_id, step_done="1"))
        self.assertIn("2 von 3", self.run_tool("artifact_update", id=artifact_id, step_done="eintragen", note="Keys sind drin"))
        artifact = self.nova.artifacts.get(artifact_id)
        self.assertEqual(nova_artifacts.progress(artifact), (2, 3))
        self.assertIn("Keys sind drin", artifact["notes"])
        self.assertTrue(any("Erledigt: Keys holen" in entry["text"] for entry in artifact["history"]))
        self.assertIn("2 von 3 erledigt", self.run_tool("artifact_list"))
        self.assertIn("[x] Keys holen", self.run_tool("artifact_read", id="Domains"))  # auch über einen Teil des Titels

    def test_risks_are_decided_by_popwin(self):
        risk = {name: nova_tools.BY_NAME[name].risk for name in nova_tools.BY_NAME if name.startswith("artifact_")}
        self.assertEqual(risk, {"artifact_create": nova_tools.FREE, "artifact_list": nova_tools.FREE, "artifact_read": nova_tools.FREE,
                                "artifact_update": nova_tools.FREE, "artifact_delete": nova_tools.ASK,
                                "artifact_schedule": nova_tools.ASK, "artifact_run_tool": nova_tools.ALWAYS})
        self.assertEqual(nova_tools.BY_NAME["ovh_dns_change"].risk, nova_tools.ALWAYS)

    def test_prompt_carries_the_open_artifacts_shortened(self):
        for number in range(12):
            self.run_tool("artifact_create", title=f"Aufgabe {number}", kind="aufgabe")
        done = self.run_tool("artifact_create", title="Schon fertig", kind="notiz").split(": ")[1]
        self.run_tool("artifact_update", id=done, state="erledigt")
        text = self.nova.artifacts.as_prompt()
        self.assertNotIn("Schon fertig", text)
        self.assertIn("weitere", text)
        self.assertLessEqual(len(text), nova_artifacts.PROMPT_CHARS + 200)
        prompt = nova_brain.system_prompt("", datetime(2026, 11, 3, 9, 0), text)
        self.assertIn("DEINE OFFENEN ARTEFAKTE", prompt)
        self.assertIn("artifact_create", prompt)

    def test_schedule_only_for_monitors(self):
        task = self.run_tool("artifact_create", title="Nur Liste", kind="aufgabe").split(": ")[1]
        self.assertFalse(self.box.run("artifact_schedule", {"id": task, "hours": 6})["ok"])
        watch = self.run_tool("artifact_create", title="Domains", kind="ueberwachung").split(": ")[1]
        self.run_tool("artifact_schedule", id=watch, hours=6)
        artifact = self.nova.artifacts.get(watch)
        self.assertFalse(nova_artifacts.is_due(artifact, datetime.now()))
        self.assertTrue(nova_artifacts.is_due(artifact, datetime.now() + timedelta(hours=7)))
        self.assertFalse(self.box.run("artifact_schedule", {"id": watch, "hours": 500})["ok"])

    def test_deleting_asks_and_then_removes_the_file(self):
        made = self.run_tool("artifact_create", title="Weg damit", kind="notiz", content="x").split(": ")[1]
        turn = NovaTurn(self.nova, "test", [])
        asked = []
        turn.approval.connect(lambda number, title, detail, *_rest: (asked.append(detail), turn.resolve(number, True, False)),
                              Qt.ConnectionType.DirectConnection)
        call = unittest.mock.Mock(args={"id": made}, id="1")
        call.name = "artifact_delete"
        turn._use_tool(call)
        self.assertIn("Weg damit", asked[0])
        self.assertFalse((self.nova.artifacts.folder / f"{made}.json").exists())

    def test_tools_never_get_at_credentials(self):
        result = self.box.run("artifact_create", {"title": "Böse", "kind": "werkzeug", "content": "import keyring\nprint(1)"})
        self.assertFalse(result["ok"])
        self.assertIn("Zugangsdaten", result["result"])

    def _tool(self, read_only=True):
        made = self.run_tool("artifact_create", title="Zähler", kind="werkzeug", content="print(41 + 1)", read_only=read_only)
        return made.split(": ")[1]

    def test_tool_runs_in_its_own_process_without_secrets(self):
        tool = self._tool()
        self.ctx.config.keys["conn:demo:token"] = "geheim-12345"
        self.nova.connections.define("demo", ["token"])
        self.nova.artifacts.get(tool)["connection"] = "demo"
        done = subprocess.CompletedProcess([], 0, b"Ergebnis geheim-12345\n", b"")
        with unittest.mock.patch("core.nova_tools.subprocess.run", return_value=done) as started:
            output = self.run_tool("artifact_run_tool", id=tool)
        command, kwargs = started.call_args.args[0], started.call_args.kwargs
        self.assertIn("nova_tool_runner.py", " ".join(command))
        self.assertEqual(kwargs["env"]["POPWIN_TOOL_CONNECTION"], "demo")
        self.assertNotIn("geheim-12345", output)
        self.assertIn("***", output)

    def test_read_only_tool_asks_once_per_code(self):
        tool = self._tool(read_only=True)
        turn = NovaTurn(self.nova, "test", [])
        asked = []
        turn.approval.connect(lambda number, title, detail, *_rest: (asked.append(detail), turn.resolve(number, True, False)),
                              Qt.ConnectionType.DirectConnection)
        done = subprocess.CompletedProcess([], 0, b"42\n", b"")
        call = unittest.mock.Mock(args={"id": tool}, id="1")
        call.name = "artifact_run_tool"
        with unittest.mock.patch("core.nova_tools.subprocess.run", return_value=done):
            turn._use_tool(call)
            turn._use_tool(call)
        self.assertEqual(len(asked), 1)
        self.assertIn("print(41 + 1)", asked[0])  # der Host sieht vorher den Code
        self.run_tool("artifact_update", id=tool, code="print(43)")
        self.assertFalse(self.box.preapproved("artifact_run_tool", {"id": tool}))

    def test_changing_tool_always_asks(self):
        tool = self._tool(read_only=False)
        turn = NovaTurn(self.nova, "test", [])
        asked = []
        turn.approval.connect(lambda number, title, detail, dangerous, for_chat: (asked.append((dangerous, for_chat)),
                                                                                  turn.resolve(number, True, True)),
                              Qt.ConnectionType.DirectConnection)
        call = unittest.mock.Mock(args={"id": tool}, id="1")
        call.name = "artifact_run_tool"
        with unittest.mock.patch("core.nova_tools.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            turn._use_tool(call)
            turn._use_tool(call)
        self.assertEqual(asked, [(True, False), (True, False)])  # nie „in diesem Chat immer“

    def test_user_can_create_and_edit_artifacts(self):
        view = ArtifactsView(self.nova, lambda: {})
        view.edit("")
        editor = view.editor
        editor.title.setText("Fenster putzen")
        editor.steps.setPlainText("Eimer\nLappen")
        editor.due.setText("2026-11-10")
        editor._save()
        artifact = next(iter(self.nova.artifacts.items.values()))
        self.assertEqual((artifact["title"], artifact["due"], nova_artifacts.progress(artifact)), ("Fenster putzen", "2026-11-10", (0, 2)))
        self.assertIn("von dir", artifact["history"][0]["text"])
        view.edit(artifact["id"])
        editor = view.editor
        artifact["steps"][0]["done"] = True
        editor.steps.setPlainText("Eimer\nLappen\nLeiter")
        editor._save()
        self.assertEqual(nova_artifacts.progress(artifact), (1, 3))  # Erledigtes bleibt erledigt

    def test_editor_refuses_tools_with_credential_access(self):
        view = ArtifactsView(self.nova, lambda: {})
        view.edit("")
        editor = view.editor
        editor.kind.setCurrentIndex(editor.kind.findData("werkzeug"))
        editor.title.setText("x")
        editor.text.setPlainText("import win32cred")
        editor._save()
        self.assertEqual(self.nova.artifacts.items, {})
        self.assertIn("Zugangsdaten", editor.problem.text())

    def test_panel_shows_cards_and_details(self):
        made = self.run_tool("artifact_create", title="Domains einrichten", kind="aufgabe", steps="A\nB").split(": ")[1]
        panel = self.ctx.nova_panel
        panel.show_page(1)
        cards = panel.artifacts.findChildren(QWidget, "NovaArtifactCard")
        self.assertEqual(len(cards), 1)
        self.assertEqual(panel.tabs[1].text(), "Artefakte · 1")
        # [Antigravity Start: Test für sauberes Umschalten ohne überlappenden "Neues Artefakt"-Button]
        create_buttons = [b for b in panel.artifacts.findChildren(QPushButton) if b.text() == "Neues Artefakt"]
        self.assertEqual(len(create_buttons), 1)

        panel.artifacts.open_artifact(made)
        steps = panel.artifacts.findChildren(QCheckBox, "NovaArtifactStep")
        self.assertEqual([step.text() for step in steps], ["A", "B"])
        steps[0].setChecked(True)
        self.assertEqual(nova_artifacts.progress(self.nova.artifacts.get(made)), (1, 2))

        # "Neues Artefakt" darf im Detail nicht mehr sichtbar sein
        create_in_detail = [b for b in panel.artifacts.findChildren(QPushButton) if b.text() == "Neues Artefakt" and b.isVisibleTo(panel.artifacts)]
        self.assertEqual(len(create_in_detail), 0)

        # Zurück zur Liste: "Neues Artefakt" existiert wieder genau einmal
        panel.artifacts.open_artifact("")
        create_back = [b for b in panel.artifacts.findChildren(QPushButton) if b.text() == "Neues Artefakt" and b.isVisibleTo(panel.artifacts)]
        self.assertEqual(len(create_back), 1)
        # --- [Antigravity Ende] ---


class NovaWatch(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.nova = self.ctx.nova
        self.notices = []
        self.nova.notice.connect(lambda *args: self.notices.append(args))

    def test_due_task_reminds_once(self):
        artifact = nova_artifacts.new_artifact("Steuer", "aufgabe")
        artifact["due"] = "2026-11-03"
        self.nova.artifacts.save(artifact)
        self.nova.watcher.tick(datetime(2026, 11, 2, 9))
        self.assertEqual(self.notices, [])
        self.nova.watcher.tick(datetime(2026, 11, 3, 9))
        self.nova.watcher.tick(datetime(2026, 11, 3, 10))
        self.assertEqual(len(self.notices), 1)
        self.assertIn("Aufgabe fällig: Steuer", self.notices[0][1])

    def test_due_monitors_are_checked(self):
        artifact = nova_artifacts.new_artifact("Domains", "ueberwachung")
        nova_artifacts.schedule(artifact, 6, datetime(2026, 11, 3, 0))
        self.nova.artifacts.save(artifact)
        with unittest.mock.patch.object(self.nova.watcher, "check") as check:
            self.nova.watcher.tick(datetime(2026, 11, 3, 5))
            check.assert_not_called()
            self.nova.watcher.tick(datetime(2026, 11, 3, 7))
            check.assert_called_once_with(artifact["id"])
            artifact["state"] = "pausiert"
            self.nova.watcher.tick(datetime(2026, 11, 3, 8))
            check.assert_called_once()

    def test_findings_reach_history_and_notice(self):
        artifact = nova_artifacts.new_artifact("Domains", "ueberwachung")
        self.nova.artifacts.save(artifact)
        self.nova.watcher.running.add(artifact["id"])
        self.nova.watcher._done(artifact["id"], {"findings": ["DNS-Eintrag von example.de geändert"], "snapshot": {"a": 1},
                                                 "failed": ""})
        saved = self.nova.artifacts.get(artifact["id"])
        self.assertEqual(saved["unseen"], 1)
        self.assertEqual(saved["snapshot"], {"a": 1})
        self.assertTrue(saved["last_check"])
        self.assertEqual(self.notices[0][1], "DNS-Eintrag von example.de geändert")
        self.assertNotIn(artifact["id"], self.nova.watcher.running)

    def test_same_failure_is_not_repeated(self):
        artifact = nova_artifacts.new_artifact("Domains", "ueberwachung")
        for _ in range(3):
            nova_artifacts.record_check(artifact, [], failed="OVH ist nicht verbunden.")
        self.assertEqual(sum("nicht verbunden" in entry["text"] for entry in artifact["history"]), 1)

    def test_tool_monitor_waits_for_approval(self):
        tool = nova_artifacts.new_artifact("Prüfer", "werkzeug", "print(1)")
        tool["read_only"] = True
        monitor = nova_artifacts.new_artifact("Wache", "ueberwachung")
        monitor["tool"] = tool["id"]
        for item in (tool, monitor):
            self.nova.artifacts.save(item)
        with unittest.mock.patch("core.nova_tools.subprocess.run") as started:
            result = nova_watch.run_check(self.nova, monitor)
        started.assert_not_called()
        self.assertIn("Freigabe", result["failed"])
        done = subprocess.CompletedProcess([], 0, b"Stand 1\n", b"")
        with unittest.mock.patch("core.nova_tools.subprocess.run", return_value=done):
            first = nova_watch.run_check(self.nova, monitor, confirmed=True)  # der Host hat Ja gesagt
            monitor["snapshot"] = first["snapshot"]
            self.assertTrue(self.nova.toolbox.preapproved("artifact_run_tool", {"id": tool["id"]}))
            self.assertEqual(nova_watch.run_check(self.nova, monitor)["findings"], [])  # gleiches Ergebnis: nichts Neues

    def test_ovh_check_runs_only_in_the_dev_build(self):
        monitor = nova_artifacts.new_artifact("OVH", "ueberwachung")
        monitor["check"] = "ovh_domains"
        with unittest.mock.patch("core.nova_tools.is_dev", return_value=False):
            self.assertIn("Entwickler-Build", nova_watch.run_check(self.nova, monitor)["failed"])


class OvhDomains(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ovh = ovh_module()
        self.api = FakeOvh()
        self.ovh.ensure_connection(self.ctx.config)
        for field in self.ovh.FIELDS:
            self.ctx.config.keys[f"conn:ovh:{field}"] = f"{field}-geheim-wert"
        self.client = unittest.mock.patch.object(self.ovh, "client", return_value=self.api)
        self.client.start()
        self.reach = unittest.mock.patch.object(self.ovh, "probe", return_value=True)
        self.reach.start()

    def tearDown(self):
        self.client.stop()
        self.reach.stop()

    def test_token_page_asks_for_the_needed_rights(self):
        page = self.ovh.token_page("ovh-eu")
        self.assertTrue(page.startswith("https://eu.api.ovh.com/createToken/"))
        for right in ("GET=/domain/*", "POST=/domain/zone/*", "PUT=/domain/zone/*"):
            self.assertIn(right, page)

    def test_keys_live_only_in_the_credential_store(self):
        self.assertEqual(self.ovh.credentials(self.ctx.config)[0], "application_key-geheim-wert")
        settings = self.ctx.config.path.read_text(encoding="utf-8")
        self.assertNotIn("geheim-wert", settings)
        self.assertIn("ovh", self.ctx.config.get("nova.connections"))

    def test_snapshot_and_first_check_only_set_the_baseline(self):
        data = self.ovh.snapshot(self.api, reach=lambda _domain: True)
        self.assertEqual(data["example.de"]["nameservers"], ["dns1.ovh.net"])
        self.assertIn("www A 1.2.3.4 (TTL 3600)", data["example.de"]["records"])
        findings, _warned = self.ovh.changes(None, data, date(2026, 10, 1), [])
        self.assertEqual(findings, [])

    def test_changes_are_reported(self):
        old = self.ovh.snapshot(self.api, reach=lambda _domain: True)
        self.api.records[1]["target"] = "5.6.7.8"
        new = self.ovh.snapshot(self.api, reach=lambda _domain: False)
        findings, _warned = self.ovh.changes(old, new, date(2026, 10, 1), [])
        self.assertIn("DNS-Eintrag von example.de geändert: www A 1.2.3.4 (TTL 3600) → www A 5.6.7.8 (TTL 3600)", findings)
        self.assertIn("example.de ist nicht mehr erreichbar", findings)

    def test_expiry_warns_once_per_step(self):
        data = self.ovh.snapshot(self.api, reach=lambda _domain: True)
        findings, warned = self.ovh.changes(data, data, date(2026, 11, 3), [])
        self.assertEqual(findings, ["Domain example.de läuft in 14 Tagen ab"])
        again, warned = self.ovh.changes(data, data, date(2026, 11, 4), warned)
        self.assertEqual(again, [])
        later, _warned = self.ovh.changes(data, data, date(2026, 11, 11), warned)
        self.assertEqual(later, ["Domain example.de läuft in 6 Tagen ab"])

    def test_change_shows_old_and_new_and_waits_for_yes(self):
        box = self.ctx.nova.toolbox
        turn = NovaTurn(self.ctx.nova, "test", [])
        shown = []
        turn.approval.connect(lambda number, title, detail, dangerous, for_chat: (shown.append((detail, dangerous)),
                                                                                  turn.resolve(number, True, False)),
                              Qt.ConnectionType.DirectConnection)
        monitor = nova_artifacts.new_artifact("OVH", "ueberwachung")
        self.ctx.nova.artifacts.save(monitor)
        call = unittest.mock.Mock(args={"zone": "example.de", "subdomain": "www", "field_type": "A", "target": "5.6.7.8",
                                        "artifact": monitor["id"]}, id="1")
        call.name = "ovh_dns_change"
        parts = turn._use_tool(call)
        detail, dangerous = shown[0]
        self.assertIn("alt:\n  www A 1.2.3.4 (TTL 3600)", detail)
        self.assertIn("neu:\n  www A 5.6.7.8 (TTL 3600)", detail)
        self.assertTrue(dangerous)
        self.assertIn(("PUT", "/domain/zone/example.de/record/1", {"subDomain": "www", "target": "5.6.7.8", "ttl": 3600}), self.api.calls)
        self.assertEqual(self.api.calls[-1][:2], ("POST", "/domain/zone/example.de/refresh"))
        self.assertIn("Zone neu geladen", parts[0].function_response.response["result"])
        self.assertIn("1.2.3.4", self.ctx.nova.artifacts.get(monitor["id"])["history"][-1]["text"])
        self.assertFalse(box._plans)

    def test_refused_change_changes_nothing(self):
        turn = NovaTurn(self.ctx.nova, "test", [])
        turn.approval.connect(lambda number, *_rest: turn.resolve(number, False, False), Qt.ConnectionType.DirectConnection)
        call = unittest.mock.Mock(args={"zone": "example.de", "subdomain": "www", "field_type": "A", "target": "9.9.9.9"}, id="1")
        call.name = "ovh_dns_change"
        turn._use_tool(call)
        self.assertFalse([c for c in self.api.calls if c[0] != "GET"])

    def test_change_without_shown_plan_is_refused(self):
        result = self.ctx.nova.toolbox.run("ovh_dns_change", {"zone": "example.de", "field_type": "A", "subdomain": "www",
                                                              "target": "5.6.7.8"})
        self.assertFalse(result["ok"])
        self.assertFalse([c for c in self.api.calls if c[0] != "GET"])

    def test_add_and_delete_plans(self):
        added = self.ovh.plan_change(self.api, "example.de", "@", "TXT", "v=spf1 -all", action="add")
        self.assertEqual(added["old"], [])
        self.ovh.apply_plan(self.api, added)
        self.assertIn(("POST", "/domain/zone/example.de/record", {"fieldType": "TXT", "subDomain": "", "target": "v=spf1 -all", "ttl": 0}),
                      self.api.calls)
        removed = self.ovh.plan_change(self.api, "example.de", "www", "A", action="delete")
        self.assertIn("(gelöscht)", self.ovh.describe_plan(removed))
        with self.assertRaises(ValueError):
            self.ovh.plan_change(self.api, "example.de", "www", "XYZ", "1")

    def test_overview_never_shows_keys(self):
        with unittest.mock.patch.object(self.ovh, "overview", return_value="Fehler mit application_secret-geheim-wert"):
            text = self.ctx.nova.toolbox.run("ovh_overview", {})["result"]
        self.assertNotIn("geheim-wert", text)

    def test_ovh_tools_only_in_the_dev_build(self):
        with unittest.mock.patch("core.nova_tools.is_dev", return_value=False):
            names = {tool["name"] for tool in nova_tools.declarations({})[0]["function_declarations"]}
        self.assertNotIn("ovh_dns_change", names)
        self.assertIn("artifact_create", names)
        self.assertIn("connection_request", names)  # eigene Anbindungen bauen kann jeder

    def test_dev_build_brings_the_example_once(self):
        import importlib
        seed = importlib.import_module("devtools.nova_seed")
        made = seed.seed(self.ctx.nova)
        titles = sorted(item["title"] for item in self.ctx.nova.artifacts.all())
        self.assertEqual(titles, ["OVH einrichten", "OVH-Domains überwachen"])
        monitor = next(item for item in self.ctx.nova.artifacts.all() if item["check"] == "ovh_domains")
        self.assertEqual(monitor["schedule_hours"], 6)
        self.assertEqual(len(made), 2)
        self.ctx.nova.artifacts.delete(monitor["id"])
        self.assertEqual(seed.seed(self.ctx.nova), [])  # gelöscht bleibt gelöscht


class NovaConnections(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.connections = self.ctx.nova.connections

    def test_names_and_fields_are_checked(self):
        with self.assertRaises(ValueError):
            self.connections.define("../böse", ["key"])
        with self.assertRaises(ValueError):
            self.connections.define("wetter", [])
        with self.assertRaises(ValueError):
            self.connections.define("wetter", ["key"], "http://unsicher.example")

    def test_nova_requests_fields_and_never_sees_values(self):
        asked = []
        self.ctx.nova.connection_wanted.connect(asked.append)
        result = self.ctx.nova.toolbox.run("connection_request", {"name": "wetter", "fields": "api_key\nstadt",
                                                                  "help_url": "https://wetter.example/keys"})
        self.assertTrue(result["ok"])
        self.assertEqual(asked, ["wetter"])
        form = ConnectionForm(self.connections, "wetter")
        form.fields["api_key"].setText("abc-geheim-123")
        form._save()
        self.assertEqual(form.fields["api_key"].text(), "")  # nach dem Speichern ist das Feld leer
        self.assertEqual(self.ctx.config.keys["conn:wetter:api_key"], "abc-geheim-123")
        self.assertFalse(self.connections.ready("wetter"))
        described = self.ctx.nova.toolbox.run("connection_list", {})["result"]
        self.assertIn("api_key gesetzt", described)
        self.assertIn("stadt fehlt", described)
        self.assertNotIn("abc-geheim-123", described + self.ctx.config.path.read_text(encoding="utf-8"))

    def test_runner_gives_only_the_own_connection(self):
        from core import nova_tool_runner
        stored = {"conn:wetter:api_key": "w-123", "conn:bank:pin": "9999"}
        with unittest.mock.patch("keyring.get_password", side_effect=lambda _service, user: stored.get(user)):
            helpers = nova_tool_runner.helpers("wetter")
            self.assertEqual(helpers["secret"]("api_key"), "w-123")
            with self.assertRaises(KeyError):
                helpers["secret"]("pin")
        self.assertEqual(nova_tool_runner.helpers(""), {})
        self.assertIn("ovh_client", nova_tool_runner.helpers("ovh"))


class NovaVoice(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.nova = self.ctx.nova
        self.nova.api_key = lambda: "test"
        self.nova.toolbox.on_main = lambda function: function()

    def tearDown(self):
        self.ctx.nova_panel.hide()

    def test_nova_changes_her_own_voice(self):
        result = self.nova.toolbox.run("set_voice", {"voice": "kore", "style": "ruhig und etwas langsamer", "speak": True})
        self.assertTrue(result["ok"], result)
        settings = self.ctx.config.get("nova")
        self.assertEqual((settings["voice"], settings["voice_style"], settings["speak"]), ("Kore", "ruhig und etwas langsamer", True))
        self.assertFalse(self.nova.toolbox.run("set_voice", {"voice": "Darth"})["ok"])
        self.assertIn("Kore", self.nova.toolbox.run("voices_list", {})["result"])
        self.nova.toolbox.run("set_voice", {"style": "-"})
        self.assertEqual(self.ctx.config.get("nova.voice_style"), "")

    def test_with_voice_nova_switches_to_gemini_live(self):
        with unittest.mock.patch.object(nova_voice.LiveTalk, "start") as live, \
                unittest.mock.patch.object(NovaTurn, "start") as normal, \
                unittest.mock.patch("core.nova_controller.LiveSpeech.start"):  # kein echter Ton, kein Netz
            self.ctx.config.set("nova.speak", False)
            self.nova.send("Hallo")
            self.assertEqual((live.call_count, normal.call_count), (0, 1))
            self.nova.turn = None
            self.ctx.config.set("nova.speak", True)
            self.nova.send("Hallo nochmal")
            self.assertEqual(live.call_count, 1)
            self.assertIsInstance(self.nova.turn, nova_voice.LiveTalk)
            self.assertEqual(self.nova.turn.typed, "Hallo nochmal")
            self.nova.turn = None
            self.ctx.config.set("nova.speak_engine", "tts")
            self.nova.send("Und jetzt?")
            self.assertEqual(normal.call_count, 2)
            self.nova.turn = None

    def test_live_context_keeps_only_text(self):
        from google.genai import types
        contents = [types.Content(role="user", parts=[types.Part(text="Hi")]),
                    types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="x", args={}))]),
                    types.Content(role="model", parts=[types.Part(text="Hallo!")])]
        kept = nova_voice.text_context(contents)
        self.assertEqual([content.parts[0].text for content in kept], ["Hi", "Hallo!"])

    def test_spoken_turn_lands_in_the_chat(self):
        talk = nova_voice.LiveTalk(self.nova, "test", self.nova._history())
        talk.heard_text, talk.answer = "Wie spät ist es?", "Kurz nach neun."
        self.nova.turn = talk
        with unittest.mock.patch.object(self.nova, "sender", return_value=talk):
            self.nova._finish()
        self.assertEqual([m["text"] for m in self.nova.log.messages], ["Wie spät ist es?", "Kurz nach neun."])

    def test_gate_stops_after_a_pause(self):
        gate = SpeechGate(1.0)
        for moment in (0.0, 0.1, 0.2):
            gate.feed(100, moment)  # Stille zum Einmessen
        gate.feed(3000, 0.5)
        gate.feed(3000, 1.0)
        self.assertFalse(gate.finished(1.5))
        gate.feed(120, 1.6)
        self.assertTrue(gate.finished(2.1))

    def test_gate_gives_up_without_speech(self):
        gate = SpeechGate(1.0)
        gate.feed(100, 0.0)
        self.assertFalse(gate.finished(5.0))
        self.assertTrue(gate.finished(9.0))

    def test_silence_is_never_sent(self):
        voice = VoiceInput(self.ctx, lambda: "test", lambda: {"auto_stop": True, "silence": 1.0, "live": True})
        failed = []
        voice.failed.connect(failed.append)
        voice.gate = SpeechGate(1.0)
        voice._stream = unittest.mock.Mock()
        voice._frames = [b"\0\0" * 1600]
        with unittest.mock.patch("ui.search_actions.TranscribeWorker") as worker:
            voice.stop()
        worker.assert_not_called()
        self.assertEqual(failed, ["Ich habe nichts gehört."])

    def test_dictation_shows_live_text(self):
        panel = self.ctx.nova_panel
        panel._on_partial("Erinnere mich an")
        self.assertEqual(panel.composer.toPlainText(), "Erinnere mich an")

    def test_heard_text_grows_in_one_bubble(self):
        panel = self.ctx.nova_panel
        panel._rebuild()
        panel._on_heard("Wie ")
        panel._on_heard("geht's?")
        self.assertEqual(panel.heard.toPlainText(), "Wie geht's?")
        panel._on_busy(False)
        self.assertIsNone(panel.heard)


class DevSearch(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()

    def test_dev_words_only_in_the_dev_build(self):
        from core import dev
        with unittest.mock.patch("core.dev.is_dev", return_value=True):
            for word in ("dev", "Dev-Menü", "release", "veröffentlichen", "PIN"):
                self.assertTrue(dev.dev_menu_wanted(word), word)
            self.assertFalse(dev.dev_menu_wanted("wlan"))
        with unittest.mock.patch("core.dev.is_dev", return_value=False):
            self.assertFalse(dev.dev_menu_wanted("dev"))

    def test_settings_search_offers_the_menu_after_the_pin(self):
        window = self.ctx.settings
        with unittest.mock.patch("core.dev.is_dev", return_value=True):
            hits = window._matches("dev")
        self.assertEqual(hits[0][0], "Dev-Menü öffnen")
        self.assertTrue(any("Symbolleiste über der Taskbar" in label for label, *_rest in hits))
        item = QListWidgetItem(hits[0][0])
        item.setData(Qt.ItemDataRole.UserRole, hits[0][1:])
        with unittest.mock.patch("ui.settings_window.open_dev_menu") as opened:
            window._open_hit(item)
        opened.assert_called_once_with(self.ctx)  # die PIN fragt open_menu selbst

    def test_settings_search_never_offers_it_to_others(self):
        with unittest.mock.patch("core.dev.is_dev", return_value=False):
            labels = [label for label, *_rest in self.ctx.settings._matches("dev")]
        self.assertNotIn("Dev-Menü öffnen", labels)

    def test_launcher_and_home_search(self):
        with unittest.mock.patch("core.dev.is_dev", return_value=True):
            found = typed_actions(self.ctx, "veröffentlichen")
            self.assertEqual(found[0].label, "Dev-Menü")
            self.assertTrue(found[0].glyph)
            self.ctx.launcher.input.setText("dev")
            self.assertEqual(self.ctx.launcher.results.item(0).text(), "Dev-Menü")
            self.assertFalse(self.ctx.launcher.results.item(0).icon().isNull())
        with unittest.mock.patch("core.dev.is_dev", return_value=False):
            self.assertNotIn("Dev-Menü", [entry.label for entry in typed_actions(self.ctx, "dev")])
            self.ctx.launcher.input.setText("release")
            self.assertNotIn("Dev-Menü", [self.ctx.launcher.results.item(i).text() for i in range(self.ctx.launcher.results.count())])
        self.ctx.launcher.input.clear()

    def test_menu_is_loaded_by_name_only(self):
        source = (ROOT_PATH / "core" / "dev.py").read_text(encoding="utf-8")
        self.assertIn('importlib.import_module("devtools.menu")', source)
        for path in (ROOT_PATH / "ui").glob("*.py"):
            self.assertNotIn("import devtools", path.read_text(encoding="utf-8"), path.name)


class NotesResize(unittest.TestCase):
    def test_dragging_bigger_does_not_run_away(self):
        ctx = ResetContext()
        spec = {"id": "n", "type": "notes", "data": {"text": "Hallo"}, "options": {}}
        ctx.config.data["widgets"].append(spec)
        window = NotesWidget(ctx, spec)
        ctx.widgets.windows["n"] = window
        window.preview_size(320, 420)
        self.assertEqual((window.width(), window.height()), (320, 420))
        window.set_option("width", 320)
        window.set_option("height", 420)
        for _ in range(3):
            window.apply_options()
        self.assertEqual(window.height(), 420)  # wächst nicht von selbst weiter
        window.preview_size(320, 200)
        self.assertEqual(window.height(), 200)  # und lässt sich wieder kleiner ziehen
        self.assertEqual(window.edit.maximumHeight(), 16777215)  # das Textfeld füllt das Fenster, statt es festzuhalten


class ClaudeKnopf(unittest.TestCase):
    STORE = r"C:\Program Files\WindowsApps\Claude_4.4212.0.0_x64__pzs8sxrjxfjjc\app\Claude.exe"
    CODE = r"C:\Users\x\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\claude-code\2.1\abc\claude.exe"

    def window(self, hwnd, exe, aumid=""):
        return win_api.WindowInfo(hwnd, "Claude", exe, 1, aumid=aumid)

    def test_only_the_app_window_counts(self):
        from ui.claude_button import is_claude_window
        self.assertTrue(is_claude_window(self.window(1, self.STORE)))
        self.assertTrue(is_claude_window(self.window(2, "", "Claude_pzs8sxrjxfjjc!Claude")))
        self.assertFalse(is_claude_window(self.window(3, self.CODE)))  # Claude Code im Terminal bleibt ein normaler Task
        self.assertFalse(is_claude_window(self.window(4, r"C:\Windows\explorer.exe")))

    def test_click_opens_raises_or_puts_away(self):
        from ui import claude_button
        ctx = ResetContext()
        app_window = self.window(10, self.STORE)
        with unittest.mock.patch.object(win_api, "shell_open") as opened, \
                unittest.mock.patch.object(win_api, "activate_window") as raised, \
                unittest.mock.patch.object(win_api, "minimize_window") as put_away:
            ctx.windows = lambda: []
            self.assertEqual(claude_button.press(ctx), "geöffnet")
            self.assertTrue(opened.call_args.args[0].lower().startswith("shell:appsfolder\\claude_"))
            ctx.windows = lambda: [app_window]
            with unittest.mock.patch.object(win_api, "foreground_window", return_value=99):
                self.assertEqual(claude_button.press(ctx), "nach vorn")
            raised.assert_called_once_with(10)
            with unittest.mock.patch.object(win_api, "foreground_window", return_value=10), \
                    unittest.mock.patch.object(win_api, "is_minimized", return_value=False):
                self.assertEqual(claude_button.press(ctx), "abgelegt")
            put_away.assert_called_once_with(10)
            with unittest.mock.patch.object(win_api, "foreground_window", return_value=10), \
                    unittest.mock.patch.object(win_api, "is_minimized", return_value=True):
                self.assertEqual(claude_button.press(ctx), "nach vorn")
            self.assertEqual(raised.call_count, 2)

    def test_dev_build_has_the_button_left_and_no_claude_task(self):
        ctx = ResetContext()
        app_window, other = self.window(10, self.STORE), self.window(11, r"C:\Windows\notepad.exe")
        ctx.windows = lambda: [app_window, other]
        with unittest.mock.patch("ui.taskbar.is_dev", return_value=True):
            dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        row = dock.root.layout()
        self.assertIs(row.itemAt(0).widget(), dock.claude)
        self.assertEqual(dock._windows_here(), [other])
        ctx.config.set_monitor(None, "dev_claude_button", False)
        self.assertEqual(dock._windows_here(), [app_window, other])
        self.assertFalse(dock.claude.isVisibleTo(dock))
        dock.hide()

    def test_never_for_others(self):
        ctx = ResetContext()
        with unittest.mock.patch("ui.taskbar.is_dev", return_value=False):
            dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        self.assertIsNone(dock.claude)
        dock.hide()

    def test_icons_stay_centered_with_the_button(self):
        """Steht rechts nichts (alles in der Symbolleiste darüber), gleicht ein Platz rechts den Claude-Knopf aus."""
        ctx = ResetContext()
        ctx.windows = lambda: []
        with unittest.mock.patch("ui.taskbar.is_dev", return_value=True):
            dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
        for widget in (dock.media, dock.nova, dock.trash, dock.desktop):
            widget.hide()
        dock.claude.show()
        dock.claude.setFixedWidth(40)
        dock._align_icons()
        spacing = dock.root.layout().spacing()
        self.assertEqual((dock.pad.minimumWidth(), dock.pad_end.minimumWidth()), (0, 40 + spacing))
        dock.nova.show()
        dock.nova.setFixedWidth(100)
        dock._align_icons()
        self.assertEqual((dock.pad.minimumWidth(), dock.pad_end.minimumWidth()), (100 - 40, 0))  # rechts mehr: Platz links

    def test_claude_button_context_menu_toggle(self):
        # [Antigravity: Claude-Knopf per Kontextmen umschaltbar wie andere Buttons]
        ctx = ResetContext()
        with unittest.mock.patch("ui.taskbar.is_dev", return_value=True):
            dock = Dock(ctx, QGuiApplication.primaryScreen(), "bottom")
            self.addCleanup(dock.hide)
            self.assertTrue(dock.claude.isVisibleTo(dock))
            menu = themed_menu(dock.ctx, dock, dock.theme_name)
            dock.add_bar_options(menu)
            claude_actions = [a for a in menu.actions() if a.text() == "Claude-Knopf"]
            self.assertEqual(len(claude_actions), 1)
            action = claude_actions[0]
            self.assertTrue(action.isCheckable())
            self.assertTrue(action.isChecked())
            action.trigger()  # toggles to False
            self.assertFalse(dock.option("dev_claude_button"))
            self.assertFalse(dock.claude.isVisibleTo(dock))
        dock.hide()


@unittest.skipUnless((ROOT_PATH / "PopWin starten.bat").exists(), "das Startskript gehört nur zum Dev-Build")
class StartScript(unittest.TestCase):
    def test_bat_repairs_a_foreign_environment(self):
        script = (ROOT_PATH / "PopWin starten.bat").read_text(encoding="utf-8")
        self.assertIn('-c "import sys"', script)  # eine mitkopierte .venv eines anderen PCs wird erkannt
        self.assertIn("requirements.installed", script)  # neue Pakete kommen von selbst dazu
        self.assertNotIn("copilot_pro", script)


# --- [Antigravity Start] Tests für Taskbar-Selbstreparatur, Fokus & Drag-Ursprung (Rest: Claude Code) ---
class TaskbarRepair(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.config.set("animations.enabled", False)
        self.ctx.config.set("taskbar.pinned", [
            {"name": "A", "target": r"C:\a.exe", "exe": "a.exe"},
            {"name": "B", "target": r"C:\b.exe", "exe": "b.exe"},
            {"name": "C", "target": r"C:\c.exe", "exe": "c.exe"},
        ], notify=False)
        self.ctx.windows = lambda: []
        self.dock = Dock(self.ctx, QGuiApplication.primaryScreen(), "bottom")
        self.dock.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.dock.show()
        self.dock.refresh(force=True)
        self.addCleanup(self.dock.hide)

    def test_minimized_foreground_window_is_activated_instead_of_minimized_again(self):
        button = self.dock._buttons["a.exe"]
        button.windows = [win_api.WindowInfo(42, "App A", r"C:\a.exe", 1)]
        with unittest.mock.patch.object(win_api, "is_window", return_value=True), \
                unittest.mock.patch.object(win_api, "foreground_window", return_value=42), \
                unittest.mock.patch.object(win_api, "is_minimized", return_value=True), \
                unittest.mock.patch.object(win_api, "activate_window") as activate, \
                unittest.mock.patch.object(win_api, "minimize_window") as minimize:
            button._activate()
        activate.assert_called_once_with(42)
        minimize.assert_not_called()

    def test_repair_and_context_menu_unstick_taskbar_and_offer_reload(self):
        button = self.dock._buttons["a.exe"]
        button._dragging = True
        button._press = QPoint(5, 5)
        button.setDown(True)
        self.dock._dragged = button
        self.dock._dropping = button
        self.dock._order_held = True
        self.dock.repair(rebuild=False)
        self.assertFalse(button._dragging)
        self.assertIsNone(button._press)
        self.assertFalse(button.isDown())
        self.assertIsNone(self.dock._dragged)
        self.assertIsNone(self.dock._dropping)
        self.assertFalse(self.dock._order_held)

        menu = QMenu()
        self.dock.add_bar_options(menu)
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertIn("Taskbar neu laden", labels)

    def test_dragging_first_icon_keeps_slot_origin_stable(self):
        first = self.dock._buttons["a.exe"]
        origin_before = self.dock._slots(self.dock._shown_keys())["a.exe"]
        start_x = first.mapToGlobal(QPoint(5, 5)).x()
        with unittest.mock.patch("ui.taskbar.QApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton):
            self.dock.drag_task(first, start_x + 25, start_x)
            self.dock.drag_task(first, start_x + 45, start_x)
            slots_during = self.dock._slots(self.dock._shown_keys())
        self.assertEqual(min(slots_during.values()), origin_before)
        self.dock.finish_drag()
        self.assertFalse(first._dragging)


# --- [Antigravity Ende] ---


# --- [Antigravity Start: Sidebar Support Tests] ---
class SidebarSupport(unittest.TestCase):
    def setUp(self):
        self.ctx = ResetContext()
        self.ctx.config.set("animations.enabled", False)
        self.ctx.config.set("taskbar.pinned", [
            {"name": "A", "target": r"C:\a.exe", "exe": "a.exe"},
            {"name": "B", "target": r"C:\b.exe", "exe": "b.exe"},
            {"name": "C", "target": r"C:\c.exe", "exe": "c.exe"},
        ], notify=False)
        self.ctx.windows = lambda: []
        self.screen = QGuiApplication.primaryScreen()

    def test_sidebar_logic_and_release_availability(self):
        # [Antigravity: Sidebar ist nun regulärer Bestandteil des Release-Builds]
        from ui import sidebar
        self.assertTrue(sidebar.sidebar_active())
        positions = sidebar.taskbar_positions()
        self.assertIn("left", positions)
        self.assertIn("right", positions)
        self.assertEqual(sidebar.effective_edge("left"), "left")
        self.assertEqual(sidebar.effective_edge("right"), "right")

        with unittest.mock.patch.object(sidebar, "SIDEBAR_ENABLED", False):
            self.assertFalse(sidebar.sidebar_active())
            self.assertEqual(sidebar.effective_edge("left"), "bottom")
            self.assertEqual(sidebar.effective_edge("right"), "bottom")
            disabled_positions = sidebar.taskbar_positions()
            self.assertNotIn("left", disabled_positions)
            self.assertNotIn("right", disabled_positions)

    def test_sidebar_vertical_dock_and_hot_zone(self):
        from ui.base import in_hot_zone
        from PySide6.QtWidgets import QBoxLayout
        area = (0, 0, 1920, 1080)
        self.assertTrue(in_hot_zone(2, 500, area, "left", 6))
        self.assertFalse(in_hot_zone(50, 500, area, "left", 6))
        self.assertTrue(in_hot_zone(1918, 500, area, "right", 6))
        self.assertFalse(in_hot_zone(1800, 500, area, "right", 6))

        dock = Dock(self.ctx, self.screen, "left")
        dock.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        dock.show()
        self.addCleanup(dock.hide)
        dock.refresh(force=True)
        self.assertEqual(dock.edge, "left")
        self.assertEqual(dock.root.layout().direction(), QBoxLayout.Direction.TopToBottom)
        self.assertEqual(dock.apps.direction(), QBoxLayout.Direction.TopToBottom)
        slots = dock._slots(dock._shown_keys())
        self.assertLess(slots["a.exe"], slots["b.exe"])
# --- [Antigravity Ende: Sidebar Support] ---


# --- [Antigravity Start: App-Suchranking & Claude-Knopf Tests (Rest: Claude Code)] ---
class AppSearchRanking(unittest.TestCase):
    def setUp(self):
        self.index = AppIndex()
        self.pool = [
            App("Minecraft", "C:\\Games\\Minecraft.exe"),
            App("Minecraft Launcher", "C:\\Games\\MinecraftLauncher.exe"),
            App("Minecraft Bedrock Edition", "shell:AppsFolder\\MinecraftBedrock"),
            App("Badlion Minecraft Client", "C:\\Games\\Badlion.exe"),
            App("Calculator", "calc.exe"),
        ]

    def test_usage_frequency_ranks_higher_than_exact_match(self):
        # Häufiger genutzte App mit Suchbegriff im Namen ("Minecraft Launcher") überholt exakten Treffer ("Minecraft")
        usage = {
            "minecraft launcher": 10,
            "minecraft": 2,
        }
        results = self.index.search("minecraft", 4, pool=self.pool, usage=usage)
        self.assertEqual(results[0].name, "Minecraft Launcher")
        self.assertEqual(results[1].name, "Minecraft")

    def test_exact_match_wins_when_usage_is_higher_or_equal(self):
        # Wenn "Minecraft" häufiger oder gleich oft genutzt wird, bleibt der exakte Treffer vorne
        usage_higher = {"minecraft": 10, "minecraft launcher": 2}
        results_higher = self.index.search("minecraft", 4, pool=self.pool, usage=usage_higher)
        self.assertEqual(results_higher[0].name, "Minecraft")
        self.assertEqual(results_higher[1].name, "Minecraft Launcher")

        usage_equal = {"minecraft": 5, "minecraft launcher": 5}
        results_equal = self.index.search("minecraft", 4, pool=self.pool, usage=usage_equal)
        self.assertEqual(results_equal[0].name, "Minecraft")
        self.assertEqual(results_equal[1].name, "Minecraft Launcher")

    def test_default_sorting_when_no_usage(self):
        # Ohne Nutzung: Exakter Treffer > Prefix > Kürzere Länge > Wort-Treffer
        results = self.index.search("minecraft", 4, pool=self.pool, usage={})
        self.assertEqual(results[0].name, "Minecraft")
        self.assertEqual(results[1].name, "Minecraft Launcher")
        self.assertEqual(results[2].name, "Minecraft Bedrock Edition")
        self.assertEqual(results[3].name, "Badlion Minecraft Client")

    def test_word_match_beats_exact_match_if_used_more(self):
        # App mit Suchwort im Namen ("Badlion Minecraft Client") überholt "Minecraft", wenn deutlich häufiger genutzt
        usage = {
            "badlion minecraft client": 15,
            "minecraft": 1,
        }
        results = self.index.search("minecraft", 4, pool=self.pool, usage=usage)
        self.assertEqual(results[0].name, "Badlion Minecraft Client")
        self.assertEqual(results[1].name, "Minecraft")

    def test_config_record_app_launch_and_get_usage(self):
        import tempfile
        from pathlib import Path
        from config.config_manager import ConfigManager
        with tempfile.TemporaryDirectory() as folder:
            cfg = ConfigManager(Path(folder) / "settings.json")
            self.assertEqual(cfg.get_app_usage(), {})
            cfg.record_app_launch("Minecraft Launcher", "C:\\Games\\MinecraftLauncher.exe")
            usage = cfg.get_app_usage()
            self.assertEqual(usage.get("minecraft launcher"), 1)
            self.assertEqual(usage.get("c:\\games\\minecraftlauncher.exe"), 1)
            cfg.record_app_launch("Minecraft Launcher", "C:\\Games\\MinecraftLauncher.exe")
            usage = cfg.get_app_usage()
            self.assertEqual(usage.get("minecraft launcher"), 2)

    def test_search_launcher_tab_navigation(self):
        # [Antigravity: TAB- und Shift+TAB-Navigation durch Suchergebnisse]
        from ui.search_launcher import SearchLauncher, Result
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent, Qt
        ctx = ResetContext()
        launcher = SearchLauncher(ctx)
        self.addCleanup(launcher.deleteLater)
        executed = []
        launcher._collect = lambda text: [
            Result("App 1", lambda: executed.append(1)),
            Result("App 2", lambda: executed.append(2)),
            Result("App 3", lambda: executed.append(3)),
        ]
        launcher._update_results("app")
        self.assertEqual(launcher.results.count(), 3)
        self.assertEqual(launcher.results.currentRow(), 0)

        # TAB -> advances to row 1
        tab_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
        launcher.eventFilter(launcher.input, tab_event)
        self.assertEqual(launcher.results.currentRow(), 1)

        # TAB -> advances to row 2
        launcher.eventFilter(launcher.input, tab_event)
        self.assertEqual(launcher.results.currentRow(), 2)

        # TAB -> wraps around to row 0
        launcher.eventFilter(launcher.input, tab_event)
        self.assertEqual(launcher.results.currentRow(), 0)

        # Shift+TAB (Backtab) -> wraps backwards to row 2
        backtab_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
        launcher.eventFilter(launcher.input, backtab_event)
        self.assertEqual(launcher.results.currentRow(), 2)

        # Accept executes currently selected item (row 2 -> App 3)
        launcher._accept()
        self.assertEqual(executed, [3])
# --- [Antigravity Ende: App-Suchranking & Claude-Knopf Tests] ---


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    release_contexts()  # sauber abbauen, bevor Python die Fenster in beliebiger Reihenfolge wegräumt
    sys.exit(0 if result.wasSuccessful() else 1)
