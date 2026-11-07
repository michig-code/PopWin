"""Leisten-Designer für die Einstellungen: eine kleine Leiste wie in echt. Klick schaltet ein Symbol an oder aus,
Ziehen ändert die Reihenfolge, alles gleitet weich an seinen Platz. Wie der Uhr-Platz-Wähler, nur für die Symbole."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

SLOT = 40  # Breite eines Symbols in der Vorschau
GLIDE_MS = 160


@dataclass
class Item:
    key: str
    label: str
    glyph: str
    on: bool
    fixed: bool = False  # nicht abschaltbar (z. B. die App-Icons der Taskbar)
    wide: int = 1  # so viele Plätze breit


class BarDesigner(QWidget):
    toggled = Signal(str, bool)
    reordered = Signal(list)

    def __init__(self, items: list[Item], dark: bool = True, accent: str = "#8B5CF6"):
        super().__init__()
        self.items = items
        self.dark = dark
        self.accent = QColor(accent)
        self.setMouseTracking(True)
        self.setMinimumHeight(96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press: tuple[str, QPointF] | None = None
        self._drag = ""
        self._drag_x = 0.0
        self._hover = ""
        self._shown_x: dict[str, float] = {}  # aktuell gezeichnete Position, gleitet zur Zielposition
        self._from: dict[str, float] = {}
        self.glide = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=GLIDE_MS)
        self.glide.valueChanged.connect(self._step)
        self._targets()
        self._shown_x = dict(self._target)

    # --- Geometrie -----------------------------------------------------------
    def bar_rect(self) -> QRectF:
        width = sum(item.wide for item in self.items) * SLOT + 24
        left = max(8.0, (self.width() - width) / 2)
        return QRectF(left, 18, min(width, self.width() - 16), 44)

    def _targets(self) -> None:
        bar = self.bar_rect()
        x = bar.left() + 12
        self._target = {}
        for item in self.items:
            self._target[item.key] = x
            x += item.wide * SLOT

    def item_at(self, x: float) -> str:
        for item in self.items:
            left = self._shown_x.get(item.key, 0)
            if left <= x < left + item.wide * SLOT:
                return item.key
        return ""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._targets()
        self._shown_x = dict(self._target)

    def _animate(self) -> None:
        self._targets()
        self._from = dict(self._shown_x)
        self.glide.stop()
        self.glide.start()

    def _step(self, value) -> None:
        value = float(value)
        for key, target in self._target.items():
            if key == self._drag:
                continue
            start = self._from.get(key, target)
            self._shown_x[key] = start + (target - start) * (1 - (1 - value) ** 3)
        self.update()

    # --- Maus ----------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            key = self.item_at(event.position().x())
            if key:
                self._press = (key, event.position())

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        if self._press and not self._drag and (point - self._press[1]).manhattanLength() >= QApplication.startDragDistance():
            self._drag = self._press[0]
            self._grab = point.x() - self._shown_x[self._drag]
        if self._drag:
            self._drag_x = point.x() - self._grab
            self._shown_x[self._drag] = self._drag_x
            self._move_dragged()
            self.update()
            return
        hover = self.item_at(point.x()) if self.bar_rect().contains(point) else ""
        if hover != self._hover:
            self._hover = hover
            label = next((item.label for item in self.items if item.key == hover), "")
            self.setToolTip(label)
            self.update()

    def _move_dragged(self) -> None:
        dragged = next(item for item in self.items if item.key == self._drag)
        center = self._drag_x + dragged.wide * SLOT / 2
        others = [item for item in self.items if item.key != self._drag]
        index = sum(self._target[item.key] + item.wide * SLOT / 2 < center for item in others)
        order = others[:index] + [dragged] + others[index:]
        if [item.key for item in order] != [item.key for item in self.items]:
            self.items = order
            self._animate()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag:
            self._drag = ""
            self._animate()
            self.reordered.emit([item.key for item in self.items])
        elif self._press:
            item = next((item for item in self.items if item.key == self._press[0]), None)
            if item is not None and not item.fixed:
                item.on = not item.on
                self.toggled.emit(item.key, item.on)
                self.update()
        self._press = None

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover = ""
        self.update()

    # --- Zeichnen -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = QColor(255, 255, 255) if self.dark else QColor(20, 23, 29)
        bar = self.bar_rect()
        painter.setPen(QPen(QColor(ink.red(), ink.green(), ink.blue(), 40), 1))
        painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 18))
        painter.drawRoundedRect(bar, 14, 14)
        font = QFont("Segoe Fluent Icons")
        font.setPixelSize(17)
        for item in sorted(self.items, key=lambda i: i.key == self._drag):  # das gezogene obenauf
            x = self._shown_x.get(item.key, 0)
            cell = QRectF(x + 3, bar.top() + 5, item.wide * SLOT - 6, bar.height() - 10)
            lifted = item.key == self._drag
            if lifted:
                cell.translate(0, -6)
            if item.key == self._hover or lifted:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 36))
                painter.drawRoundedRect(cell, 9, 9)
            if lifted:
                painter.setPen(QPen(self.accent, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(cell, 9, 9)
            painter.setPen(QColor(ink.red(), ink.green(), ink.blue(), 235 if item.on else 70))
            if item.wide > 1:  # Platzhalter für mehrere App-Icons
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 120))
                for number in range(item.wide):
                    painter.drawRoundedRect(QRectF(cell.left() + number * SLOT + 8, cell.top() + 7, SLOT - 22, cell.height() - 14), 5, 5)
            else:
                painter.setFont(font)
                painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, item.glyph)
            if not item.on:  # durchgestrichen: ausgeschaltet
                painter.setPen(QPen(QColor(229, 72, 77, 200), 2))
                painter.drawLine(QPointF(cell.left() + 9, cell.bottom() - 8), QPointF(cell.right() - 9, cell.top() + 8))
        painter.setPen(QColor(ink.red(), ink.green(), ink.blue(), 140))
        small = QFont(self.font())
        small.setPixelSize(11)
        painter.setFont(small)
        painter.drawText(QRectF(0, bar.bottom() + 8, self.width(), 18), Qt.AlignmentFlag.AlignCenter,
                         "Klick: an/aus  ·  Ziehen: Reihenfolge")
