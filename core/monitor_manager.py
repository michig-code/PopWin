"""Multi-Monitor-Erkennung und Umrechnung zwischen Qt-Bildschirmen und echten Pixeln."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication, QScreen

from core import win_api


def screen_key(screen: QScreen) -> str:
    """Stabil über Neustarts hinweg; die Nummerierung von Windows ist das nicht."""
    serial = screen.serialNumber()
    return f"{screen.name()}|{serial}" if serial else screen.name()


def native_rect(screen: QScreen) -> tuple[int, int, int, int]:
    """Das echte Pixel-Rechteck. Qt behält die native Ecke bei, die Größe ist aber skaliert."""
    geo = screen.geometry()
    for rect in win_api.monitor_rects():
        if rect[0] == geo.x() and rect[1] == geo.y():
            return rect
    ratio = screen.devicePixelRatio()
    return (geo.x(), geo.y(), geo.x() + round(geo.width() * ratio), geo.y() + round(geo.height() * ratio))


def native_point(screen: QScreen, x: int, y: int) -> tuple[int, int]:
    """Rechnet eine Qt-Position auf diesem Monitor in echte Pixel um."""
    geo = screen.geometry()
    left, top, _right, _bottom = native_rect(screen)
    ratio = screen.devicePixelRatio()
    return left + round((x - geo.x()) * ratio), top + round((y - geo.y()) * ratio)


class MonitorManager(QObject):
    screens_changed = Signal()

    def __init__(self):
        super().__init__()
        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _s: self.screens_changed.emit())
        app.screenRemoved.connect(lambda _s: self.screens_changed.emit())
        app.primaryScreenChanged.connect(lambda _s: self.screens_changed.emit())

    @staticmethod
    def screens() -> list[QScreen]:
        return QGuiApplication.screens()

    @staticmethod
    def is_primary(screen: QScreen) -> bool:
        return screen == QGuiApplication.primaryScreen()

    @staticmethod
    def by_key(key: str) -> QScreen | None:
        return next((s for s in QGuiApplication.screens() if screen_key(s) == key), None)
