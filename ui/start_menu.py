"""Minimalistisches Home-Menü mit angepinnten Apps, Energieoptionen und Kill-Switch."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from core import win_api
from core.replacements import launch
from ui.base import G, IconButton, PopupPanel, glyph_icon, icon_for_target

LOGO = Path(__file__).resolve().parent.parent / "assets" / "popwin.png"
POPOVER_GAP = 6
BAR_GAP = 14  # so weit schwebt das Menü über der Leiste


class Popover(QWidget):
    """Kleines Menü über einem Knopf. Bleibt im Home-Menü: Ein eigenes Fenster nähme ihm den Fokus und schlösse es."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, objectName="Popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.anchor: QWidget | None = None
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(6, 6, 6, 6)
        self.box.setSpacing(1)
        self.hide()

    def fill(self, actions: list, color: str) -> None:
        while self.box.count():
            widget = self.box.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for glyph, label, handler in actions:
            row = QPushButton(glyph_icon(glyph, 18, color), label, objectName="PopoverItem")
            row.setIconSize(QSize(18, 18))
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.clicked.connect(lambda _=False, h=handler: h())
            self.box.addWidget(row)

    def open_at(self, anchor: QWidget, right: bool) -> None:
        self.anchor = anchor
        self.adjustSize()
        corner = anchor.mapTo(self.parentWidget(), QPoint(anchor.width() if right else 0, 0))
        x = corner.x() - self.width() if right else corner.x()
        self.move(max(POPOVER_GAP, x), max(POPOVER_GAP, corner.y() - self.height() - POPOVER_GAP))
        self.raise_()
        self.show()


class StartMenu(PopupPanel):
    CENTERED = True  # wie unter Windows 11: immer mittig über der Taskbar, egal wo der Home-Knopf sitzt

    def __init__(self, ctx):
        super().__init__(ctx)
        self.anchor: QWidget | None = None  # die Taskbar, an der das Menü gerade hängt
        header = QHBoxLayout()
        header.addWidget(QLabel("Home", objectName="PanelTitle"))
        header.addStretch(1)
        self.settings = IconButton(G.SETTINGS, "UI-Einstellungen")
        self.settings.clicked.connect(lambda: (self.dismiss(), ctx.open_settings()))
        files = IconButton(G.FOLDER, "PopWin Explorer")
        files.clicked.connect(lambda: (self.dismiss(), ctx.open_explorer()))
        header.addWidget(files)
        header.addWidget(self.settings)
        self.body.addLayout(header)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.body.addWidget(self.grid_host)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.power = IconButton(G.POWER, "Sperren, Energie sparen, Neu starten, Herunterfahren")
        self.power.clicked.connect(lambda: self._toggle_popover(self.power, self._power_actions(), False))
        self.popwin = QPushButton("PopWin", objectName="Secondary")
        self.popwin.setIcon(QIcon(str(LOGO)))
        self.popwin.setToolTip("PopWin neu starten oder stoppen")
        self.popwin.clicked.connect(lambda: self._toggle_popover(self.popwin, self._popwin_actions(), True))
        row.addWidget(self.power)
        row.addStretch(1)
        row.addWidget(self.popwin)
        self.body.addLayout(row)
        self.popover = Popover(self.panel)

    def _confirm(self, question: str, action) -> None:
        if QMessageBox.question(None, "PopWin", question) == QMessageBox.StandardButton.Yes:
            action()

    def _color(self) -> str:
        return "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"

    def _toggle_popover(self, anchor: QWidget, actions: list, right: bool) -> None:
        if self.popover.isVisible() and self.popover.anchor is anchor:
            self.popover.hide()
            return
        self.popover.fill([(glyph, label, lambda h=handler: (self.dismiss(), h())) for glyph, label, handler in actions],
                          self._color())
        self.popover.open_at(anchor, right)

    def _power_actions(self) -> list[tuple[str, str, object]]:
        actions = [
            (G.LOCK, "Sperren", win_api.lock_workstation),
            (G.SIGN_OUT, "Abmelden", lambda: self._confirm("Jetzt abmelden?", win_api.sign_out)),
            (G.SLEEP, "Energie sparen", win_api.sleep_system),
        ]
        if win_api.hibernate_available():
            actions.append((G.DISK, "Ruhezustand", win_api.hibernate))
        return actions + [
            (G.POWER, "Herunterfahren", lambda: self._confirm("Jetzt herunterfahren?", win_api.shutdown)),
            (G.RESTART, "Neu starten", lambda: self._confirm("Jetzt neu starten?", win_api.restart)),
        ]

    def _popwin_actions(self) -> list[tuple[str, str, object]]:
        return [(G.RESTART, "PopWin neu starten", self.ctx.restart_ui), (G.POWER, "PopWin stoppen", self.ctx.disable_ui)]

    def mousePressEvent(self, event) -> None:
        self.popover.hide()
        super().mousePressEvent(event)

    def _rebuild(self) -> None:
        config = self.ctx.config.get("start_menu")
        while self.grid.count():
            tile = self.grid.takeAt(0).widget()
            tile.hide()
            tile.deleteLater()
        columns = max(1, config["columns"])
        icon_size = config["icon_size"]
        color = self._color()
        for index, app in enumerate(config["pinned"]):
            tile = QToolButton(objectName="AppTile")
            tile.setText(app["name"])
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setIconSize(QSize(icon_size, icon_size))
            tile.setFixedWidth(max(64, (config["width"] - 24) // columns - 4))
            icon = icon_for_target(app["target"])
            tile.setIcon(glyph_icon(G.APP, icon_size, color) if icon.isNull() else icon)
            tile.setToolTip(app["target"])
            tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tile.clicked.connect(lambda _=False, t=app["target"]: (self.dismiss(), launch(self.ctx, t)))
            self.grid.addWidget(tile, index // columns, index % columns)
            tile.show()  # Wer das Menü beim Ausblenden wieder öffnet: Sonst zählt die Kachel bei der Größe noch nicht mit
        self.setFixedWidth(config["width"])

    def toggle(self, anchor: QWidget | None) -> None:
        if self.isVisible() and not self.animator.closing:
            self.dismiss()
            return
        self.anchor = anchor
        self.popover.hide()
        self._rebuild()
        if anchor is not None:
            self.show_at(anchor, BAR_GAP)
            return
        self._take_over()
        self.apply_theme()
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.move(screen.availableGeometry().center() - self.rect().center())
        self.show()
        win_api.force_foreground(int(self.winId()))
        self.activateWindow()
        self.setFocus()
        self.animator.enter(1.0, slide=10)

    def keyPressEvent(self, event) -> None:
        # Wie im Windows-Startmenü: Einfach lostippen startet die Suche.
        text = event.text()
        if text and text.isprintable() and not text.isspace():
            self.dismiss()
            self.ctx.launcher.open()
            self.ctx.launcher.input.setText(text)
        else:
            super().keyPressEvent(event)
