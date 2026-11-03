"""Die andockbaren Bereiche des PowerShell Studios: Variablen, Beobachten, Aufrufe, Haltepunkte, Probleme, Gliederung."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from studio.debugger import BreakPoint


class CommandList(QWidget):
    insert = Signal(str)

    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit(placeholderText="Befehl suchen")
        self.search.textChanged.connect(self._filter)
        self.items = QListWidget()
        self.items.itemActivated.connect(lambda item: self.insert.emit(item.text()))
        box.addWidget(self.search)
        box.addWidget(self.items, 1)
        self.names: list[str] = []

    def set_names(self, names: list[str]) -> None:
        self.names = names
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        self.items.clear()
        needle = text.lower()
        self.items.addItems([name for name in self.names if needle in name.lower()][:400])


class VariablesPanel(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setColumnCount(3)
        self.setHeaderLabels(["Name", "Wert", "Typ"])
        self.setRootIsDecorated(False)
        self.setColumnWidth(0, 130)
        self.setColumnWidth(1, 200)
        self.values: dict[str, str] = {}

    def show_state(self, variables: list[dict]) -> None:
        self.clear()
        self.values = {entry["name"].lower(): entry["value"] for entry in variables}
        for entry in variables:
            row = QTreeWidgetItem(["$" + entry["name"], entry["value"], entry.get("type", "")])
            row.setToolTip(1, entry["value"])
            self.addTopLevelItem(row)


class WatchPanel(QWidget):
    """Ausdrücke, die an jedem Halt neu berechnet werden."""
    changed = Signal(list)

    def __init__(self, watches: list[str]):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 4, 4, 4)
        self.field = QLineEdit(placeholderText="Ausdruck hinzufügen, zum Beispiel $name.Length")
        self.field.returnPressed.connect(self._add)
        self.table = QTreeWidget()
        self.table.setColumnCount(2)
        self.table.setHeaderLabels(["Ausdruck", "Wert"])
        self.table.setRootIsDecorated(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        box.addWidget(self.field)
        box.addWidget(self.table, 1)
        self.watches = list(watches)
        self._fill({})

    def _fill(self, values: dict) -> None:
        self.table.clear()
        for watch in self.watches:
            self.table.addTopLevelItem(QTreeWidgetItem([watch, values.get(watch, "")]))

    def _add(self) -> None:
        text = self.field.text().strip()
        if text and text not in self.watches:
            self.watches.append(text)
            self._fill({})
            self.changed.emit(self.watches)
        self.field.clear()

    def _menu(self, position) -> None:
        item = self.table.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Entfernen", lambda: self._remove(item.text(0)))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _remove(self, watch: str) -> None:
        self.watches.remove(watch)
        self._fill({})
        self.changed.emit(self.watches)

    def show_state(self, watches: list[dict]) -> None:
        self._fill({entry["expression"]: entry["value"] for entry in watches})


class LocationList(QListWidget):
    """Liste von Stellen im Code; ein Klick springt hin."""
    jump = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.itemActivated.connect(self._go)
        self.itemClicked.connect(self._go)

    def _go(self, item: QListWidgetItem) -> None:
        file, line = item.data(Qt.ItemDataRole.UserRole)
        if line:
            self.jump.emit(file or "", line)

    def add_place(self, label: str, file: str, line: int) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, (file, line))
        self.addItem(item)


class StackPanel(LocationList):
    def show_state(self, stack: list[dict]) -> None:
        self.clear()
        for frame in stack:
            if frame.get("file"):
                self.add_place(f"{frame['name']}  ({os.path.basename(frame['file'])}:{frame['line']})", frame["file"], frame["line"])


class ProblemsPanel(LocationList):
    def show_problems(self, problems: list[dict]) -> None:
        self.clear()
        for problem in problems:
            self.add_place(f"Zeile {problem['line']}: {problem['message']}", "", problem["line"])


class OutlinePanel(LocationList):
    def show_outline(self, names: list[tuple[str, int]]) -> None:
        self.clear()
        for name, line in names:
            self.add_place(name, "", line)


class BreakpointPanel(QTreeWidget):
    jump = Signal(str, int)
    toggled = Signal(str, int, bool)
    edit = Signal(str, int)
    remove = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.setColumnCount(3)
        self.setHeaderLabels(["Stelle", "Bedingung", "Meldung"])
        self.setRootIsDecorated(False)
        self.setColumnWidth(0, 180)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemChanged.connect(self._checked)
        self.itemActivated.connect(lambda item, _column: self.jump.emit(*item.data(0, Qt.ItemDataRole.UserRole)))
        self.itemClicked.connect(lambda item, _column: self.jump.emit(*item.data(0, Qt.ItemDataRole.UserRole)))

    def show_marks(self, marks: list[BreakPoint]) -> None:
        self.blockSignals(True)
        self.clear()
        for mark in marks:
            name = os.path.basename(mark.file) or "Unbenannt"
            row = QTreeWidgetItem([f"{name}:{mark.line}", mark.condition, mark.message])
            row.setCheckState(0, Qt.CheckState.Checked if mark.enabled else Qt.CheckState.Unchecked)
            row.setData(0, Qt.ItemDataRole.UserRole, (mark.file, mark.line))
            self.addTopLevelItem(row)
        self.blockSignals(False)

    def _checked(self, item: QTreeWidgetItem, _column: int) -> None:
        self.toggled.emit(*item.data(0, Qt.ItemDataRole.UserRole), item.checkState(0) == Qt.CheckState.Checked)

    def _menu(self, position) -> None:
        item = self.itemAt(position)
        if item is None:
            return
        place = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("Bearbeiten", lambda: self.edit.emit(*place))
        menu.addAction("Löschen", lambda: self.remove.emit(*place))
        menu.exec(self.viewport().mapToGlobal(position))


class CommandPalette(QDialog):
    """Strg + Umschalt + P wie in VS Code: jeder Befehl des Studios, ohne das Menü zu suchen."""

    def __init__(self, parent: QWidget, actions: list[tuple[str, str, object]], placeholder: str = "Befehl eingeben"):
        super().__init__(parent, Qt.WindowType.Popup)
        self.resize(520, 340)
        self.entries = actions
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 6, 6, 6)
        self.field = QLineEdit(placeholderText=placeholder)
        self.field.textChanged.connect(self._filter)
        self.field.returnPressed.connect(self._run)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _item: self._run())
        box.addWidget(self.field)
        box.addWidget(self.list, 1)
        self._filter("")
        self.field.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + step)))
        else:
            super().keyPressEvent(event)

    def _filter(self, text: str) -> None:
        self.list.clear()
        words = text.lower().split()
        for label, key, handler in self.entries:
            if all(word in label.lower() for word in words):
                item = QListWidgetItem(f"{label}    {key}" if key else label)
                item.setData(Qt.ItemDataRole.UserRole, handler)
                self.list.addItem(item)
        self.list.setCurrentRow(0)

    def _run(self) -> None:
        item = self.list.currentItem()
        if item:
            handler = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
            handler()


class DebugBar(QWidget):
    """Klein genug für die Titelleiste, zeigt, ob gerade ein Skript läuft oder hält."""

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.state = QPushButton(objectName="Secondary")
        self.state.setEnabled(False)
        row.addWidget(self.state)
        self.hide()

    def show_text(self, text: str) -> None:
        self.state.setText(text)
        self.setVisible(bool(text))
