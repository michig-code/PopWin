"""Symbolleiste über der Taskbar (Beta): Unten bleiben nur die Apps, Home, Suche und die anderen Knöpfe schweben darüber.

Wahlweise immer zu sehen oder nur, solange die Maus auf der Taskbar oder der Leiste selbst liegt. Schwebt auch die Uhr über
der Taskbar-Mitte, legt `dock_stack_order` fest, wer von beiden direkt an der Taskbar sitzt.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ui.base import ThemedWindow

TOOL_ITEMS = {"start": "Home-Knopf", "search": "Suche", "media": "Player", "nova": "Nova", "trash": "Papierkorb",
              "desktop": "Desktop anzeigen"}
TOOL_MODES = {"off": "Aus", "hover": "Nur beim Drüberfahren", "always": "Immer"}
STACK_ORDERS = {"clock_below": "Uhr unten, Symbole oben", "tools_below": "Symbole unten, Uhr oben"}
GAP = 8
LINGER = 0.6  # so lange bleibt die Leiste nach dem Wegfahren noch stehen
FADE_MS = 120


def tools_wanted(mode: str, always_visible: bool, hovered: bool, held: bool) -> bool:
    if mode == "always":
        return always_visible
    return mode == "hover" and always_visible and (hovered or held)


def stacked_y(edge: str, top: int, bottom: int, height: int, gap: int, under: int = 0) -> int:
    """Wo ein schwebendes Fenster an der Taskbar steht. `under` ist die Höhe dessen, was zwischen ihm und der Taskbar sitzt,
    0 heißt: direkt an der Taskbar. Oben am Bildschirm gilt alles gespiegelt: unten ist dort näher an der Taskbar."""
    lift = under + gap if under else 0
    return top - gap - lift - height if edge == "bottom" else bottom + gap + lift


def sits_below(order: str, window: str) -> bool:
    """Ob „clock“ oder „tools“ näher an der Taskbar sitzt."""
    return (order == "tools_below") == (window == "tools")


def showing(window) -> bool:
    """Sichtbar und nicht gerade am Ausblenden: Nur dann muss das andere Fenster Platz machen."""
    if window is None or not window.isVisible():
        return False
    fade = getattr(window, "fade", None)
    return fade is None or fade.state() != fade.State.Running or fade.endValue() != 0.0


class ToolRow(ThemedWindow):
    def __init__(self, dock):
        super().__init__(dock.ctx, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint, dock.theme_name)
        self.dock = dock
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QWidget(self, objectName="Bar")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.row = QHBoxLayout(self.frame)
        self.row.setContentsMargins(6, 3, 6, 3)
        self.row.setSpacing(3)
        outer.addWidget(self.frame)
        self._linger_until = 0.0
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(FADE_MS)
        self.fade.finished.connect(self._fade_done)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self.follow)
        self.apply_theme()

    def contextMenuEvent(self, event) -> None:
        """Rechtsklick wie auf der Taskbar: Die Leiste gehört zu ihr."""
        self.dock.contextMenuEvent(event)

    def set_widgets(self, widgets: list[QWidget]) -> None:
        for widget in widgets:
            self.row.addWidget(widget)
        self.adjustSize()

    def start(self) -> None:
        self.timer.start()
        self.follow()

    def stop(self) -> None:
        self.timer.stop()
        self.fade.stop()
        self.hide()

    def _hovered(self) -> bool:
        cursor = QCursor.pos()
        dock = self.dock
        return (dock.frameGeometry().contains(cursor) or (self.isVisible() and self.frameGeometry().contains(cursor))
                or dock._keeps_open())

    def follow(self) -> None:
        dock = self.dock
        mode = dock.tools_mode()
        visible = (dock.isVisible() and dock.windowOpacity() > 0.5 and (not dock._fullscreen or dock.peeking()))
        now = time.monotonic()
        hovered = visible and self._hovered()
        if hovered:
            self._linger_until = now + LINGER
        if tools_wanted(mode, visible, hovered, now < self._linger_until):
            self._place()
            if not self.isVisible() or self.fade.endValue() == 0.0:
                self._fade_to(1.0)
        elif self.isVisible() and self.fade.endValue() != 0.0:
            self._fade_to(0.0 if visible else -1)

    def _place(self) -> None:
        self.adjustSize()
        dock = self.dock
        area = dock.frameGeometry()
        pill = dock.pill
        # Wer oben steht, rechnet mit der Höhe des unteren; ist die Uhr gerade nicht da, sitzt die Leiste an der Taskbar.
        under = 0
        if not sits_below(dock.option("dock_stack_order"), "tools") and showing(pill):
            under = pill.height()
        x = area.center().x() - self.width() // 2
        self.move(x, stacked_y(dock.edge, area.top(), area.bottom(), self.height(), GAP, under))

    def _fade_to(self, value: float) -> None:
        """-1 heißt: sofort weg, ohne Animation, etwa wenn die Taskbar selbst verschwindet."""
        self.fade.stop()
        if value < 0:
            self.fade.setEndValue(0.0)
            self.hide()
            return
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(value)
        self.fade.start()

    def _fade_done(self) -> None:
        if self.fade.endValue() == 0.0:
            self.hide()
