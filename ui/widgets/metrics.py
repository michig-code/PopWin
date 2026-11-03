"""Einzelne Messwerte: CPU, GPU, RAM, Netz - als Ring, Balken, Kurve oder Zahl."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from ui.base import G
from ui.widgets.base import DeskletWindow, Option

STYLES = {"ring": "Ring", "bar": "Balken", "graph": "Verlauf", "number": "Nur Zahl"}


def format_speed(bytes_per_second: float) -> str:
    value = float(bytes_per_second)
    for unit in ("B/s", "KB/s", "MB/s"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{value:.1f} GB/s".replace(".", ",")


class Gauge(QWidget):
    """Zeichnet einen Wert von 0 bis 100 im gewählten Stil."""

    def __init__(self, owner: DeskletWindow):
        super().__init__()
        self.owner = owner
        self.value: float | None = None
        self.text = "–"
        self.history: deque[float] = deque(maxlen=60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: float | None, text: str | None = None) -> None:
        self.value = value
        self.text = text or ("–" if value is None else f"{value:.0f} %")
        if value is not None:
            self.history.append(value)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        style = self.owner.option("style")
        accent = self.owner.accent()
        base = QColor(128, 128, 128, 70)
        text_color = QColor("#FFFFFF" if self.owner.ctx.theme.mode == "dark" else "#202020")
        area = QRectF(self.rect())
        value = self.value or 0.0
        if style == "ring":
            side = min(area.width(), area.height()) - 10
            ring = QRectF((area.width() - side) / 2, (area.height() - side) / 2, side, side)
            pen = QPen(base, 8, c=Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, 225 * 16, -270 * 16)
            pen.setColor(accent)
            painter.setPen(pen)
            painter.drawArc(ring, 225 * 16, round(-270 * 16 * value / 100))
        elif style == "bar":
            track = QRectF(0, area.height() - 12, area.width(), 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base)
            painter.drawRoundedRect(track, 4, 4)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(0, track.y(), track.width() * value / 100, 8), 4, 4)
        elif style == "graph":
            self._draw_graph(painter, area, accent)
        font = QFont(painter.font())
        font.setPixelSize(int(self.owner.option("value_size")))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        target = area.adjusted(0, 0, 0, -14) if style == "bar" else area
        painter.drawText(target, Qt.AlignmentFlag.AlignCenter, self.text)
        painter.end()

    def _draw_graph(self, painter: QPainter, area: QRectF, accent: QColor) -> None:
        points = list(self.history)
        if len(points) < 2:
            return
        top = max(100.0, max(points))
        step = area.width() / (self.history.maxlen - 1)
        start = area.width() - step * (len(points) - 1)
        path = QPainterPath()
        for index, value in enumerate(points):
            point = QPointF(start + index * step, area.height() - 2 - (area.height() - 4) * value / top)
            path.lineTo(point) if index else path.moveTo(point)
        fill = QPainterPath(path)
        fill.lineTo(area.width(), area.height())
        fill.lineTo(start, area.height())
        fill.closeSubpath()
        shade = QColor(accent)
        shade.setAlpha(50)
        painter.fillPath(fill, shade)
        painter.setPen(QPen(accent, 2))
        painter.drawPath(path)


class MetricWidget(DeskletWindow):
    STATE_KEY = ""
    DEFAULT_WIDTH = 180
    OPTIONS = (
        Option("style", "Darstellung", "choice", "ring", choices=STYLES),
        Option("height", "Höhe der Anzeige", "int", 110, 50, 260),
        Option("value_size", "Schriftgröße des Werts", "int", 20, 10, 48),
    )

    def build(self) -> None:
        self.add_title()
        self.gauge = Gauge(self)
        self.body.addWidget(self.gauge)
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def options_changed(self) -> None:
        self.gauge.setFixedHeight(self.option("height"))

    def update_status(self, state: dict) -> None:
        self.gauge.set_value(state.get(self.STATE_KEY))


class CpuWidget(MetricWidget):
    TITLE = "CPU"
    GLYPH = G.CPU
    DESCRIPTION = "Prozessor-Auslastung"
    STATE_KEY = "cpu"


class GpuWidget(MetricWidget):
    TITLE = "GPU"
    GLYPH = G.GPU
    DESCRIPTION = "Grafikkarten-Auslastung"
    STATE_KEY = "gpu"


class RamWidget(MetricWidget):
    TITLE = "RAM"
    GLYPH = G.MEMORY
    DESCRIPTION = "Arbeitsspeicher-Belegung"
    STATE_KEY = "ram"
    OPTIONS = (*MetricWidget.OPTIONS, Option("show_gb", "Belegung in GB anzeigen", "bool", False))

    def update_status(self, state: dict) -> None:
        if self.option("show_gb") and state["ram_total"]:
            self.gauge.set_value(state["ram"], f"{state['ram_used'] / 1024 ** 3:.1f} GB".replace(".", ","))
        else:
            self.gauge.set_value(state["ram"])


class NetworkWidget(DeskletWindow):
    TITLE = "Netz-Tempo"
    GLYPH = G.NETWORK
    DESCRIPTION = "Download und Upload live"
    DEFAULT_WIDTH = 220
    OPTIONS = (
        Option("style", "Darstellung", "choice", "graph", choices={"graph": "Verlauf", "number": "Nur Zahl"}),
        Option("height", "Höhe der Anzeige", "int", 70, 40, 200),
        Option("value_size", "Schriftgröße des Werts", "int", 12, 9, 28),
        Option("show_upload", "Upload anzeigen", "bool", True),
    )

    def build(self) -> None:
        self.add_title()
        self.gauge = Gauge(self)
        self.upload = QLabel(objectName="Muted")
        self.body.addWidget(self.gauge)
        self.body.addWidget(self.upload)
        self._peak = 1024.0
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def options_changed(self) -> None:
        self.gauge.setFixedHeight(self.option("height"))
        self.upload.setVisible(self.option("show_upload"))

    def update_status(self, state: dict) -> None:
        down = state["net_down"]
        self._peak = max(self._peak * 0.98, down, 1024.0)  # Skala folgt langsam dem Spitzenwert
        self.gauge.set_value(down / self._peak * 100, f"↓ {format_speed(down)}")
        self.upload.setText(f"↑ {format_speed(state['net_up'])}")
