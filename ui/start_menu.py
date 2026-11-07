"""Minimalistisches Home-Menü mit angepinnten Apps, Energieoptionen und Kill-Switch."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from core import win_api
from core.replacements import launch
from ui.base import G, IconButton, PopupPanel, glyph_icon, icon_for_target

LOGO = Path(__file__).resolve().parent.parent / "assets" / "popwin.png"
POPOVER_GAP = 6
BAR_GAP = 14  # so weit schwebt das Menü über der Leiste
# Aussehen des Home-Menüs: Kennung -> (Name, kurze Beschreibung)
VARIANTS = {
    "grid": ("Raster", "Kleine Icons mit Namen, wie bisher"),
    "list": ("Liste", "Eine App pro Zeile, Name neben dem Icon"),
    "tiles": ("Große Kacheln", "Drei große Kacheln pro Reihe"),
    "strip": ("Schmale Leiste", "Nur Icons in einer Reihe, sehr kompakt"),
    "search": ("Suche zuerst", "Suchfeld oben, darunter das Raster"),
}


def variant_of(config: dict) -> str:
    value = config.get("layout", "grid")
    return value if value in VARIANTS else "grid"


def variant_columns(variant: str, columns: int, count: int) -> int:
    """Spalten je Aussehen: Liste eine, Kacheln drei, Leiste alle Icons nebeneinander (höchstens acht)."""
    if variant == "list":
        return 1
    if variant == "tiles":
        return 3
    if variant == "strip":
        return max(1, min(8, count))
    return max(1, columns)


class VariantCard(QToolButton):
    """Gemalte Skizze eines Aussehens für die Vorschau in den Einstellungen."""

    def __init__(self, key: str):
        super().__init__(objectName="ThemeCard")
        self.key = key
        name, hint = VARIANTS[key]
        self.setText(name)
        self.setToolTip(hint)
        self.setCheckable(True)
        self.setFixedSize(116, 110)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = self.palette().window().color().lightness() < 128
        ink = QColor(255, 255, 255) if dark else QColor(20, 23, 29)
        accent = self.palette().highlight().color()
        box = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        painter.setPen(QPen(accent if self.isChecked() else QColor(ink.red(), ink.green(), ink.blue(), 40),
                            2.5 if self.isChecked() else 1))
        painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 14 if not self.underMouse() else 24))
        painter.drawRoundedRect(box, 12, 12)
        menu = QRectF(box.left() + 10, box.top() + 9, box.width() - 20, 64)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 26))
        painter.drawRoundedRect(menu, 8, 8)
        dot = QColor(ink.red(), ink.green(), ink.blue(), 150)
        soft = QColor(ink.red(), ink.green(), ink.blue(), 70)
        left, top, width = menu.left() + 7, menu.top() + 7, menu.width() - 14
        if self.key in ("grid", "search"):
            if self.key == "search":
                painter.setBrush(soft)
                painter.drawRoundedRect(QRectF(left, top, width, 9), 4.5, 4.5)
                top += 14
            painter.setBrush(dot)
            for row in range(2 if self.key == "search" else 3):
                for column in range(4):
                    painter.drawRoundedRect(QRectF(left + column * (width / 4) + 3, top + row * 17, width / 4 - 8, 11), 3, 3)
        elif self.key == "list":
            for row in range(4):
                painter.setBrush(dot)
                painter.drawRoundedRect(QRectF(left, top + row * 14, 9, 9), 2, 2)
                painter.setBrush(soft)
                painter.drawRoundedRect(QRectF(left + 14, top + row * 14 + 2, width * 0.6, 5), 2.5, 2.5)
        elif self.key == "tiles":
            painter.setBrush(dot)
            for row in range(2):
                for column in range(3):
                    painter.drawRoundedRect(QRectF(left + column * (width / 3) + 2, top + row * 28, width / 3 - 5, 23), 5, 5)
        else:
            painter.setBrush(dot)
            for column in range(6):
                painter.drawRoundedRect(QRectF(left + column * (width / 6) + 1, top + 20, width / 6 - 4, width / 6 - 4), 3, 3)
        painter.setPen(ink)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(box.left(), box.bottom() - 30, box.width(), 26), Qt.AlignmentFlag.AlignCenter, self.text())

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()


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

        self.search_bar = QPushButton(glyph_icon(G.SEARCH, 15, "#FFFFFF"), "  Apps, Dateien und Einstellungen suchen",
                                      objectName="SearchField")
        self.search_bar.setProperty("searchStyle", "pill")
        self.search_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.search_bar.setMinimumHeight(34)
        self.search_bar.clicked.connect(lambda: (self.dismiss(), ctx.launcher.open()))
        self.search_bar.hide()
        self.body.addWidget(self.search_bar)

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
        variant = variant_of(config)
        pinned = config["pinned"]
        columns = variant_columns(variant, config["columns"], len(pinned))
        icon_size = {"list": 24, "tiles": max(48, config["icon_size"]), "strip": min(32, config["icon_size"])}.get(
            variant, config["icon_size"])
        width = config["width"]
        if variant == "strip":
            width = max(280, columns * (icon_size + 26) + 30)
        color = self._color()
        self.search_bar.setVisible(variant == "search")
        self.search_bar.setIcon(glyph_icon(G.SEARCH, 15, color))
        self.grid.setSpacing(2 if variant == "list" else 8 if variant == "tiles" else 4)
        for index, app in enumerate(pinned):
            tile = QToolButton(self.grid_host, objectName="WindowRow" if variant == "list" else "AppTile")
            tile.setText(("   " + app["name"]) if variant == "list" else app["name"])  # Abstand zum Icon
            tile.setIconSize(QSize(icon_size, icon_size))
            if variant == "list":
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                tile.setMinimumHeight(36)
                tile.setStyleSheet("QToolButton { text-align: left; padding: 4px 10px; }")
            elif variant == "strip":
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                tile.setFixedSize(icon_size + 20, icon_size + 18)
            else:
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                tile.setFixedWidth(max(64, (width - 24) // columns - 4))
                if variant == "tiles":
                    tile.setFixedHeight(icon_size + 50)
                    tile.setStyleSheet("QToolButton { background: rgba(128, 128, 128, 0.13); border-radius: 14px; }"
                                       "QToolButton:hover { background: rgba(128, 128, 128, 0.26); }")
            icon = icon_for_target(app["target"])
            tile.setIcon(glyph_icon(G.APP, icon_size, color) if icon.isNull() else icon)
            tile.setToolTip(app["name"] if variant == "strip" else app["target"])
            tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tile.clicked.connect(lambda _=False, t=app["target"]: (self.dismiss(), launch(self.ctx, t)))
            self.grid.addWidget(tile, index // columns, index % columns)
            tile.show()  # Wer das Menü beim Ausblenden wieder öffnet: Sonst zählt die Kachel bei der Größe noch nicht mit
        self.setFixedWidth(width)

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
        self.setWindowOpacity(0)
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
