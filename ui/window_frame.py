"""Eigenes Fenster mit eigener Titelleiste, das technisch ein echtes Windows-Fenster bleibt.

Die Fensterstile bleiben gesetzt, nur die Titelleiste zeichnen wir selbst. Dadurch laufen Minimieren-
und Maximieren-Animationen, Einrasten, Snap-Layouts, Größe ändern und Schatten über Windows.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from core import win_api
from ui.base import ThemedWindow, set_prop

ASSETS = Path(__file__).resolve().parent.parent / "assets"
WM_NCCALCSIZE, WM_NCHITTEST, WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP = 0x83, 0x84, 0xA0, 0xA1, 0xA2
WM_NCMOUSELEAVE = 0x2A2
WM_EXITSIZEMOVE = 0x232
WM_WINDOWPOSCHANGED = 0x47
HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17
GWL_STYLE = -16
WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_THICKFRAME, WS_CAPTION, WS_SYSMENU = 0x10000, 0x20000, 0x40000, 0xC00000, 0x80000
WS_POPUP = 0x80000000
SWP_FRAMECHANGED = 0x20
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
RESIZE_BORDER = 6
GLYPH_MIN, GLYPH_MAX, GLYPH_RESTORE, GLYPH_CLOSE = "", "", "", ""

user32 = win_api.user32
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


class MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


win_api.dwmapi.DwmExtendFrameIntoClientArea.argtypes = (wintypes.HWND, ctypes.POINTER(MARGINS))


def native_style(style: int) -> int:
    """Stil eines normalen App-Fensters. Qt setzt für rahmenlose Fenster WS_POPUP; damit behandelt Windows es wie ein Menü:
    Win+Pfeiltasten, Einrasten und die Öffnen-, Minimieren- und Maximieren-Animationen fehlen dann."""
    return (style & 0xFFFFFFFF & ~WS_POPUP) | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU


def hit_edge(x: int, y: int, width: int, height: int, border: int = RESIZE_BORDER) -> int:
    """Liegt der Punkt am Fensterrand? Liefert den passenden Windows-Trefferwert oder 0."""
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top:
        return HTTOPLEFT if left else HTTOPRIGHT if right else HTTOP
    if bottom:
        return HTBOTTOMLEFT if left else HTBOTTOMRIGHT if right else HTBOTTOM
    return HTLEFT if left else HTRIGHT if right else 0


class TitleBar(QWidget):
    HEIGHT = 40

    def __init__(self, window: "NativeWindow", title: str, icon: str):
        super().__init__(objectName="TitleBar")
        self.window_ref = window
        self.setFixedHeight(self.HEIGHT)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(12, 0, 0, 0)
        self.row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / f"{icon}.png")).scaled(
            18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.title = QLabel(title, objectName="MediaTitle")
        self.row.addWidget(logo)
        self.row.addWidget(self.title)
        self.middle = QHBoxLayout()
        self.middle.setContentsMargins(16, 0, 16, 0)
        self.row.addLayout(self.middle, 1)
        self.minimize = self._button(GLYPH_MIN, "Minimieren", window.showMinimized)
        self.maximize = self._button(GLYPH_MAX, "Maximieren", window.toggle_maximized)
        self.close_button = self._button(GLYPH_CLOSE, "Schließen", window.close, "WinClose")

    def _button(self, glyph: str, tip: str, handler, name: str = "WinButton") -> QPushButton:
        button = QPushButton(glyph, objectName=name)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda: handler())
        self.row.addWidget(button)
        return button

    def interactive_at(self, position: QPoint) -> bool:
        """Alles außer Beschriftungen bekommt seine Klicks selbst, zum Beispiel Knöpfe und Tabs."""
        child = self.childAt(position)
        if isinstance(child, QTabBar):  # sie füllt die ganze Leiste, frei neben den Tabs bleibt sie zum Ziehen
            return child.tabAt(child.mapFrom(self, position)) >= 0
        return child is not None and child is not self and not isinstance(child, QLabel)


class MouseButtons(QObject):
    """Maustasten 4 und 5 für alle Fenster; ohne Filter fingen Listen und Felder sie ab."""
    _installed: "MouseButtons | None" = None

    @classmethod
    def install(cls) -> None:
        if cls._installed is None:
            cls._installed = cls(QApplication.instance())
            QApplication.instance().installEventFilter(cls._installed)

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.MouseButtonPress or not isinstance(watched, QWidget):
            return False
        button = event.button()
        if button not in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            return False
        window = watched.window()
        if not isinstance(window, NativeWindow):
            return False
        window.mouse_step(-1 if button == Qt.MouseButton.BackButton else 1)
        return True


class NativeWindow(ThemedWindow):
    def __init__(self, ctx, title: str, icon: str = "popwin"):
        """icon: Name der Bilddatei in assets, ohne Endung."""
        super().__init__(ctx, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint, translucent=False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("SettingsRoot")
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(str(ASSETS / f"{icon}.ico")))
        self.setMinimumSize(620, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.titlebar = TitleBar(self, title, icon)
        layout.addWidget(self.titlebar)
        self.content = QWidget()
        layout.addWidget(self.content, 1)
        self._framed = False
        self._normal_rect: tuple[int, int, int, int] | None = None
        self._frame_styles()  # schon vor dem ersten Zeigen, sonst fehlt beim ersten Öffnen die Animation von Windows
        MouseButtons.install()

    def mouse_step(self, step: int) -> None:
        """Maustaste 4 (-1) oder 5 (+1); jede App legt selbst fest, was das bedeutet."""

    def keep_in_work_area(self) -> None:
        """Die Topbar liegt immer vorn: Darunter wäre die Titelleiste nicht mehr zu greifen."""
        area = self.screen().availableGeometry()
        if not self.isMaximized() and self.y() < area.top():
            self.move(self.x(), area.top())

    # --- Windows-Fensterstile ------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.keep_in_work_area)
        self._frame_styles()

    def _frame_styles(self) -> None:
        """Prüft bei jedem Zeigen nach: Qt baut das Fenster bei manchen Änderungen neu und setzt dabei seine Stile."""
        hwnd = int(self.winId())
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        if self._framed and style == native_style(style):
            return
        self._framed = True
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, native_style(style))
        margins = MARGINS(0, 0, 0, 1)  # hält den Fensterschatten von Windows aktiv
        win_api.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        self._refresh_frame()

    def _refresh_frame(self) -> None:
        user32.SetWindowPos(int(self.winId()), None, 0, 0, 0, 0, win_api.SWP_NOMOVE | win_api.SWP_NOSIZE | win_api.SWP_NOZORDER
                            | win_api.SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def apply_theme(self) -> None:
        super().apply_theme()
        hwnd = int(self.winId())
        win_api.set_blur(hwnd, "none")
        win_api.set_rounded(hwnd, True)

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMaximized() and self._normal_rect:
            QTimer.singleShot(0, lambda: win_api.fix_normal_position(int(self.winId()), self._normal_rect))
        titlebar = getattr(self, "titlebar", None)  # Qt meldet schon den Fenstertitel, bevor sie existiert
        if titlebar:
            titlebar.maximize.setText(GLYPH_RESTORE if self.isMaximized() else GLYPH_MAX)

    # --- Titelleiste für Windows: Ziehen, Rand, Snap-Layouts --------------------------
    def _local(self, lparam: int) -> tuple[QPoint, tuple[int, int, int, int]]:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = win_api.window_rect(int(self.winId()))
        return QPoint(x - rect[0], y - rect[1]), rect

    def _hit_test(self, lparam: int) -> int:
        native, rect = self._local(lparam)
        if not user32.IsZoomed(int(self.winId())):
            edge = hit_edge(native.x(), native.y(), rect[2] - rect[0], rect[3] - rect[1])
            if edge:
                return edge
        ratio = self.devicePixelRatio()
        position = self.titlebar.mapFrom(self, QPoint(round(native.x() / ratio), round(native.y() / ratio)))
        if not self.titlebar.rect().contains(position):
            return HTCLIENT
        if self.titlebar.maximize.geometry().contains(position):
            return HTMAXBUTTON  # nur so zeigt Windows beim Überfahren die Snap-Layouts
        return HTCLIENT if self.titlebar.interactive_at(position) else HTCAPTION

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_WINDOWPOSCHANGED and not user32.IsZoomed(msg.hWnd) and not user32.IsIconic(msg.hWnd):
            self._normal_rect = win_api.window_rect(msg.hWnd)
        if msg.message == WM_EXITSIZEMOVE:
            self.keep_in_work_area()
            self._refresh_frame()
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            # Windows fragen, nicht Qt: Beim Herausziehen aus dem Vollbild weiß Qt noch nichts davon, der Rahmenabstand
            # bliebe stehen und oben entstünde ein weißer Streifen.
            if user32.IsZoomed(msg.hWnd):
                frame_x = user32.GetSystemMetrics(SM_CXSIZEFRAME) + user32.GetSystemMetrics(SM_CXPADDEDBORDER)
                frame_y = user32.GetSystemMetrics(SM_CYSIZEFRAME) + user32.GetSystemMetrics(SM_CXPADDEDBORDER)
                rect = wintypes.RECT.from_address(msg.lParam)
                rect.left += frame_x
                rect.top += frame_y
                rect.right -= frame_x
                rect.bottom -= frame_y
            return True, 0
        if msg.message == WM_NCHITTEST:
            result = self._hit_test(msg.lParam)
            if result != HTCLIENT:
                return True, result
        button = self.titlebar.maximize
        if msg.message == WM_NCMOUSEMOVE:
            set_prop(button, "hot", msg.wParam == HTMAXBUTTON)
        elif msg.message == WM_NCMOUSELEAVE:
            set_prop(button, "hot", False)
        elif msg.wParam == HTMAXBUTTON and msg.message == WM_NCLBUTTONDOWN:
            return True, 0
        elif msg.wParam == HTMAXBUTTON and msg.message == WM_NCLBUTTONUP:
            self.toggle_maximized()
            return True, 0
        return False, 0


def fade_in_page(page: QWidget, duration: int) -> None:
    """Sanfter Seitenwechsel innerhalb des Fensters, unabhängig von Windows-Animationen."""
    if not duration:
        return
    effect = QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    fade = QVariantAnimation(page)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.valueChanged.connect(effect.setOpacity)
    fade.finished.connect(lambda: page.setGraphicsEffect(None))
    fade.start()
