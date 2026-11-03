"""Alles, was man am PowerShell Studio einstellen kann, und das Fenster dafür. Liegt getrennt von PopWins Einstellungen."""
from __future__ import annotations

import copy
import json

from PySide6.QtGui import QColor, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFontComboBox, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QKeySequenceEdit, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core import i18n
from core.paths import DATA_DIR

SETTINGS_FILE = "studio_settings.json"
SCHEMES = {
    "Dark Modern": {"background": "#1E1E1E", "text": "#D4D4D4", "keyword": "#569CD6", "string": "#CE9178", "comment": "#6A9955",
                    "number": "#B5CEA8", "name": "#DCDCAA", "variable": "#9CDCFE", "gutter": "#858585", "line": "#2A2D2E",
                    "match": "#515C6A", "stop": "#5A4B00"},
    "ISE Klassik": {"background": "#012456", "text": "#EEEDF0", "keyword": "#7FC4FF", "string": "#FFB27A", "comment": "#7EC699",
                    "number": "#F5D76E", "name": "#FFFFFF", "variable": "#FF8FD8", "gutter": "#7E9BC4", "line": "#0A3370",
                    "match": "#2C5A9A", "stop": "#6B5900"},
    "Monokai": {"background": "#272822", "text": "#F8F8F2", "keyword": "#F92672", "string": "#E6DB74", "comment": "#75715E",
                "number": "#AE81FF", "name": "#A6E22E", "variable": "#66D9EF", "gutter": "#90908A", "line": "#3E3D32",
                "match": "#49483E", "stop": "#6B5B00"},
    "Dracula": {"background": "#282A36", "text": "#F8F8F2", "keyword": "#FF79C6", "string": "#F1FA8C", "comment": "#6272A4",
                "number": "#BD93F9", "name": "#50FA7B", "variable": "#8BE9FD", "gutter": "#6272A4", "line": "#44475A",
                "match": "#5A5D72", "stop": "#6B5B00"},
    "Solarized Hell": {"background": "#FDF6E3", "text": "#586E75", "keyword": "#859900", "string": "#2AA198", "comment": "#93A1A1",
                       "number": "#D33682", "name": "#268BD2", "variable": "#B58900", "gutter": "#93A1A1", "line": "#EEE8D5",
                       "match": "#E4DCC2", "stop": "#FBE7A1"},
    "GitHub Hell": {"background": "#FFFFFF", "text": "#24292F", "keyword": "#CF222E", "string": "#0A3069", "comment": "#6E7781",
                    "number": "#0550AE", "name": "#8250DF", "variable": "#953800", "gutter": "#8C959F", "line": "#F6F8FA",
                    "match": "#FFE58A", "stop": "#FFF3B0"},
}
COLOR_LABELS = {"background": "Hintergrund", "text": "Text", "keyword": "Schlüsselwörter", "string": "Texte in Anführungszeichen",
                "comment": "Kommentare", "number": "Zahlen", "name": "Befehle", "variable": "Variablen", "gutter": "Zeilennummern",
                "line": "Aktuelle Zeile", "match": "Treffer", "stop": "Haltezeile"}
SNIPPETS = {
    "Funktion": "function Verb-Noun {\n    param(\n        [string]$Name\n    )\n\n}\n",
    "if": "if ($true) {\n\n}\n",
    "foreach": "foreach ($item in $items) {\n\n}\n",
    "try/catch": "try {\n\n} catch {\n    Write-Error $_\n}\n",
    "Parameter": "param(\n    [Parameter(Mandatory)]\n    [string]$Path\n)\n",
}
DEFAULTS = {
    "scheme": "Dark Modern", "colors": {}, "font": "Cascadia Code", "font_size": 11, "tab_width": 4, "wrap": False,
    "line_numbers": True, "line_highlight": True, "completion": True, "live_check": True, "save_before_run": True,
    "restore_session": True, "shell": "powershell.exe", "console_size": 11, "start_folder": "", "script_arguments": "",
    "keys": {}, "toolbar": ["run", "selection", "stop", "toggle_break", "step_over", "step_into", "step_out"],
    "snippets": SNIPPETS, "layout": "", "watches": [],
    "format_on_save": False, "format_tabs": False, "format_indent": 4, "format_spacing": True, "format_casing": True,
    "format_blank_lines": 2,
}


