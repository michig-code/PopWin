"""PowerShell Studio: Die Idee der ISE mit dem, was man aus VS Code und Co. kennt: echter Debugger, Haltepunkte,
andockbare Bereiche, Befehlspalette, frei belegbare Tasten und eigene Farben."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QByteArray, QEvent, Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QAction, QKeySequence, QTextCursor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDockWidget, QInputDialog, QMainWindow, QMenu, QMenuBar, QMessageBox, QToolBar, QToolTip, QVBoxLayout,
)

from core.paths import DATA_DIR  # noqa: E402
from editor.widget import BreakMark, EditorPanel, language_of  # noqa: E402
from studio.debugger import (  # noqa: E402
    CONTINUE, QUIT, STEP_INTO, STEP_OUT, STEP_OVER, BreakPoint, DebugSession, SyntaxChecker, console_command, outline, run_command,
    write_plan,
)
from studio.formatter import FormatOptions, ScriptFormatter  # noqa: E402
from studio.panels import (  # noqa: E402
    BreakpointPanel, CommandList, CommandPalette, DebugBar, OutlinePanel, ProblemsPanel, StackPanel, VariablesPanel, WatchPanel,
)
from studio.settings import SettingsDialog, colors_of, load_settings, save_settings  # noqa: E402
from terminal.widget import TerminalWidget  # noqa: E402
from ui.app_shell import run_app  # noqa: E402
from ui.file_dialog import pick_files, pick_save  # noqa: E402
from ui.window_frame import NativeWindow  # noqa: E402

SCRIPT_TYPES = "PowerShell (*.ps1 *.psm1 *.psd1);;Alle Dateien (*)"
RUN_FILE = "studio_run.ps1"
COMMANDS_FILE = "studio_commands.json"
MARKS_FILE = "studio_breakpoints.json"
LIST_COMMANDS = "Get-Command -CommandType Cmdlet,Function | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"
CHECK_MS = 900
CHECK_LIMIT = 200_000  # sehr große Dateien nicht bei jedem Tippen prüfen
NOTE_MS = 3500


def run_line(path: Path) -> str:
    """Punkt-Aufruf wie in der ISE: Variablen und Funktionen des Skripts bleiben in der Konsole erhalten."""
    return ". '" + str(path).replace("'", "''") + "'"


def parse_commands(output: str) -> list[str]:
    try:
        names = json.loads(output)
    except ValueError:
        return []
    return sorted(name for name in names if isinstance(name, str)) if isinstance(names, list) else []


def same_file(first: str, second: str) -> bool:
    return bool(first and second) and os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def word_at(line: str, column: int) -> str:
    """Die Variable unter der Maus, ohne das Dollarzeichen; leer, wenn dort keine steht."""
    start = column
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_"):
        start -= 1
    end = column
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[start:end] if start > 0 and line[start - 1] == "$" else ""


class CommandLoader(QThread):
    """Die Liste aller Befehle dauert Sekunden; sie bleibt für den nächsten Start gespeichert."""
    loaded = Signal(list)

    def run(self) -> None:
        cache = DATA_DIR / COMMANDS_FILE
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                     "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + LIST_COMMANDS],
                                    capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return
        names = parse_commands(result.stdout.decode("utf-8", "replace"))
        if names:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(names), encoding="utf-8")
            self.loaded.emit(names)


@dataclass(frozen=True)
class StudioAction:
    key: str
    label: str
    shortcut: str
    menu: str
    method: str


ACTIONS = (
    StudioAction("new", "Neues Skript", "Ctrl+N", "Datei", "new_script"),
    StudioAction("open", "Öffnen", "Ctrl+O", "Datei", "open_dialog"),
    StudioAction("save", "Speichern", "Ctrl+S", "Datei", "save"),
    StudioAction("save_as", "Speichern unter", "Ctrl+Shift+S", "Datei", "save_as"),
    StudioAction("close_tab", "Tab schließen", "Ctrl+W", "Datei", "close_tab"),
    StudioAction("palette", "Befehlspalette", "Ctrl+Shift+P", "Ansicht", "show_palette"),
    StudioAction("snippets", "Schnipsel einfügen", "Ctrl+J", "Bearbeiten", "show_snippets"),
    StudioAction("format", "Dokument formatieren", "Shift+Alt+F", "Bearbeiten", "format_document"),
    StudioAction("format_selection", "Auswahl formatieren", "Ctrl+K, Ctrl+F", "Bearbeiten", "format_selection"),
    StudioAction("console", "Konsole ein- oder ausblenden", "Ctrl+R", "Ansicht", "toggle_console"),
    StudioAction("reset_layout", "Anordnung zurücksetzen", "", "Ansicht", "reset_layout"),
    StudioAction("customize", "Anpassen", "Ctrl+,", "Ansicht", "customize"),
    StudioAction("run", "Ausführen oder Fortsetzen", "F5", "Debuggen", "run_script"),
    StudioAction("selection", "Auswahl ausführen", "F8", "Debuggen", "run_selection"),
    StudioAction("stop", "Stoppen", "Shift+F5", "Debuggen", "stop"),
    StudioAction("step_over", "Prozedurschritt", "F10", "Debuggen", "step_over"),
    StudioAction("step_into", "Einzelschritt", "F11", "Debuggen", "step_into"),
    StudioAction("step_out", "Rücksprung", "Shift+F11", "Debuggen", "step_out"),
    StudioAction("toggle_break", "Haltepunkt setzen oder löschen", "F9", "Debuggen", "toggle_break"),
    StudioAction("edit_break", "Haltepunkt bearbeiten", "Ctrl+F9", "Debuggen", "edit_break_here"),
    StudioAction("clear_breaks", "Alle Haltepunkte löschen", "Ctrl+Shift+F9", "Debuggen", "clear_breaks"),
)


class StudioWindow(NativeWindow):
    def __init__(self, ctx, files: list[str] | None = None):
        super().__init__(ctx, "PowerShell Studio", "apps/studio")
        self.resize(1320, 840)
        self.settings = load_settings()
        self.host = QMainWindow()  # bringt das Andocken mit; das Fenster drumherum bleibt PopWins
        self.host.setWindowFlags(Qt.WindowType.Widget)
        self.host.setDockOptions(QMainWindow.DockOption.AllowTabbedDocks | QMainWindow.DockOption.AnimatedDocks
                                 | QMainWindow.DockOption.AllowNestedDocks)
        self.panel = EditorPanel(ctx, "studio_session.json")
        self.host.setCentralWidget(self.panel)
        self.console = TerminalWidget(console_command(self.settings["shell"]), self.settings["start_folder"] or os.path.expanduser("~"))
        self.console.text_font.setPointSize(self.settings["console_size"])
        self.console._measure()
        self.commands = CommandList()
        self.commands.insert.connect(self._insert_command)
        self.variables = VariablesPanel()
        self.watch = WatchPanel(self.settings["watches"])
        self.watch.changed.connect(self._watches_changed)
        self.stack = StackPanel()
        self.stack.jump.connect(self.go_to)
        self.breaks = BreakpointPanel()
        self.breaks.jump.connect(self.go_to)
        self.breaks.toggled.connect(self._enable_mark)
        self.breaks.edit.connect(self._edit_mark_at)
        self.breaks.remove.connect(self._remove_mark_at)
        self.problems = ProblemsPanel()
        self.problems.jump.connect(self.go_to)
        self.outline = OutlinePanel()
        self.outline.jump.connect(self.go_to)
        self.docks = {}
        self._build_docks()
        self.default_layout = self.host.saveState()
        if self.settings["layout"]:
            self.host.restoreState(QByteArray.fromBase64(self.settings["layout"].encode()))
        self.actions_by_key: dict[str, QAction] = {}
        self.toolbar = QToolBar("Werkzeuge")
        self.toolbar.setObjectName("StudioTools")
        self.toolbar.setMovable(True)
        self.host.addToolBar(self.toolbar)
        self._build_actions()
        self._build_menu()
        self._fill_toolbar()
        self.status_bar = DebugBar()
        self.titlebar.middle.addStretch(1)
        self.titlebar.middle.addWidget(self.status_bar)

        box = QVBoxLayout(self.content)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.host)

        self.debug = DebugSession(self)
        self.debug.paused.connect(self._on_paused)
        self.debug.idle.connect(self._on_idle)
        self.debug.resumed.connect(lambda: self.status_bar.show_text("Läuft"))
        self.stop_at: tuple[object, int] | None = None
        self.checker = SyntaxChecker(self.settings["shell"], self)
        self.checker.finished.connect(self._on_problems)
        self.formatter = ScriptFormatter(self.settings["shell"], self)
        self.formatter.finished.connect(self._formatted)
        self.formatter.failed.connect(self._format_failed)
        self.format_editor = None
        self.save_after_format = None
        self._note_timer = QTimer(self, singleShot=True, interval=NOTE_MS)
        self._note_timer.timeout.connect(self._clear_note)
        self._check_timer = QTimer(self, singleShot=True, interval=CHECK_MS)
        self._check_timer.timeout.connect(self._check_now)
        self.panel.bind_shortcuts(self)
        if self.settings["restore_session"]:
            self.panel.restore_session()
        for file in files or []:
            self.panel.open_file(file)
        if not self.panel.tabs:
            self.new_script()
        self._load_marks()
        self.panel.title_changed.connect(lambda _name: self._prepare_tabs())
        self._prepare_tabs()
        self._load_commands()
        self.apply_theme()
        QTimer.singleShot(0, self.console.start)

    # --- Aufbau ------------------------------------------------------------------
    def _dock(self, key: str, title: str, widget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self.host)
        dock.setObjectName(key)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.host.addDockWidget(area, dock)
        self.docks[key] = dock
        return dock

    def _build_docks(self) -> None:
        bottom, right = Qt.DockWidgetArea.BottomDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        console = self._dock("console", "Konsole", self.console, bottom)
        problems = self._dock("problems", "Probleme", self.problems, bottom)
        self.host.tabifyDockWidget(problems, console)
        console.raise_()
        variables = self._dock("variables", "Variablen", self.variables, right)
        watch = self._dock("watch", "Beobachten", self.watch, right)
        self.host.tabifyDockWidget(variables, watch)
        stack = self._dock("stack", "Aufrufliste", self.stack, right)
        breaks = self._dock("breakpoints", "Haltepunkte", self.breaks, right)
        self.host.tabifyDockWidget(stack, breaks)
        outline_dock = self._dock("outline", "Gliederung", self.outline, right)
        commands = self._dock("commands", "Befehle", self.commands, right)
        self.host.tabifyDockWidget(outline_dock, commands)
        variables.raise_()
        stack.raise_()
        outline_dock.raise_()
        self.host.resizeDocks([variables], [340], Qt.Orientation.Horizontal)
        self.host.resizeDocks([console], [260], Qt.Orientation.Vertical)

    def _build_actions(self) -> None:
        for spec in ACTIONS:
            action = QAction(spec.label, self)
            action.setShortcut(QKeySequence(self.settings["keys"].get(spec.key, spec.shortcut)))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(lambda _=False, m=spec.method: getattr(self, m)())
            self.addAction(action)
            self.actions_by_key[spec.key] = action

    def _build_menu(self) -> None:
        bar = QMenuBar()
        menus: dict[str, QMenu] = {}
        for spec in ACTIONS:
            menu = menus.get(spec.menu) or menus.setdefault(spec.menu, bar.addMenu(spec.menu))
            menu.addAction(self.actions_by_key[spec.key])
        panes = menus["Ansicht"].addMenu("Bereiche")
        for dock in self.docks.values():
            panes.addAction(dock.toggleViewAction())
        self.titlebar.middle.addWidget(bar)

    def _fill_toolbar(self) -> None:
        self.toolbar.clear()
        for key in self.settings["toolbar"]:
            if key in self.actions_by_key:
                self.toolbar.addAction(self.actions_by_key[key])

    def mouse_step(self, step: int) -> None:
        self.panel.step_tab(step)

    def reset_layout(self) -> None:
        self.host.restoreState(self.default_layout)

    def toggle_console(self) -> None:
        dock = self.docks["console"]
        dock.setVisible(not dock.isVisible())

    def close_tab(self) -> None:
        self.panel.close_tab(self.panel.bar.currentIndex())

    # --- Tabs vorbereiten --------------------------------------------------------
    def new_script(self) -> None:
        self.panel.new_tab()
        self.panel.editor().highlighter.set_language("PowerShell")
        if hasattr(self, "debug"):
            self._prepare_tabs()

    def _prepare_tabs(self) -> None:
        for tab in self.panel.tabs:
            editor = tab.editor
            if not getattr(editor, "studio_ready", False):
                editor.studio_ready = True
                editor.enable_marks()
                editor.gutter_clicked.connect(lambda line, e=editor: self._toggle_mark(e, line))
                editor.gutter_menu.connect(lambda line, point, e=editor: self._mark_menu(e, line, point))
                editor.textChanged.connect(self._text_changed)
                editor.viewport().installEventFilter(self)
                self._apply_settings(editor)
            if language_of(tab.path) == "Text":
                editor.highlighter.set_language("PowerShell")
            if self.settings["completion"] and editor.completer is None and self.commands.names:
                editor.use_completions(self.commands.names)
        self._refresh_marks()
        self._check_now()

    def _apply_settings(self, editor) -> None:
        settings = self.settings
        colors = colors_of(settings)
        editor.apply_look(settings["font"], settings["font_size"], colors, settings["tab_width"], settings["line_numbers"],
                          settings["line_highlight"])
        self.panel.wrap = settings["wrap"]
        self.panel._apply_wrap(editor)

    def customize(self) -> None:
        dialog = SettingsDialog(self, self.settings, list(ACTIONS))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.settings = dialog.result_settings()
        self._store_settings()
        for tab in self.panel.tabs:
            self._apply_settings(tab.editor)
        for spec in ACTIONS:
            self.actions_by_key[spec.key].setShortcut(QKeySequence(self.settings["keys"].get(spec.key, spec.shortcut)))
        self._fill_toolbar()
        self.console.text_font.setPointSize(self.settings["console_size"])
        self.console._measure()
        self.console.update()
        self._prepare_tabs()

    def _store_settings(self) -> None:
        self.settings["layout"] = bytes(self.host.saveState().toBase64()).decode()
        self.settings["watches"] = self.watch.watches
        save_settings(self.settings)

    # --- Befehle und Schnipsel ---------------------------------------------------
    def _load_commands(self) -> None:
        cache = DATA_DIR / COMMANDS_FILE
        if cache.exists():
            self._use_commands(parse_commands(cache.read_text(encoding="utf-8")))
        self.loader = CommandLoader(self)
        self.loader.loaded.connect(self._use_commands)
        self.loader.start()

    def _use_commands(self, names: list[str]) -> None:
        self.commands.set_names(names)
        self._prepare_tabs()

    def _insert_command(self, name: str) -> None:
        editor = self.panel.editor()
        if editor:
            editor.insertPlainText(name)
            editor.setFocus()

    def show_palette(self) -> None:
        entries = [(spec.label, self.actions_by_key[spec.key].shortcut().toString(), self.actions_by_key[spec.key].trigger)
                   for spec in ACTIONS]
        CommandPalette(self, entries).exec()

    def show_snippets(self) -> None:
        entries = [(name, "", lambda t=text: self._insert_command(t)) for name, text in self.settings["snippets"].items()]
        CommandPalette(self, entries, "Schnipsel suchen").exec()

    # --- Ausführen ---------------------------------------------------------------
    def _script_path(self, text: str) -> str:
        """Gespeicherte Skripte laufen unter ihrem echten Pfad, damit $PSScriptRoot und relative Pfade stimmen."""
        tab = self.panel.current()
        if tab.path and language_of(tab.path) == "PowerShell":
            if self.settings["save_before_run"] and tab.modified:
                self.panel.save(tab)
            if not tab.modified:
                return tab.path
        script = DATA_DIR / RUN_FILE
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8-sig")  # ohne BOM liest PowerShell 5 Umlaute falsch
        return str(script)

    def _focus_console(self) -> None:
        self.docks["console"].show()
        self.docks["console"].raise_()
        self.console.setFocus()

    def _run_file(self, path: str, editor=None) -> None:
        write_plan(self.all_marks(path, editor), self.watch.watches)
        self._focus_console()
        self.console.run_line(run_command(path, self.settings["script_arguments"]))
        self.debug.started()

    def run_script(self) -> None:
        if self.debug.is_paused:
            self._step(CONTINUE)
            return
        editor = self.panel.editor()
        if editor and editor.toPlainText().strip():
            self._run_file(self._script_path(editor.toPlainText()), editor)

    def run_selection(self) -> None:
        editor = self.panel.editor()
        if editor is None:
            return
        cursor = editor.textCursor()
        text = cursor.selectedText().replace(" ", "\n") if cursor.hasSelection() else cursor.block().text()
        if not text.strip():
            return
        script = DATA_DIR / RUN_FILE
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8-sig")
        self._focus_console()
        self.console.run_line(run_line(script))

    def stop(self) -> None:
        if self.debug.is_paused:
            self._step(QUIT)
        else:
            self.console.write("\x03")

    def _step(self, command: str) -> None:
        if self.debug.is_paused:
            self.debug.state = None
            self._clear_stop()
            self.status_bar.show_text("Läuft")
            self.console.run_line(command)

    def step_over(self) -> None:
        self._step(STEP_OVER)

    def step_into(self) -> None:
        self._step(STEP_INTO)

    def step_out(self) -> None:
        self._step(STEP_OUT)

    def open_dialog(self) -> None:
        for path in pick_files(self, "Skript öffnen", SCRIPT_TYPES):
            self.panel.open_file(path)
        self._prepare_tabs()

    def save(self) -> None:
        tab = self.panel.current()
        if tab and tab.path and self.settings["format_on_save"] and self._formats(tab):
            self.save_after_format = tab
            self.format_document()
        elif tab and tab.path:
            self.panel.save(tab)
        else:
            self.save_as()

    def save_as(self) -> None:
        path = pick_save(self, "Skript speichern", SCRIPT_TYPES)
        if path:
            self.panel.save(path=path)

    # --- Formatieren -------------------------------------------------------------
    @staticmethod
    def _formats(tab) -> bool:
        return language_of(tab.path) in ("PowerShell", "Text")

    def _format_options(self) -> FormatOptions:
        settings = self.settings
        return FormatOptions(settings["format_indent"], settings["format_tabs"], settings["format_spacing"],
                             settings["format_casing"], settings["format_blank_lines"])

    def _start_format(self, selection: tuple[int, int] | None = None) -> None:
        tab = self.panel.current()
        if tab is None or self.formatter.busy:
            return
        if not self._formats(tab):
            self._note("Formatieren gibt es nur für PowerShell")
            return
        self.format_editor = tab.editor
        self.formatter.start(tab.editor.toPlainText(), self._format_options(),
                             {name.lower(): name for name in self.commands.names}, selection)

    def format_document(self) -> None:
        self._start_format()

    def format_selection(self) -> None:
        editor = self.panel.editor()
        if editor is None:
            return
        cursor = editor.textCursor()
        first = editor.document().findBlock(cursor.selectionStart()).blockNumber()
        last = editor.document().findBlock(cursor.selectionEnd()).blockNumber()
        self._start_format((first, last))

    def _formatted(self, lines: list) -> None:
        editor, self.format_editor = self.format_editor, None
        if editor is None or editor.toPlainText().split("\n") != self.formatter.text.split("\n"):
            self._note("Der Text hat sich geändert, bitte noch einmal formatieren")
            return
        changed = editor.replace_lines(lines)
        self._note(f"Formatiert: {changed} Zeilen" if changed else "Der Code ist schon sauber")
        self._finish_saving()

    def _format_failed(self, message: str) -> None:
        self.format_editor = None
        self._note(message)
        self._finish_saving()

    def _finish_saving(self) -> None:
        tab, self.save_after_format = self.save_after_format, None
        if tab is not None and tab in self.panel.tabs:
            self.panel.save(tab)

    def _note(self, text: str) -> None:
        self.status_bar.show_text(text)
        self._note_timer.start()

    def _clear_note(self) -> None:
        if self.debug.is_paused:
            self.status_bar.show_text(f"Angehalten in Zeile {self.debug.state['line']}")
        else:
            self.status_bar.show_text("Läuft" if self.debug.running else "")

    # --- Halten ------------------------------------------------------------------
    def _on_paused(self, state: dict) -> None:
        self.variables.show_state(state["variables"])
        self.watch.show_state(state["watches"])
        self.stack.show_state(state["stack"])
        self.status_bar.show_text(f"Angehalten in Zeile {state['line']}")
        self.docks["variables"].raise_()
        self._clear_stop()
        tab = self._tab_for(state["file"])
        if tab is None and state["file"] and not same_file(state["file"], str(DATA_DIR / RUN_FILE)):
            tab = self.panel.open_file(state["file"])
            self._prepare_tabs()
        if tab is None:
            tab = self.panel.current()  # ungespeichertes Skript läuft als Kopie, die Zeilen passen trotzdem
        if tab is not None:
            self.panel.bar.setCurrentIndex(self.panel.tabs.index(tab))
            tab.editor.show_stop(state["line"] - 1)
            self.stop_at = (tab.editor, state["line"])

    def _clear_stop(self) -> None:
        if self.stop_at:
            self.stop_at[0].show_stop(-1)
            self.stop_at = None

    def _on_idle(self) -> None:
        self._clear_stop()
        self.status_bar.show_text("")
        self.variables.show_state([])
        self.stack.show_state([])

    def _tab_for(self, file: str):
        return next((tab for tab in self.panel.tabs if same_file(tab.path, file)), None)

    def go_to(self, file: str, line: int) -> None:
        tab = self._tab_for(file) if file else self.panel.current()
        if tab is None and file and os.path.exists(file):
            tab = self.panel.open_file(file)
            self._prepare_tabs()
        if tab is not None:
            self.panel.bar.setCurrentIndex(self.panel.tabs.index(tab))
            tab.editor.go_to_line(line)
            tab.editor.setFocus()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.debug.is_paused:
            editor = watched.parent()
            cursor = editor.cursorForPosition(event.pos())
            name = word_at(cursor.block().text(), cursor.positionInBlock())
            value = self.variables.values.get(name.lower())
            if value is not None:
                QToolTip.showText(event.globalPos(), f"${name} = {value}", editor)
                return True
        return super().eventFilter(watched, event)

    # --- Haltepunkte -------------------------------------------------------------
    def _toggle_mark(self, editor, line: int) -> None:
        block = editor.document().findBlockByNumber(line)
        block.setUserData(None if isinstance(block.userData(), BreakMark) else BreakMark())
        editor.gutter.update()
        self._refresh_marks()

    def toggle_break(self) -> None:
        editor = self.panel.editor()
        if editor:
            self._toggle_mark(editor, editor.textCursor().blockNumber())

    def clear_breaks(self) -> None:
        for tab in self.panel.tabs:
            for block in tab.editor.marked_blocks():
                block.setUserData(None)
            tab.editor.gutter.update()
        self._refresh_marks()

    def _mark_menu(self, editor, line: int, point) -> None:
        block = editor.document().findBlockByNumber(line)
        mark = block.userData()
        menu = QMenu(self)
        if isinstance(mark, BreakMark):
            menu.addAction("Haltepunkt bearbeiten", lambda: self._edit_mark(editor, block))
            menu.addAction("Deaktivieren" if mark.enabled else "Aktivieren", lambda: self._set_enabled(editor, block, not mark.enabled))
            menu.addAction("Haltepunkt löschen", lambda: self._toggle_mark(editor, line))
        else:
            menu.addAction("Haltepunkt setzen", lambda: self._toggle_mark(editor, line))
            menu.addAction("Bedingten Haltepunkt setzen", lambda: self._edit_mark(editor, block))
            menu.addAction("Logpunkt setzen", lambda: self._edit_mark(editor, block, True))
        menu.exec(point)

    def _edit_mark(self, editor, block, log: bool = False) -> None:
        mark = block.userData() if isinstance(block.userData(), BreakMark) else BreakMark()
        if log or mark.message:
            text, ok = QInputDialog.getText(self, "Logpunkt", "Meldung in der Konsole, zum Beispiel: Wert ist $wert", text=mark.message)
            if ok:
                mark.message, mark.condition = text.strip(), ""
        else:
            text, ok = QInputDialog.getText(self, "Haltepunkt", "Nur anhalten, wenn das wahr ist, zum Beispiel: $i -gt 5", text=mark.condition)
            if ok:
                mark.condition = text.strip()
        if ok:
            block.setUserData(mark)
            editor.gutter.update()
            self._refresh_marks()

    def edit_break_here(self) -> None:
        editor = self.panel.editor()
        if editor:
            self._edit_mark(editor, editor.textCursor().block())

    def _set_enabled(self, editor, block, enabled: bool) -> None:
        block.userData().enabled = enabled
        editor.gutter.update()
        self._refresh_marks()

    def _find_mark(self, file: str, line: int):
        for tab in self.panel.tabs:
            if tab.path == file:
                block = tab.editor.document().findBlockByNumber(line - 1)
                if isinstance(block.userData(), BreakMark):
                    return tab.editor, block
        return None

    def _enable_mark(self, file: str, line: int, enabled: bool) -> None:
        found = self._find_mark(file, line)
        if found:
            self._set_enabled(*found, enabled)

    def _edit_mark_at(self, file: str, line: int) -> None:
        found = self._find_mark(file, line)
        if found:
            self._edit_mark(*found)

    def _remove_mark_at(self, file: str, line: int) -> None:
        found = self._find_mark(file, line)
        if found:
            found[1].setUserData(None)
            found[0].gutter.update()
            self._refresh_marks()

    def all_marks(self, running: str = "", editor=None) -> list[BreakPoint]:
        """Alle Haltepunkte; die eines ungespeicherten Skripts zeigen auf die Kopie, die gerade läuft."""
        marks = []
        for tab in self.panel.tabs:
            file = running if tab.editor is editor and running else tab.path
            for block in tab.editor.marked_blocks():
                mark = block.userData()
                marks.append(BreakPoint(file, block.blockNumber() + 1, mark.enabled, mark.condition, mark.message))
        return marks

    def _refresh_marks(self) -> None:
        self.breaks.show_marks(self.all_marks())

    def _load_marks(self) -> None:
        try:
            saved = json.loads((DATA_DIR / MARKS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for tab in self.panel.tabs:
            for entry in saved.get(os.path.normcase(tab.path), []):
                block = tab.editor.document().findBlockByNumber(entry["line"] - 1)
                if block.isValid():
                    block.setUserData(BreakMark(entry["condition"], entry["enabled"], entry["message"]))

    def _store_marks(self) -> None:
        saved = {}
        for tab in self.panel.tabs:
            if tab.path:
                entries = [{"line": block.blockNumber() + 1, "condition": block.userData().condition,
                            "enabled": block.userData().enabled, "message": block.userData().message}
                           for block in tab.editor.marked_blocks()]
                if entries:
                    saved[os.path.normcase(tab.path)] = entries
        (DATA_DIR / MARKS_FILE).write_text(json.dumps(saved), encoding="utf-8")

    def _watches_changed(self, watches: list[str]) -> None:
        self.settings["watches"] = watches

    # --- Prüfen beim Tippen ------------------------------------------------------
    def _text_changed(self) -> None:
        self._check_timer.start()

    def _check_now(self) -> None:
        editor = self.panel.editor()
        if editor is None:
            return
        text = editor.toPlainText()
        self.outline.show_outline(outline(text))
        self._refresh_marks()
        if self.settings["live_check"] and len(text) < CHECK_LIMIT:
            self.checker.check(text)

    def _on_problems(self, problems: list[dict]) -> None:
        self.problems.show_problems(problems)
        self.docks["problems"].setWindowTitle(f"Probleme ({len(problems)})" if problems else "Probleme")
        editor = self.panel.editor()
        if editor:
            editor.show_problems([problem["line"] - 1 for problem in problems])

    def closeEvent(self, event) -> None:
        self._store_marks()
        self._store_settings()
        self.panel.save_session()
        self.console.stop()
        super().closeEvent(event)


def main() -> int:
    return run_app("Studio", "PowerShell Studio", lambda ctx, arguments: StudioWindow(ctx, arguments))


if __name__ == "__main__":
    sys.exit(main())
