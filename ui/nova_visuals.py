"""Was an Nova nur Bild ist: ihr Symbol, ihr Auftritt und der Hintergrund des Classic-Aussehens."""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QAbstractAnimation, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

ORB_SIZE = 34
ORB_TICK = 50
IDLE_STEP, BUSY_STEP = 3.0, 12.0  # Grad je Takt: eine Runde in 6 Sekunden, beim Nachdenken schneller
SPAWN_MS = 1100
SPAWN_DELAY = 0.2  # Anteil, in dem Nova noch hereinrutscht
SPARKS = 14
REACH = 300  # bis hierher reicht der Auftritt um das Symbol
CORNER = 16
AURORA_ALPHA = 0.2
STARS = 90


def fade(color: str, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(max(0.0, min(1.0, alpha)))
    return result


def spawn_scale(phase: float) -> float:
    """Wächst aus dem Nichts, schießt über das Ziel hinaus und federt zurück."""
    if phase < 0.45:
        return 1.2 * phase / 0.45
    if phase < 0.7:
        return 1.2 - 0.26 * (phase - 0.45) / 0.25
    return min(1.0, 0.94 + 0.06 * (phase - 0.7) / 0.3)


NOVA_COLORS = {"accent": "#8b6cff", "b2": "#00b7ff", "b3": "#ff3fa4"}


def paint_orb(painter: QPainter, colors: dict, angle: float) -> None:
    """Ring, Kern und kreisender Punkt, um den Ursprung des Malers."""
    painter.setPen(QPen(fade(colors["accent"], 0.8), 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(0, 0), 11, 11)
    core = QRadialGradient(QPointF(0, 0), 7, QPointF(-2, -2.5))
    core.setColorAt(0, QColor("#ffffff"))
    core.setColorAt(0.55, QColor(colors["accent"]))
    core.setColorAt(1, QColor(colors["b3"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(core)
    painter.drawEllipse(QPointF(0, 0), 7, 7)
    painter.rotate(angle)
    painter.setBrush(QColor(colors["b2"]))
    painter.drawEllipse(QPointF(0, -11), 2.5, 2.5)


class NovaOrb(QWidget):
    """Der Ring mit dem Kern und dem kreisenden Punkt. Er läuft nur, solange man ihn sieht."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(ORB_SIZE, ORB_SIZE)
        self.colors = dict(NOVA_COLORS)
        self.busy = False
        self.phase = 1.0
        self.angle = 0.0
        self._timer = QTimer(self, interval=ORB_TICK)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self.angle = (self.angle + (BUSY_STEP if self.busy else IDLE_STEP)) % 360
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(ORB_SIZE / 2, ORB_SIZE / 2)
        painter.scale(*[spawn_scale(self.phase)] * 2)
        painter.rotate(-220 * (1 - self.phase))
        paint_orb(painter, self.colors, self.angle)


class NovaSpawn(QWidget):
    """Lichtblitz, Schockwelle und Funken um Novas Symbol, wenn sie erscheint."""

    def __init__(self, parent: QWidget, orb: NovaOrb):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.orb = orb
        self.colors = orb.colors
        self.value = 0.0
        pick = random.Random(7)
        self.sparks = [(pick.uniform(0, math.tau), pick.uniform(60, 190), pick.uniform(3.5, 6), pick.randrange(4))
                       for _ in range(SPARKS)]
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(SPAWN_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.hide)
        self.hide()

    @property
    def running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def start(self) -> None:
        self.orb.phase = 0.0
        self.show()
        self._animation.stop()
        self._animation.start()

    def _origin(self) -> QPointF:
        return QPointF(self.orb.mapTo(self.parentWidget(), self.orb.rect().center()))

    def _step(self, value: float) -> None:
        self.value = value
        self.orb.phase = phase = max(0.0, (value - SPAWN_DELAY) / (1 - SPAWN_DELAY))
        self.orb.update()
        origin = self._origin().toPoint()
        self.update(QRect(origin.x() - REACH, origin.y() - REACH, 2 * REACH, 2 * REACH) if phase < 1 else self.rect())

    def paintEvent(self, _event) -> None:
        phase = max(0.0, (self.value - SPAWN_DELAY) / (1 - SPAWN_DELAY))
        if phase <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        edge = QPainterPath()
        edge.addRoundedRect(QRectF(self.rect()), CORNER, CORNER)
        painter.setClipPath(edge)
        origin = self._origin()
        self._flash(painter, origin, phase)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(fade(self.colors["accent"], 0.9 * (1 - phase)), 3 - 2 * phase))
        radius = 16 + 150 * (1 - (1 - phase) ** 2)
        painter.drawEllipse(origin, radius, radius)
        self._sparks(painter, origin, phase)

    def _flash(self, painter: QPainter, origin: QPointF, phase: float) -> None:
        strength = 0.55 * (phase / 0.18 if phase < 0.18 else (1 - phase) / 0.82)
        light = QRadialGradient(origin, REACH)
        light.setColorAt(0, fade(self.colors["accent"], strength))
        light.setColorAt(1, fade(self.colors["accent"], 0))
        painter.fillRect(self.rect(), light)

    def _sparks(self, painter: QPainter, origin: QPointF, phase: float) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        tones = (self.colors["accent"], self.colors["b2"], self.colors["b3"], "#ffe9a0")
        travel = 1 - (1 - phase) ** 3
        for angle, distance, size, tone in self.sparks:
            painter.save()
            painter.translate(origin + QPointF(math.cos(angle), math.sin(angle)) * distance * travel)
            painter.rotate(200 * phase)
            painter.setBrush(fade(tones[tone], 1 - phase))
            arm, waist = size * (0.5 + phase), size * 0.25
            painter.drawPolygon(QPolygonF([QPointF(0, -arm), QPointF(waist, -waist), QPointF(arm, 0), QPointF(waist, waist),
                                           QPointF(0, arm), QPointF(-waist, waist), QPointF(-arm, 0), QPointF(-waist, -waist)]))
            painter.restore()


def classic_backdrop(size: QSize, ratio: float, colors: dict) -> QPixmap:
    """Tiefer Raum mit Nordlicht, Sternen und haarfeiner Farbkante. Einmal gemalt, damit Nova im Leerlauf nichts kostet."""
    pixmap = QPixmap(size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    width, height = size.width(), size.height()
    frame = QRectF(0.5, 0.5, width - 1, height - 1)
    shape = QPainterPath()
    shape.addRoundedRect(frame, CORNER, CORNER)
    painter.fillPath(shape, QColor(colors["bg"]))
    painter.setClipPath(shape)
    for key, x, y in (("b1", 0.1, 0.05), ("b2", 1.0, 0.45), ("b3", 0.3, 1.0)):
        light = QRadialGradient(QPointF(width * x, height * y), height * 0.42)
        light.setColorAt(0, fade(colors[key], AURORA_ALPHA))
        light.setColorAt(1, fade(colors[key], 0))
        painter.fillRect(QRectF(0, 0, width, height), light)
    if QColor(colors["bg"]).lightness() < 128:
        pick = random.Random(11)
        painter.setPen(Qt.PenStyle.NoPen)
        for _ in range(STARS):
            painter.setBrush(fade("#ffffff", pick.uniform(0.15, 0.7)))
            painter.drawEllipse(QPointF(pick.uniform(0, width), pick.uniform(0, height)), *[pick.uniform(0.4, 1.0)] * 2)
    painter.setClipping(False)
    border = QLinearGradient(0, 0, width, height)
    border.setColorAt(0, fade(colors["accent"], 0.6))
    border.setColorAt(0.45, fade(colors["b2"], 0.12))
    border.setColorAt(1, fade(colors["b3"], 0.4))
    painter.setPen(QPen(border, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shape)
    painter.end()
    return pixmap