def load_settings() -> dict:
    settings = copy.deepcopy(DEFAULTS)
    try:
        saved = json.loads((DATA_DIR / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(saved, dict):
        settings.update({key: value for key, value in saved.items() if key in DEFAULTS})
    return settings


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / SETTINGS_FILE).write_text(json.dumps(settings, indent=1, ensure_ascii=False), encoding="utf-8")


def colors_of(settings: dict) -> dict:
    return {**SCHEMES.get(settings["scheme"], SCHEMES["Dark Modern"]), **settings["colors"]}


class SettingsDialog(QDialog):
    """Änderungen gelten erst nach OK; Abbrechen lässt alles, wie es war."""

    def __init__(self, parent: QWidget, settings: dict, actions: list):
        super().__init__(parent)
        self.setWindowTitle("PowerShell Studio anpassen")
        self.resize(720, 620)
        self.settings = copy.deepcopy(settings)
        self.actions = actions
        tabs = QTabWidget()
        tabs.addTab(self._look(), "Aussehen")
        tabs.addTab(self._behavior(), "Verhalten")
        tabs.addTab(self._formatting(), "Formatieren")
        tabs.addTab(self._keys(), "Tasten")
        tabs.addTab(self._toolbar(), "Werkzeugleiste")
        tabs.addTab(self._snippets(), "Schnipsel")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addWidget(tabs, 1)
        box.addWidget(buttons)

    # --- Aussehen ----------------------------------------------------------------
    def _look(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.scheme = QComboBox()
        self.scheme.addItems(SCHEMES)
        self.scheme.setCurrentText(self.settings["scheme"])
        self.scheme.currentTextChanged.connect(self._scheme_chosen)
        form.addRow("Farbschema", self.scheme)
        self.font = QFontComboBox()
        self.font.setCurrentFont(self.settings["font"])
        form.addRow("Schrift", self.font)
        self.size = self._number(7, 40, self.settings["font_size"])
        form.addRow("Schriftgröße", self.size)
        self.tab_width = self._number(1, 12, self.settings["tab_width"])
        form.addRow("Tab-Breite", self.tab_width)
        self.wrap = self._check("Lange Zeilen umbrechen", "wrap")
        self.numbers = self._check("Zeilennummern anzeigen", "line_numbers")
        self.highlight = self._check("Aktuelle Zeile hervorheben", "line_highlight")
        for check in (self.wrap, self.numbers, self.highlight):
            form.addRow(check)
        self.swatches: dict[str, QPushButton] = {}
        for key, label in COLOR_LABELS.items():
            swatch = QPushButton()
            swatch.setFixedWidth(120)
            swatch.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self.swatches[key] = swatch
            form.addRow(label, swatch)
        self._paint_swatches()
        return page

    def _number(self, low: int, high: int, value: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(low, high)
        field.setValue(value)
        return field

    def _check(self, label: str, key: str) -> QCheckBox:
        check = QCheckBox(label)
        check.setChecked(self.settings[key])
        return check

    def _scheme_chosen(self, name: str) -> None:
        self.settings["scheme"] = name
        self.settings["colors"] = {}
        self._paint_swatches()

    def _paint_swatches(self) -> None:
        colors = colors_of(self.settings)
        for key, swatch in self.swatches.items():
            swatch.setText(colors[key])
            swatch.setStyleSheet(f"background: {colors[key]}; color: {'#000000' if QColor(colors[key]).lightness() > 128 else '#FFFFFF'};")

    def _pick_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(colors_of(self.settings)[key]), self, COLOR_LABELS[key])
        if color.isValid():
            self.settings["colors"][key] = color.name()
            self._paint_swatches()

    # --- Verhalten ---------------------------------------------------------------
    def _behavior(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.shell = QComboBox(editable=True)
        self.shell.addItems(["powershell.exe", "pwsh.exe"])
        self.shell.setCurrentText(self.settings["shell"])
        form.addRow("Konsole (gilt nach Neustart)", self.shell)
        self.console_size = self._number(7, 32, self.settings["console_size"])
        form.addRow("Schriftgröße der Konsole", self.console_size)
        self.start_folder = QLineEdit(self.settings["start_folder"], placeholderText="Benutzerordner")
        form.addRow("Startordner der Konsole", self.start_folder)
        self.arguments = QLineEdit(self.settings["script_arguments"], placeholderText="zum Beispiel -Name Test")
        form.addRow("Argumente beim Ausführen", self.arguments)
        self.completion = self._check("Befehle beim Tippen vorschlagen", "completion")
        self.live_check = self._check("Syntaxfehler beim Tippen anzeigen", "live_check")
        self.save_first = self._check("Vor dem Ausführen speichern", "save_before_run")
        self.restore = self._check("Offene Tabs beim Start wiederherstellen", "restore_session")
        for check in (self.completion, self.live_check, self.save_first, self.restore):
            form.addRow(check)
        return page

    # --- Formatieren -------------------------------------------------------------
    def _formatting(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.format_save = self._check("Beim Speichern formatieren", "format_on_save")
        self.format_tabs = self._check("Mit Tabs statt Leerzeichen einrücken", "format_tabs")
        self.format_spacing = self._check("Abstände um Operatoren, Kommas und Klammern setzen", "format_spacing")
        self.format_casing = self._check("Schreibweise von Befehlen und Schlüsselwörtern korrigieren", "format_casing")
        for check in (self.format_save, self.format_tabs, self.format_spacing, self.format_casing):
            form.addRow(check)
        self.format_indent = self._number(1, 12, self.settings["format_indent"])
        form.addRow("Einrückung in Leerzeichen", self.format_indent)
        self.format_blank = self._number(0, 9, self.settings["format_blank_lines"])
        form.addRow("Leerzeilen hintereinander, höchstens", self.format_blank)
        return page

    # --- Tasten ------------------------------------------------------------------
    def _keys(self) -> QWidget:
        self.key_table = QTableWidget(len(self.actions), 2)
        self.key_table.setHorizontalHeaderLabels(["Befehl", "Taste"])
        self.key_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.key_table.verticalHeader().hide()
        self.key_edits: dict[str, QKeySequenceEdit] = {}
        for row, action in enumerate(self.actions):
            self.key_table.setItem(row, 0, QTableWidgetItem(action.label))
            field = QKeySequenceEdit(QKeySequence(self.settings["keys"].get(action.key, action.shortcut)))
            self.key_edits[action.key] = field
            self.key_table.setCellWidget(row, 1, field)
        return self.key_table

    # --- Werkzeugleiste ----------------------------------------------------------
    def _toolbar(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        self.shown = QListWidget()
        self.shown.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.hidden_tools = QListWidget()
        labels = {action.key: i18n.translate(action.label) for action in self.actions}
        self.shown.addItems([labels[key] for key in self.settings["toolbar"] if key in labels])
        self.hidden_tools.addItems([i18n.translate(action.label) for action in self.actions if action.key not in self.settings["toolbar"]])
        middle = QVBoxLayout()
        add, drop = QPushButton("←  Anzeigen"), QPushButton("Ausblenden  →")
        add.clicked.connect(lambda: self._move_tool(self.hidden_tools, self.shown))
        drop.clicked.connect(lambda: self._move_tool(self.shown, self.hidden_tools))
        middle.addStretch(1)
        middle.addWidget(add)
        middle.addWidget(drop)
        middle.addStretch(1)
        row.addWidget(self.shown, 1)
        row.addLayout(middle)
        row.addWidget(self.hidden_tools, 1)
        return page

    @staticmethod
    def _move_tool(source: QListWidget, target: QListWidget) -> None:
        if source.currentItem():
            target.addItem(source.takeItem(source.currentRow()).text())

    # --- Schnipsel ---------------------------------------------------------------
    def _snippets(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        side = QVBoxLayout()
        self.snippet_names = QListWidget()
        self.snippet_names.addItems(self.settings["snippets"])
        self.snippet_names.currentTextChanged.connect(self._show_snippet)
        add, remove = QPushButton("Neu"), QPushButton("Löschen")
        add.clicked.connect(self._add_snippet)
        remove.clicked.connect(self._remove_snippet)
        side.addWidget(self.snippet_names, 1)
        side.addWidget(add)
        side.addWidget(remove)
        self.snippet_text = QPlainTextEdit()
        self.snippet_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.snippet_text.textChanged.connect(self._keep_snippet)
        self._snippet = ""
        row.addLayout(side, 1)
        row.addWidget(self.snippet_text, 2)
        return page

    def _show_snippet(self, name: str) -> None:
        self._snippet = ""
        self.snippet_text.setPlainText(self.settings["snippets"].get(name, ""))
        self._snippet = name

    def _keep_snippet(self) -> None:
        if self._snippet:
            self.settings["snippets"][self._snippet] = self.snippet_text.toPlainText()

    def _add_snippet(self) -> None:
        name, ok = QInputDialog.getText(self, "Neuer Schnipsel", "Name")
        if ok and name.strip() and name not in self.settings["snippets"]:
            self.settings["snippets"][name] = ""
            self.snippet_names.addItem(name)
            self.snippet_names.setCurrentRow(self.snippet_names.count() - 1)

    def _remove_snippet(self) -> None:
        item = self.snippet_names.currentItem()
        if item:
            self.settings["snippets"].pop(item.text(), None)
            self._snippet = ""
            self.snippet_names.takeItem(self.snippet_names.row(item))

    # --- Ergebnis ----------------------------------------------------------------
    def result_settings(self) -> dict:
        result = self.settings
        result.update(scheme=self.scheme.currentText(), font=self.font.currentFont().family(), font_size=self.size.value(),
                      tab_width=self.tab_width.value(), wrap=self.wrap.isChecked(), line_numbers=self.numbers.isChecked(),
                      line_highlight=self.highlight.isChecked(), shell=self.shell.currentText().strip() or "powershell.exe",
                      console_size=self.console_size.value(), start_folder=self.start_folder.text().strip(),
                      script_arguments=self.arguments.text(), completion=self.completion.isChecked(),
                      live_check=self.live_check.isChecked(), save_before_run=self.save_first.isChecked(),
                      restore_session=self.restore.isChecked(), format_on_save=self.format_save.isChecked(),
                      format_tabs=self.format_tabs.isChecked(), format_spacing=self.format_spacing.isChecked(),
                      format_casing=self.format_casing.isChecked(), format_indent=self.format_indent.value(),
                      format_blank_lines=self.format_blank.value())
        defaults = {action.key: action.shortcut for action in self.actions}
        keys = {key: field.keySequence().toString() for key, field in self.key_edits.items()}
        result["keys"] = {key: value for key, value in keys.items() if value != defaults[key]}
        by_label = {i18n.translate(action.label): action.key for action in self.actions}
        result["toolbar"] = [by_label[self.shown.item(row).text()] for row in range(self.shown.count())]
        return result
