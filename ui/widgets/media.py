"""Medien-Controller als eigenes Widget."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

from ui.base import G
from ui.taskbar import MediaBox
from ui.widgets.base import DeskletWindow


class MediaWidget(DeskletWindow):
    TITLE = "Medien"
    GLYPH = G.MUSIC
    DESCRIPTION = "Was gerade läuft, mit Steuerung"
    DEFAULT_WIDTH = 300

    def build(self) -> None:
        self.idle = QLabel("Gerade läuft nichts", objectName="Muted")
        self.box = MediaBox(self.ctx, width=self.DEFAULT_WIDTH - 24)
        self.body.addWidget(self.idle)
        self.body.addWidget(self.box)
        self.ctx.media.updated.connect(self._sync)
        self._sync(self.ctx.last_media)

    def options_changed(self) -> None:
        self.box.setFixedWidth(self.option("width") - 24)

    def _sync(self, media: dict | None) -> None:
        self.idle.setVisible(not (media and media["title"]))
