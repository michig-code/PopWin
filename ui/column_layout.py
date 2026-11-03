"""Ordnet Blöcke in so vielen Spalten an, wie die Breite hergibt, damit nichts gestreckt aussieht."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout

MIN_COLUMN_WIDTH = 460
MAX_COLUMNS = 4


def column_count(width: int, spacing: int) -> int:
    return max(1, min(MAX_COLUMNS, (width + spacing) // (MIN_COLUMN_WIDTH + spacing)))


class ColumnLayout(QLayout):
    """Jeder Block kommt in die gerade kürzeste Spalte; add_full legt einen über die ganze Breite."""

    def __init__(self, parent=None, spacing: int = 12):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self._items = []
        self._full = set()
        self._gap = spacing

    def addItem(self, item) -> None:
        self._items.append(item)

    def add_full(self, widget) -> None:
        self.addWidget(widget)
        self._full.add(widget)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        if not 0 <= index < len(self._items):
            return None
        item = self._items.pop(index)
        self._full.discard(item.widget())
        return item

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        margins = self.contentsMargins()
        width = MIN_COLUMN_WIDTH // 2 + margins.left() + margins.right()
        return QSize(width, self.heightForWidth(width))

    @staticmethod
    def _height(item, width: int) -> int:
        return item.heightForWidth(width) if item.hasHeightForWidth() else item.sizeHint().height()

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        left, top = rect.x() + margins.left(), rect.y() + margins.top()
        width = rect.width() - margins.left() - margins.right()
        columns = column_count(width, self._gap)
        column_width = (width - (columns - 1) * self._gap) // columns
        heights = [top] * columns
        for item in self._items:
            if item.isEmpty():
                continue
            if item.widget() in self._full:
                y = max(heights)
                height = self._height(item, width)
                if apply:
                    item.setGeometry(QRect(QPoint(left, y), QSize(width, height)))
                heights = [y + height + self._gap] * columns
                continue
            column = heights.index(min(heights))
            height = self._height(item, column_width)
            if apply:
                x = left + column * (column_width + self._gap)
                item.setGeometry(QRect(QPoint(x, heights[column]), QSize(column_width, height)))
            heights[column] += height + self._gap
        used = max(heights) - top
        return used - (self._gap if used else 0) + margins.top() + margins.bottom()
