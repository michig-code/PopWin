"""Große Uhr."""
from __future__ import annotations

from PySide6.QtCore import QDateTime, QLocale, Qt, QTimer
from PySide6.QtWidgets import QLabel

from ui.base import G
from ui.widgets.base import DeskletWindow, Option


class ClockWidget(DeskletWindow):
    TITLE = "Uhr"
    GLYPH = G.CLOCK
    DESCRIPTION = "Große Uhrzeit mit Datum"
    DEFAULT_WIDTH = 220
    OPTIONS = (
        Option("size", "Schriftgröße", "int", 40, 18, 110),
        Option("seconds", "Sekunden anzeigen", "bool", False),
        Option("show_date", "Datum anzeigen", "bool", True),
    )

    def build(self) -> None:
        self.time = QLabel()
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date = QLabel(objectName="Muted")
        self.date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.time)
        self.body.addWidget(self.date)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def options_changed(self) -> None:
        color = f"color: {self.option('accent')};" if self.option("accent") else ""
        self.time.setStyleSheet(f"font-size: {self.option('size')}px; font-weight: 600; {color}")
        self.date.setVisible(self.option("show_date"))
        self._tick()

    def _tick(self) -> None:
        now = QDateTime.currentDateTime()
        self.time.setText(now.toString("HH:mm:ss" if self.option("seconds") else "HH:mm"))
        self.date.setText(QLocale.system().toString(now, "dddd, dd.MM.yyyy"))
