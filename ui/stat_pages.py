"""Diagramme für Netzwerk und Performance in den Einstellungen."""
from __future__ import annotations

from datetime import datetime

import psutil
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core import cleanup, i18n, win_api
from core.paths import app_shortcut

CHART_HEIGHT = 180
RAM_CHECK_MS = 2500
MARGIN_LEFT, MARGIN_TOP, MARGIN_BOTTOM = 56, 10, 22
PERFORMANCE_MS = 5000
DOWN_COLOR, UP_COLOR, MEMORY_COLOR = "#48B9C7", "#F18A00", "#8B6CFF"


class LineChart(QWidget):
    """Eine oder mehrere Linien mit einfacher Skala. Bewusst schlicht: Es soll den Verlauf zeigen, keine Tabelle ersetzen."""

    def __init__(self, unit: str):
        super().__init__()
        self.unit = unit
        self.lines: list[tuple[list[float], str]] = []
        self.setFixedHeight(CHART_HEIGHT)

    def set_lines(self, lines: list[tuple[list[float], str]]) -> None:
        self.lines = lines
        self.update()

    def top_value(self) -> float:
        peak = max((max(values) for values, _color in self.lines if values), default=1.0)
        return max(peak * 1.15, 1.0)

    def point(self, index: int, count: int, value: float, top: float, area: QRectF) -> QPointF:
        x = area.left() + (area.width() * index / (count - 1) if count > 1 else area.width() / 2)
        return QPointF(x, area.bottom() - area.height() * value / top)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(MARGIN_LEFT, MARGIN_TOP, self.width() - MARGIN_LEFT - 8, self.height() - MARGIN_TOP - MARGIN_BOTTOM)
        text = self.palette().text().color()
        grid = QColor(text)
        grid.setAlphaF(0.18)
        top = self.top_value()
        for step in range(5):
            y = area.bottom() - area.height() * step / 4
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(text)
            painter.drawText(QRectF(0, y - 9, MARGIN_LEFT - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{top * step / 4:.0f} {self.unit}")
        for values, color in self.lines:
            if not values:
                continue
            path = QPainterPath(self.point(0, len(values), values[0], top, area))
            for index, value in enumerate(values[1:], 1):
                path.lineTo(self.point(index, len(values), value, top, area))
            painter.setPen(QPen(QColor(color), 2))
            painter.drawPath(path)
            if len(values) == 1:
                painter.setBrush(QColor(color))
                painter.drawEllipse(path.currentPosition(), 3, 3)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.end()


class NetworkStats(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.history = ctx.speed
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.start = QPushButton("Speedtest starten", objectName="Primary")
        self.start.clicked.connect(self._run)
        reset = QPushButton("Statistik zurücksetzen", objectName="Secondary")
        reset.clicked.connect(self._reset)
        row.addWidget(self.start)
        row.addWidget(reset)
        row.addStretch(1)
        box.addLayout(row)
        self.result = QLabel("Noch keine Messung.", objectName="Muted")
        self.result.setWordWrap(True)
        box.addWidget(self.result)
        self.chart = LineChart("Mbit")
        box.addWidget(self.chart)
        self.table = QTreeWidget()
        self.table.setColumnCount(4)
        self.table.setHeaderLabels(["Zeitpunkt", "Download", "Upload", "Ping"])
        self.table.setRootIsDecorated(False)
        self.table.setColumnWidth(0, 170)
        self.table.setMinimumHeight(170)
        box.addWidget(self.table)
        self.history.changed.connect(self.refresh)
        self.history.stage.connect(self.result.setText)
        self.history.failed.connect(self._failed)
        self.refresh()

    def _run(self) -> None:
        self.start.setEnabled(False)
        self.result.setText("Startet …")
        self.history.run()

    def _failed(self, error: str) -> None:
        self.start.setEnabled(True)
        self.result.setText(f"Das hat nicht geklappt: {error}")

    def _reset(self) -> None:
        if QMessageBox.question(self, "PopWin", "Alle Messungen löschen?") == QMessageBox.StandardButton.Yes:
            self.history.reset()

    def refresh(self) -> None:
        self.start.setEnabled(True)
        entries = self.history.entries
        self.chart.set_lines([([entry["down"] for entry in entries], DOWN_COLOR), ([entry["up"] for entry in entries], UP_COLOR)])
        self.table.clear()
        for entry in reversed(entries):
            moment = datetime.fromisoformat(entry["time"]).strftime("%d.%m.%Y %H:%M")
            self.table.addTopLevelItem(QTreeWidgetItem([moment, f"{entry['down']} Mbit/s", f"{entry['up']} Mbit/s", f"{entry['ping']} ms"]))
        if entries:
            last = entries[-1]
            self.result.setText(f"Download {last['down']} Mbit/s, Upload {last['up']} Mbit/s, Ping {last['ping']} ms")
        else:
            self.result.setText("Noch keine Messung.")


def gigabytes(size: int) -> str:
    return f"{size / 1024 ** 3:.2f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"


class CachePanel(QWidget):
    """Löscht nur auf Knopfdruck und sagt vorher, um wie viel es geht."""

    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.files = QPushButton("Datei-Cache leeren", objectName="Secondary")
        self.files.clicked.connect(self._scan_files)
        self.memory = QPushButton("RAM-Cache leeren", objectName="Secondary")
        self.memory.clicked.connect(self._purge_memory)
        row.addWidget(self.files)
        row.addWidget(self.memory)
        row.addStretch(1)
        box.addLayout(row)
        self.note = QLabel(objectName="Muted")
        self.note.setWordWrap(True)
        box.addWidget(self.note)
        self.job: cleanup.CacheJob | None = None
        self._free_before = 0
        self._check = QTimer(self, singleShot=True, interval=RAM_CHECK_MS)
        self._check.timeout.connect(self._show_memory)

    def _run(self, work, handler) -> None:
        self.files.setEnabled(False)
        self.job = cleanup.CacheJob(work, self)
        self.job.finished_with.connect(handler)
        self.job.start()

    def _idle(self) -> None:
        self.job = None
        self.files.setEnabled(True)

    def _scan_files(self) -> None:
        self.note.setText("Temporäre Dateien werden gezählt …")
        self._run(lambda: cleanup.scan(cleanup.temp_folders()), self._ask_files)

    def _ask_files(self, found: tuple) -> None:
        self._idle()
        count, size = found
        if not count:
            self.note.setText("Keine alten temporären Dateien gefunden.")
            return
        question = f"{count} temporäre Dateien mit {gigabytes(size)} löschen? Sie sind älter als ein Tag."
        if QMessageBox.question(self, "PopWin", i18n.translate(question)) != QMessageBox.StandardButton.Yes:
            self.note.setText("")
            return
        self.note.setText("Wird geleert …")
        self._run(lambda: cleanup.clean(cleanup.temp_folders()), self._files_done)

    def _files_done(self, result: tuple) -> None:
        self._idle()
        removed, size, skipped = result
        self.note.setText(f"{removed} Dateien gelöscht, {gigabytes(size)} frei. {skipped} waren in Benutzung und bleiben.")

    def _purge_memory(self) -> None:
        program, arguments = app_shortcut("cleanup")
        self._free_before = psutil.virtual_memory().free
        if win_api.run_elevated(program, arguments):
            self.note.setText("Wird geleert …")
            self._check.start()
        else:
            self.note.setText("Ohne Administratorrechte lässt sich der RAM-Cache nicht leeren.")

    def _show_memory(self) -> None:
        gained = max(0, psutil.virtual_memory().free - self._free_before)
        self.note.setText(f"Frei gewordener Arbeitsspeicher: {gigabytes(gained)}")


class PerformanceStats(QWidget):
    """Zeichnet nur, solange die Seite offen ist; die Werte sammelt PopWin im Hintergrund."""

    def __init__(self, ctx):
        super().__init__()
        self.log = ctx.status.performance
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.total = QLabel(objectName="PanelTitle")
        box.addWidget(self.total)
        self.chart = LineChart("MB")
        box.addWidget(self.chart)
        self.parts = QTreeWidget()
        self.parts.setColumnCount(2)
        self.parts.setHeaderLabels(["Teil von PopWin", "Arbeitsspeicher"])
        self.parts.setRootIsDecorated(False)
        self.parts.setColumnWidth(0, 240)
        box.addWidget(self.parts)
        self.timer = QTimer(self, interval=PERFORMANCE_MS)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        points = list(self.log.points)
        self.total.setText(f"PopWin braucht gerade {points[-1]:.0f} MB" if points else "Wird gemessen …")
        self.chart.set_lines([(points, MEMORY_COLOR)])
        self.parts.clear()
        for name, size in sorted(self.log.parts.items(), key=lambda part: -part[1]):
            self.parts.addTopLevelItem(QTreeWidgetItem([name, f"{size:.0f} MB"]))
