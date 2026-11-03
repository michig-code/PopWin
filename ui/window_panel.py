"""Fenster-Menü der Topbar: alle offenen Fenster nach App gruppiert, mit Suche."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core import win_api
from ui.base import G, IconButton, PopupPanel, icon_for_target, set_prop

MAX_LIST_HEIGHT = 420


def group_windows(windows: list[win_api.WindowInfo], query: str = "") -> dict[str, list[win_api.WindowInfo]]:
    """Gruppiert nach Programm; die Suche prüft Fenstertitel und Programmname."""
    needle = query.strip().lower()
    groups: dict[str, list[win_api.WindowInfo]] = {}
    for window in windows:
        app = os.path.splitext(os.path.basename(window.exe))[0] or window.title
        if not needle or needle in window.title.lower() or needle in app.lower():
            groups.setdefault(app, []).append(window)
    return groups


class WindowRow(QWidget):
    def __init__(self, panel: "WindowPanel", window: win_api.WindowInfo, screen_number: int, focused: bool):
        super().__init__(objectName="WindowRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.panel = panel
        self.window = window
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 4, 3)
        row.setSpacing(8)
        title = QLabel()
        title.setText(title.fontMetrics().elidedText(window.title, Qt.TextElideMode.ElideRight, 300))
        title.setProperty("dim", win_api.is_minimized(window.hwnd))
        monitor = QLabel(str(screen_number), objectName="Muted")
        monitor.setToolTip(f"Monitor {screen_number}")
        close = IconButton(G.CLOSE, "Fenster schließen")
        close.clicked.connect(self._close)
        row.addWidget(title, 1)
        row.addWidget(monitor)
        row.addWidget(close)
        set_prop(self, "focused", focused)

    def _close(self) -> None:
        win_api.close_window(self.window.hwnd)
        self.hide()

    def mouseReleaseEvent(self, event) -> None:
        win_api.activate_window(self.window.hwnd)
        self.panel.dismiss()


class WindowPanel(PopupPanel):
    def __init__(self, ctx, theme_name: str):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(420)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Fenster suchen")
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._open_first)
        self.body.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.addWidget(self.scroll)
        view = QPushButton("Aufgabenansicht", objectName="Secondary")
        view.clicked.connect(lambda: (self.dismiss(), win_api.send_keys(win_api.VK_LWIN, win_api.VK_TAB)))
        self.body.addWidget(view)
        self.rows: list[WindowRow] = []

    def open_at(self, anchor: QWidget) -> None:
        self.toggle_at(anchor, self._prepare)
        if self.isVisible():
            self.search.setFocus()

    def _prepare(self) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()

    def _monitor_numbers(self) -> dict[tuple, int]:
        return {rect: index + 1 for index, rect in enumerate(win_api.monitor_rects())}

    def refresh(self) -> None:
        host = QWidget(objectName="SettingsPage")
        host.setStyleSheet("background: transparent;")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        numbers = self._monitor_numbers()
        foreground = win_api.foreground_window()
        self.rows = []
        groups = group_windows(win_api.list_windows(), self.search.text())
        for app, windows in groups.items():
            head = QHBoxLayout()
            head.setContentsMargins(6, 4, 0, 0)
            icon = QLabel()
            icon.setPixmap(self._icon(windows[0]).pixmap(QSize(18, 18)))
            head.addWidget(icon)
            head.addWidget(QLabel(app, objectName="MediaTitle"), 1)
            box.addLayout(head)
            for window in windows:
                number = numbers.get(win_api.window_monitor(window.hwnd), 1)
                row = WindowRow(self, window, number, window.hwnd == foreground)
                box.addWidget(row)
                self.rows.append(row)
        if not groups:
            box.addWidget(QLabel("Keine Fenster gefunden", objectName="Muted"))
        self.scroll.setWidget(host)
        self.scroll.setFixedHeight(min(MAX_LIST_HEIGHT, host.sizeHint().height() + 8))
        self.adjustSize()

    @staticmethod
    def _icon(window: win_api.WindowInfo) -> QIcon:
        icon = icon_for_target(window.exe) if window.exe else QIcon()
        if icon.isNull():
            handle = win_api.window_hicon(window.hwnd)
            image = QImage.fromHICON(handle) if handle else QImage()
            if not image.isNull():
                return QIcon(QPixmap.fromImage(image))
        return icon

    def _open_first(self) -> None:
        if self.rows:
            win_api.activate_window(self.rows[0].window.hwnd)
            self.dismiss()
