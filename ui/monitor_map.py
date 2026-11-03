"""Monitor-Karte wie in den Windows-Anzeigeeinstellungen und die großen Nummern auf den Bildschirmen."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

MAP_HEIGHT = 230
TILE_GAP = 4
BADGE_SIZE = 200
BADGE_MARGIN = 48
BADGE_SECONDS = 3


@dataclass(frozen=True)
class MonitorEntry:
    key: str
    number: int
    name: str
    rect: tuple[int, int, int, int]  # echte Pixel: links, oben, rechts, unten
    primary: bool

    @property
    def size_text(self) -> str:
        return f"{self.rect[2] - self.rect[0]} × {self.rect[3] - self.rect[1]}"

    @property
    def title(self) -> str:
        return f"Bildschirm {self.number} · {self.name} · {self.size_text}" + (" · Hauptmonitor" if self.primary else "")


def fit_rects(rects: list[tuple], width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Verkleinert die Anordnung aus Windows so, dass sie zentriert in die Fläche passt: (x, y, Breite, Höhe)."""
    left, top = min(r[0] for r in rects), min(r[1] for r in rects)
    right, bottom = max(r[2] for r in rects), max(r[3] for r in rects)
    scale = min(width / (right - left), height / (bottom - top))
    offset_x = (width - (right - left) * scale) / 2
    offset_y = (height - (bottom - top) * scale) / 2
    return [(round(offset_x + (r[0] - left) * scale), round(offset_y + (r[1] - top) * scale),
             round((r[2] - r[0]) * scale) - TILE_GAP, round((r[3] - r[1]) * scale) - TILE_GAP) for r in rects]


class MonitorTile(QPushButton):
    def __init__(self, entry: MonitorEntry):
        super().__init__(objectName="MonitorTile")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # sonst scrollt die Seite zum nächsten Feld, wenn die Karte neu entsteht
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(entry.title)
        box = QVBoxLayout(self)
        box.setContentsMargins(4, 2, 4, 2)
        box.setSpacing(0)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for text, name in ((str(entry.number), "MonitorNumber"), (entry.name, "Muted"), (entry.size_text, "Muted")):
            label = QLabel(text, objectName=name, alignment=Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            box.addWidget(label)


class MonitorMap(QWidget):
    picked = Signal(str)

    def __init__(self, entries: list[MonitorEntry], current: str):
        super().__init__()
        self.setFixedHeight(MAP_HEIGHT)
        self.entries = entries
        self.tiles: dict[str, MonitorTile] = {}
        for entry in entries:
            tile = MonitorTile(entry)
            tile.setParent(self)
            tile.setChecked(entry.key == current)
            tile.clicked.connect(lambda _=False, key=entry.key: self.picked.emit(key))
            self.tiles[entry.key] = tile

    def resizeEvent(self, event) -> None:
        places = fit_rects([entry.rect for entry in self.entries], self.width(), self.height())
        for entry, place in zip(self.entries, places):
            self.tiles[entry.key].setGeometry(*place)
        super().resizeEvent(event)


class NumberBadge(QWidget):
    """Die große Nummer unten links auf einem Bildschirm, wie bei „Identifizieren“ in Windows."""

    def __init__(self, number: int, screen_area: QRect):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.number = number
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(BADGE_SIZE, BADGE_SIZE)
        self.move(screen_area.left() + BADGE_MARGIN, screen_area.bottom() - BADGE_SIZE - BADGE_MARGIN)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 24, 24, 235))
        painter.drawRoundedRect(self.rect(), 20, 20)
        font = QFont("Segoe UI Variable Display")
        font.setPixelSize(130)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.number))


class MonitorNumbers:
    """Zeigt die Nummern für ein paar Sekunden; ein erneuter Aufruf beginnt von vorn."""

    def __init__(self):
        self.badges: list[NumberBadge] = []
        self.timer = QTimer(singleShot=True, interval=BADGE_SECONDS * 1000)
        self.timer.timeout.connect(self.hide)

    def show(self, screens: list[tuple[int, QRect]]) -> None:
        self.hide()
        self.badges = [NumberBadge(number, area) for number, area in screens]
        for badge in self.badges:
            badge.show()
        self.timer.start()

    def hide(self) -> None:
        for badge in self.badges:
            badge.close()
            badge.deleteLater()
        self.badges = []
