"""Die Uhr steht an genau einem Platz: oben rechts, oben in der Mitte, in der Taskbar links oder rechts oder über ihrer Mitte."""
from __future__ import annotations

from PySide6.QtCore import QDateTime, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLabel, QWidget

from ui.base import ThemedWindow
from ui.dock_tools import showing, sits_below, stacked_y

PLACES = {"top_right": "Topbar rechts", "top_center": "Topbar Mitte", "dock_left": "Taskbar links",
          "dock_center": "Über der Taskbar-Mitte", "dock_right": "Taskbar rechts", "none": "Keine Uhr"}
PILL_GAP = 8


def clock_texts(profile: dict, now: QDateTime) -> tuple[str, str]:
    """Uhrzeit und Datum, wie das Profil sie will; ein leerer Teil wird nicht gezeigt."""
    time = now.toString("HH:mm:ss" if profile["clock_seconds"] else "HH:mm")
    date = now.toString("dd.MM.yyyy")
    style = profile["clock_style"]
    return (time if style != "date" else "", date if style != "time" else "")


class ClockFace(QWidget):
    """Uhrzeit über Datum oder beides in einer Zeile. Dieselben Namen wie in der Topbar, also dieselbe Schrift aus dem Theme."""

    def __init__(self, profile, align=Qt.AlignmentFlag.AlignRight, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.box.setContentsMargins(6, 0, 6, 0)
        self.box.setSpacing(0)
        self.time_label = QLabel(objectName="ClockTime")
        self.date_label = QLabel(objectName="ClockDate")
        for label in (self.time_label, self.date_label):
            label.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            self.box.addWidget(label)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.arrange()

    def arrange(self, one_line: bool | None = None) -> None:
        if one_line is None:
            one_line = self.profile()["clock_lines"] == "one"
        self.box.setDirection(QBoxLayout.Direction.LeftToRight if one_line else QBoxLayout.Direction.TopToBottom)
        self.box.setSpacing(6 if one_line else 0)
        self.tick()

    def tick(self) -> None:
        time, date = clock_texts(self.profile(), QDateTime.currentDateTime())
        self.time_label.setText(time)
        self.date_label.setText(date)
        self.time_label.setVisible(bool(time))
        self.date_label.setVisible(bool(date))


class ClockPill(ThemedWindow):
    """Schwebt mittig über der Taskbar. Klicks gehen durch, sie soll nichts darunter verdecken, was man bedienen will."""

    def __init__(self, dock):
        super().__init__(dock.ctx, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.NoDropShadowWindowHint, dock.theme_name)
        self.dock = dock
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.frame = QWidget(self, objectName="Panel")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        inner = QHBoxLayout(self.frame)
        inner.setContentsMargins(12, 5, 12, 5)
        self.face = ClockFace(lambda: dock.ctx.config.monitor(dock.screen_id), Qt.AlignmentFlag.AlignCenter)
        self.face.arrange(one_line=True)
        inner.addWidget(self.face)
        row.addWidget(self.frame)
        self.apply_theme()

    def follow(self) -> None:
        """Steht, wo die Taskbar gerade sichtbar ist; ist sie weg, ist die Uhr auch weg."""
        dock = self.dock
        wanted = (dock.option("clock_place") == "dock_center" and dock.isVisible() and dock.windowOpacity() > 0.5
                  and (not dock._fullscreen or dock.peeking()))
        if not wanted:
            self.hide()
            return
        self.face.tick()
        self.adjustSize()
        area = dock.frameGeometry()
        x = area.center().x() - self.width() // 2
        under = 0  # steht die Symbolleiste unten und ist gerade da, rückt die Uhr über sie
        if sits_below(dock.option("dock_stack_order"), "tools") and showing(dock.tools):
            under = dock.tools.height()
        self.move(x, stacked_y(dock.edge, area.top(), area.bottom(), self.height(), PILL_GAP, under))
        if not self.isVisible():
            self.show()


class PlacePicker(QWidget):
    """Ein kleiner Bildschirm mit Topbar und Taskbar: Man klickt einfach dorthin, wo die Uhr stehen soll.

    Steht die Uhr über der Taskbar-Mitte und gibt es die Symbolleiste, zeigt das Bild beide Reihen. Ein Klick auf die obere
    tauscht sie."""
    picked = Signal(str)
    stack_changed = Signal(str)

    SPOTS = {"top_right": (0.83, 0.07), "top_center": (0.5, 0.07), "dock_left": (0.17, 0.93), "dock_center": (0.5, 0.79),
             "dock_right": (0.83, 0.93)}
    STACK_STEP = 0.14  # so viel höher steht die obere Reihe

    def __init__(self, current: str, parent=None, stack_order: str = "", show_stack: bool = False):
        super().__init__(parent)
        self.current = current
        self.stack_order = stack_order or "clock_below"
        self.show_stack = show_stack
        self.accent = self.palette().color(QPalette.ColorRole.Highlight)
        self.hovered = ""
        self.setMouseTracking(True)
        self.setMinimumSize(420, 236)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def stacking(self) -> bool:
        return self.show_stack and self.current == "dock_center"

    def _rect_at(self, x: float, y: float) -> QRectF:
        width, height = 92, 26
        return QRectF(self.width() * x - width / 2, self.height() * y - height / 2, width, height)

    def _spot_rect(self, place: str) -> QRectF:
        x, y = self.SPOTS[place]
        if place == "dock_center" and self.stacking() and not sits_below(self.stack_order, "clock"):
            y -= self.STACK_STEP
        return self._rect_at(x, y)

    def tools_rect(self) -> QRectF:
        x, y = self.SPOTS["dock_center"]
        return self._rect_at(x, y - (0 if sits_below(self.stack_order, "tools") else self.STACK_STEP))

    def place_at(self, point) -> str:
        if self.stacking() and self.tools_rect().contains(QPointF(point)):
            return "tools"
        return next((place for place in self.SPOTS if self._spot_rect(place).contains(QPointF(point))), "")

    def mouseMoveEvent(self, event) -> None:
        hovered = self.place_at(event.position())
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()

    def leaveEvent(self, _event) -> None:
        self.hovered = ""
        self.update()

    def swap(self) -> None:
        self.stack_order = "clock_below" if self.stack_order == "tools_below" else "tools_below"
        self.update()
        self.stack_changed.emit(self.stack_order)

    def mousePressEvent(self, event) -> None:
        place = self.place_at(event.position())
        if place == "tools" or (place == "dock_center" and self.stacking()):
            self.swap()  # ein Klick auf eine der beiden Reihen tauscht sie
        elif place:
            self.current = place
            self.update()
            self.picked.emit(place)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        screen = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 2))
        painter.setBrush(QColor(20, 22, 30))
        painter.drawRoundedRect(screen, 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRect(QRectF(screen.left() + 6, screen.top() + 6, screen.width() - 12, 22))
        painter.drawRoundedRect(QRectF(screen.left() + 6, screen.bottom() - 30, screen.width() - 12, 24), 6, 6)
        painter.setBrush(QColor(255, 255, 255, 60))
        for index in range(7):  # angedeutete App-Symbole in der Taskbar
            painter.drawRoundedRect(QRectF(screen.center().x() - 70 + index * 20, screen.bottom() - 25, 14, 14), 3, 3)
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        for place in self.SPOTS:
            rect = self._spot_rect(place)
            chosen = place == self.current
            colour = self.accent if chosen else QColor(255, 255, 255, 90 if place == self.hovered else 40)
            painter.setPen(QPen(colour, 2))
            painter.setBrush(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 90) if chosen else QColor(0, 0, 0, 90))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor("#FFFFFF") if chosen or place == self.hovered else QColor(255, 255, 255, 150))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "12:34" if chosen else "·")
        if self.stacking():
            rect = self.tools_rect()
            painter.setPen(QPen(QColor(255, 255, 255, 140 if self.hovered == "tools" else 70), 2))
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 150))
            for index in range(4):  # angedeutete Knöpfe der Symbolleiste
                painter.drawRoundedRect(QRectF(rect.center().x() - 33 + index * 18, rect.center().y() - 5, 12, 10), 3, 3)
        painter.end()
