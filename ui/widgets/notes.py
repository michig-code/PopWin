"""Notizzettel: speichert automatisch kurz nach der letzten Änderung."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPlainTextEdit

from ui.base import G
from ui.widgets.base import DeskletWindow, Option

QWIDGETSIZE_MAX = 16777215


class NotesWidget(DeskletWindow):
    TITLE = "Notizen"
    GLYPH = G.NOTE
    DESCRIPTION = "Schneller Notizzettel"
    DEFAULT_WIDTH = 260
    TEXT_HEIGHT = 150  # ohne feste Fensterhöhe so hoch
    MIN_TEXT = 40
    # Die Höhe gehört dem Fenster (gemeinsame Option, auch der Griff unten rechts). Früher hatte die Notiz eine eigene
    # Option gleichen Namens fürs Textfeld; beide zogen sich gegenseitig hoch, und das Fenster wuchs beim Ziehen immer weiter.
    OPTIONS = (
        Option("font", "Schriftgröße", "int", 13, 10, 28),
    )

    def build(self) -> None:
        self.add_title()
        self.edit = QPlainTextEdit(self.spec.get("data", {}).get("text", ""))
        self.edit.setPlaceholderText("Notiz schreiben …")
        self.body.addWidget(self.edit)
        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(self._save)
        self.edit.textChanged.connect(self._save_timer.start)

    def _stretch(self) -> None:
        """Das Textfeld füllt, was das Fenster hergibt."""
        self.edit.setMinimumHeight(self.MIN_TEXT)
        self.edit.setMaximumHeight(QWIDGETSIZE_MAX)

    def options_changed(self) -> None:
        if self.option("height"):
            self._stretch()
        else:
            self.edit.setFixedHeight(self.TEXT_HEIGHT)
        self.edit.setStyleSheet(f"font-size: {self.option('font')}px;")

    def preview_size(self, width: int, height: int) -> None:
        self._stretch()  # sonst hielte die alte feste Höhe das Fenster beim Ziehen fest
        super().preview_size(width, height)

    def _save(self) -> None:
        self.ctx.widgets.update_spec(self.spec["id"], data={"text": self.edit.toPlainText()})

    def closeEvent(self, event) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        super().closeEvent(event)
