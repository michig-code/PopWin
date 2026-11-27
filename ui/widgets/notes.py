"""Notizzettel: speichert automatisch kurz nach der letzten Änderung."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy

from ui.base import G
from ui.widgets.base import MAX_HEIGHT, DeskletWindow, Option


class NotesWidget(DeskletWindow):
    TITLE = "Notizen"
    GLYPH = G.NOTE
    DESCRIPTION = "Schneller Notizzettel"
    DEFAULT_WIDTH = 260
    DEFAULT_HEIGHT = 220
    EDITOR_MIN_HEIGHT = 48
    # Früher galt options.height als Höhe des Textfelds, jetzt als Höhe des ganzen Widgets.
    # Titel + Ränder + Abstand kommen bei alten Notizen einmalig dazu, damit sie nicht kleiner werden.
    LAYOUT_VERSION = 2
    LEGACY_CHROME_HEIGHT = 56
    OPTIONS = (
        Option("font", "Schriftgröße", "int", 13, 10, 28),
    )

    def _migrate_height(self) -> None:
        options = self.spec.setdefault("options", {})
        if options.get("layout") == self.LAYOUT_VERSION:
            return
        height = options.get("height")
        if isinstance(height, int) and height > 0:
            options["height"] = min(height + self.LEGACY_CHROME_HEIGHT, MAX_HEIGHT)
        options["layout"] = self.LAYOUT_VERSION  # wird mit der nächsten Optionsänderung gespeichert

    def build(self) -> None:
        self._migrate_height()
        self.body.setContentsMargins(12, 10, 12, 14)
        self.add_title()
        self.edit = QPlainTextEdit(self.spec.get("data", {}).get("text", ""), objectName="NotesEdit")
        self.edit.setPlaceholderText("Notiz schreiben …")
        self.edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.edit.setMinimumHeight(self.EDITOR_MIN_HEIGHT)
        self.body.addWidget(self.edit, 1)
        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(self._save)
        self.edit.textChanged.connect(self._save_timer.start)

    def options_changed(self) -> None:
        # Als Stylesheet, nicht per setFont(): Jedes Theme setzt `* { font-size: 13px; }`, und Stylesheets haben
        # in Qt Vorrang vor setFont(). Rahmen und Hintergrund für #NotesEdit kommen weiter aus dem Theme.
        self.edit.setStyleSheet(f"#NotesEdit {{ font-size: {int(self.option('font'))}px; }}")

    def _save(self) -> None:
        text = self.edit.toPlainText()
        data = {"text": text} if text else {}
        self.ctx.widgets.update_spec(self.spec["id"], data=data)

    def flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()

    def closeEvent(self, event) -> None:
        self.flush()
        super().closeEvent(event)
