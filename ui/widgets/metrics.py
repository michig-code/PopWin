"""Einzelne Messwerte: CPU, GPU, RAM, Netz - als Ring, Balken, Kurve oder Zahl."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

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

    # --- [Antigravity Start: Gauge dynamisch an Fenstergröße anpassen (Rest: Claude Code)] ---
    MIN_GAUGE_HEIGHT = 30

    def __init__(self, owner: DeskletWindow, default_height: int = 80, show_text: bool = True):
        super().__init__()
        self.owner = owner
        self.default_height = default_height
        self.show_text = show_text
        self.value: float | None = None
        self.text = "–"
        self.history: deque[float] = deque(maxlen=60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(self.MIN_GAUGE_HEIGHT)

    def sizeHint(self) -> QSize:
        return QSize(self.owner.DEFAULT_WIDTH, self.default_height)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, self.MIN_GAUGE_HEIGHT)
    # --- [Antigravity Ende] ---

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
        if self.show_text:
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
    DEFAULT_GAUGE_HEIGHT = 110  # [Antigravity]
    # [Antigravity: 'height' gehört als gemeinsame Option dem Fenster (wie bei NotesWidget)]
    OPTIONS = (
        Option("style", "Darstellung", "choice", "ring", choices=STYLES),
        Option("value_size", "Schriftgröße des Werts", "int", 20, 10, 48),
    )

    def build(self) -> None:
        self.add_title()
        self.gauge = Gauge(self, self.DEFAULT_GAUGE_HEIGHT)
        self.body.addWidget(self.gauge, 1)  # [Antigravity: stretch=1 füllt den Raum]
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def options_changed(self) -> None:
        pass  # [Antigravity: Höhe wird über Fenster-Option 'height' und Layout geregelt]

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
    DEFAULT_GAUGE_HEIGHT = 70  # [Antigravity]
    # [Antigravity: 'height' gehört als gemeinsame Option dem Fenster (wie bei NotesWidget)]
    OPTIONS = (
        Option("style", "Darstellung", "choice", "graph", choices={"graph": "Verlauf", "number": "Nur Zahl"}),
        Option("value_size", "Schriftgröße des Werts", "int", 12, 9, 28),
        Option("show_upload", "Upload anzeigen", "bool", True),
    )

    # --- [Antigravity Start: Download- & Upload-Anzeige sauber unten platziert (Rest: Claude Code)] ---
    def build(self) -> None:
        self.add_title()
        self.gauge = Gauge(self, self.DEFAULT_GAUGE_HEIGHT, show_text=False)
        self.download = QLabel()
        self.upload = QLabel(objectName="Muted")
        self.body.addWidget(self.gauge, 1)

        rates_row = QHBoxLayout()
        rates_row.setContentsMargins(0, 0, 0, 0)
        rates_row.setSpacing(12)
        rates_row.addWidget(self.download)
        rates_row.addWidget(self.upload)
        rates_row.addStretch(1)
        self.body.addLayout(rates_row)

        self._peak = 1024.0
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def options_changed(self) -> None:
        is_graph = self.option("style") == "graph"
        self.gauge.setVisible(is_graph)
        self.upload.setVisible(self.option("show_upload"))
        font_size = self.option("value_size")
        self.download.setStyleSheet(f"font-size: {font_size}px; font-weight: bold;")
        self.upload.setStyleSheet(f"font-size: {font_size}px;")

    def update_status(self, state: dict) -> None:
        down = state.get("net_down", 0.0)
        up = state.get("net_up", 0.0)
        self._peak = max(self._peak * 0.98, down, 1024.0)  # Skala folgt langsam dem Spitzenwert
        self.gauge.set_value(down / self._peak * 100, "")
        self.download.setText(f"↓ {format_speed(down)}")
        self.upload.setText(f"↑ {format_speed(up)}")
    # --- [Antigravity Ende: Download- & Upload-Anzeige sauber unten platziert] ---
