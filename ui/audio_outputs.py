"""Liste der Lautsprecher und Headsets. Ein Klick macht das Gerät zur Audio-Ausgabe von ganz Windows."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListWidget, QListWidgetItem

from core import system_status

MARK = "✓  "
PLAIN = "     "
ROW_PADDING = 10
STYLE = ("QListWidget { background: transparent; border: none; } "
         "QListWidget::item { padding: 2px 6px; border-radius: 6px; } "
         "QListWidget::item:hover { background: rgba(128, 128, 128, 0.25); }")


class AudioOutputs(QListWidget):
    """Liest die Geräte bei jedem Öffnen neu: Headsets kommen und gehen."""
    switched = Signal()
    failed = Signal(str)

    def __init__(self, rows: int):
        super().__init__()
        self.setStyleSheet(STYLE)
        self.rows = rows
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.itemClicked.connect(self._choose)

    def refresh(self) -> None:
        self.clear()
        self.ensurePolished()
        height = self.fontMetrics().lineSpacing() + ROW_PADDING
        for device in system_status.audio_outputs():
            item = QListWidgetItem((MARK if device.active else PLAIN) + device.name)
            item.setToolTip(device.full_name)
            item.setData(Qt.ItemDataRole.UserRole, device.id)
            item.setSizeHint(QSize(0, height))
            self.addItem(item)
        self.setVisible(self.count() > 1)  # mit nur einem Gerät gibt es nichts zu wählen
        if self.count():
            self.setFixedHeight(height * min(self.rows, self.count()) + 2)

    def _choose(self, item: QListWidgetItem) -> None:
        try:
            system_status.set_audio_output(item.data(Qt.ItemDataRole.UserRole))
        except OSError as exc:
            self.failed.emit(f"Ausgabe ließ sich nicht wechseln: {exc}")
            return
        self.refresh()
        self.switched.emit()
