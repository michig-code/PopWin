"""Liste der Lautsprecher und Mikrofone. Ein Klick macht das Gerät zum Standard von ganz Windows.
Oben wählt eine kleine Leiste zwischen Ausgabe und Eingabe."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core import system_status

MARK = "✓  "
PLAIN = "     "
ROW_PADDING = 10
STYLE = ("QListWidget { background: transparent; border: none; } "
         "QListWidget::item { padding: 2px 6px; border-radius: 6px; } "
         "QListWidget::item:hover { background: rgba(128, 128, 128, 0.25); }")
SWITCH_STYLE = ("QFrame#DeviceSwitch { background: rgba(128, 128, 128, 0.16); border-radius: 10px; } "
                "QFrame#DeviceSwitch QPushButton { background: transparent; border: none; border-radius: 8px; "
                "padding: 5px 8px; font-weight: 600; } "
                "QFrame#DeviceSwitch QPushButton:hover { background: rgba(128, 128, 128, 0.18); } "
                "QFrame#DeviceSwitch QPushButton:checked { background: rgba(128, 128, 128, 0.38); }")
KINDS = {"out": ("Ausgabegeräte", "Ausgabe"), "in": ("Eingabegeräte", "Eingabe")}


class AudioOutputs(QListWidget):
    """Liest die Geräte bei jedem Öffnen neu: Headsets kommen und gehen."""
    switched = Signal()
    failed = Signal(str)

    def __init__(self, rows: int, kind: str = "out", hide_single: bool = True):
        super().__init__()
        self.setStyleSheet(STYLE)
        self.rows = rows
        self.kind = kind
        self.hide_single = hide_single
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.itemClicked.connect(self._choose)

    def devices(self) -> list:
        return system_status.audio_inputs() if self.kind == "in" else system_status.audio_outputs()

    def refresh(self) -> None:
        self.clear()
        self.ensurePolished()
        height = self.fontMetrics().lineSpacing() + ROW_PADDING
        for device in self.devices():
            item = QListWidgetItem((MARK if device.active else PLAIN) + device.name)
            item.setToolTip(device.full_name)
            item.setData(Qt.ItemDataRole.UserRole, device.id)
            item.setSizeHint(QSize(0, height))
            self.addItem(item)
        if self.hide_single:
            self.setVisible(self.count() > 1)  # mit nur einem Gerät gibt es nichts zu wählen
        if self.count():
            self.setFixedHeight(height * min(self.rows, self.count()) + 2)

    def _choose(self, item: QListWidgetItem) -> None:
        try:
            system_status.set_audio_output(item.data(Qt.ItemDataRole.UserRole))  # geht für Lautsprecher und Mikrofone
        except OSError as exc:
            what = "Eingabe" if self.kind == "in" else "Ausgabe"
            self.failed.emit(f"{what} ließ sich nicht wechseln: {exc}")
            return
        self.refresh()
        self.switched.emit()


class AudioDevices(QWidget):
    """Leiste „Ausgabe | Eingabe“ und darunter die passende Liste. Ein Klick auf die Leiste wechselt."""
    switched = Signal()
    failed = Signal(str)

    def __init__(self, rows: int):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.bar = QFrame(self, objectName="DeviceSwitch")
        self.bar.setStyleSheet(SWITCH_STYLE)
        self.bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # wächst nie in die Höhe
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for kind, (_title, label) in KINDS.items():
            button = QPushButton(label, self.bar)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=kind: self.show_kind(k))
            self.group.addButton(button)
            row.addWidget(button)
            self.buttons[kind] = button
        layout.addWidget(self.bar)
        self.lists: dict[str, AudioOutputs] = {}
        for kind in KINDS:
            view = AudioOutputs(rows, kind, hide_single=False)
            view.setParent(self)  # mit Eltern: nie kurz ein eigenes Fenster
            view.hide()
            view.switched.connect(self.switched)
            view.failed.connect(self.failed)
            layout.addWidget(view)
            self.lists[kind] = view
        self.kind = "out"
        self.buttons["out"].setChecked(True)

    def show_kind(self, kind: str) -> None:
        self.kind = kind
        self.buttons[kind].setChecked(True)
        for name, view in self.lists.items():
            if name == kind:
                view.refresh()
            view.setVisible(name == kind and view.count() > 0)
        self._fit_window()

    def _fit_window(self) -> None:
        """Die Listen sind verschieden lang: Das offene Fenster passt sich an, statt dass die Leiste den Rest füllt."""
        window = self.window()
        if window is self or not window.isVisible():
            return
        old = window.geometry()
        window.adjustSize()
        screen = window.screen().availableGeometry() if window.screen() else old
        if old.center().y() > screen.center().y():  # unten aufgeklappt: die Unterkante bleibt, wo sie war
            window.move(window.x(), old.bottom() - window.height() + 1)

    def refresh(self) -> None:
        self.show_kind(self.kind)

    def count(self) -> int:
        return self.lists[self.kind].count()

    def title(self) -> str:
        return KINDS[self.kind][0]
