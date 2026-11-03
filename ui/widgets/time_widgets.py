"""Datum und Stoppuhr."""
from __future__ import annotations

import time

from PySide6.QtCore import QDate, QLocale, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from ui.base import G
from ui.widgets.base import DeskletWindow, Option


def format_stopwatch(seconds: float, show_hundredths: bool = True) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
    return f"{text},{int((seconds - whole) * 100):02d}" if show_hundredths else text


class DateWidget(DeskletWindow):
    TITLE = "Datum"
    GLYPH = G.CALENDAR
    DESCRIPTION = "Wochentag und Datum groß"
    DEFAULT_WIDTH = 180
    OPTIONS = (
        Option("day_size", "Größe des Tages", "int", 52, 24, 110),
        Option("week", "Kalenderwoche anzeigen", "bool", True),
    )

    def build(self) -> None:
        self.weekday = QLabel(objectName="Muted")
        self.day = QLabel()
        self.month = QLabel(objectName="MediaTitle")
        self.week = QLabel(objectName="Muted")
        for label in (self.weekday, self.day, self.month, self.week):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.body.addWidget(label)
        self.timer = QTimer(self, interval=60000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    def options_changed(self) -> None:
        self.day.setStyleSheet(f"font-size: {self.option('day_size')}px; font-weight: 600; color: {self.accent().name()};")
        self.week.setVisible(self.option("week"))

    def _tick(self) -> None:
        today = QDate.currentDate()
        locale = QLocale.system()
        self.weekday.setText(locale.toString(today, "dddd"))
        self.day.setText(str(today.day()))
        self.month.setText(locale.toString(today, "MMMM yyyy"))
        self.week.setText(f"KW {today.weekNumber()[0]}")


class StopwatchWidget(DeskletWindow):
    TITLE = "Stoppuhr"
    GLYPH = G.STOPWATCH
    DESCRIPTION = "Starten, anhalten, zurücksetzen"
    DEFAULT_WIDTH = 220
    OPTIONS = (Option("hundredths", "Hundertstel anzeigen", "bool", True),)

    def build(self) -> None:
        self.add_title()
        self._started = 0.0
        self._elapsed = 0.0
        self.value = QLabel(objectName="BigValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.value)
        row = QHBoxLayout()
        self.toggle = QPushButton("Start", objectName="Secondary")
        self.toggle.clicked.connect(self._toggle)
        reset = QPushButton("Zurücksetzen", objectName="Secondary")
        reset.clicked.connect(self._reset)
        row.addWidget(self.toggle)
        row.addWidget(reset)
        self.body.addLayout(row)
        self.timer = QTimer(self, interval=47)
        self.timer.timeout.connect(self._show)

    def options_changed(self) -> None:
        self._show()

    def _current(self) -> float:
        running = time.monotonic() - self._started if self.timer.isActive() else 0.0
        return self._elapsed + running

    def _show(self) -> None:
        self.value.setText(format_stopwatch(self._current(), self.option("hundredths")))

    def _toggle(self) -> None:
        if self.timer.isActive():
            self._elapsed = self._current()
            self.timer.stop()
            self.toggle.setText("Weiter")
        else:
            self._started = time.monotonic()
            self.timer.start()
            self.toggle.setText("Anhalten")
        self._show()

    def _reset(self) -> None:
        self.timer.stop()
        self._elapsed = 0.0
        self.toggle.setText("Start")
        self._show()
