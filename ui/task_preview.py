"""Vorschau beim Draufbleiben auf einem Task: Name, Schließen, Task beenden, Live-Bild des Fensters."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from core import win_api
from ui.animation import WindowAnimator
from ui.base import G, IconButton, ThemedWindow, icon_for_target
from ui.drag_switch import DragHover

CARD_WIDTH, THUMB_HEIGHT = 220, 130
MAX_CARDS = 4
HEADER_RESERVE = 110  # Platz für „Task beenden“ neben dem Namen


class WindowCard(QWidget):
    """Eine Karte pro Fenster; das Bild zeichnet DWM selbst in das reservierte Feld."""

    def __init__(self, preview: "TaskPreview", window: win_api.WindowInfo):
        super().__init__(objectName="PreviewCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.preview = preview
        self.window = window
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 4, 4, 6)
        box.setSpacing(4)
        head = QHBoxLayout()
        title = QLabel(objectName="MediaTitle")
        title.setText(title.fontMetrics().elidedText(window.title, Qt.TextElideMode.ElideRight, CARD_WIDTH - 44))
        close = IconButton(G.CLOSE, "Fenster schließen")
        close.clicked.connect(lambda: (win_api.close_window(window.hwnd), preview.dismiss()))
        head.addWidget(title, 1)
        head.addWidget(close)
        box.addLayout(head)
        self.thumb = QWidget()
        self.thumb.setFixedHeight(THUMB_HEIGHT)
        box.addWidget(self.thumb)
        # Browser-Oberflächen wie die von Steam zeichnen minimiert nichts mehr, DWM zeigt dann eine weiße Fläche.
        self.blank = window.from_helper and win_api.is_minimized(window.hwnd)
        if self.blank:
            icon = QLabel(self.thumb, alignment=Qt.AlignmentFlag.AlignCenter)
            icon.setPixmap(icon_for_target(window.exe).pixmap(QSize(64, 64)))
            QVBoxLayout(self.thumb).addWidget(icon)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:  # Mittelklick schließt das Fenster, wie bei Browser-Tabs
            win_api.close_window(self.window.hwnd)
        else:
            win_api.activate_window(self.window.hwnd)
        self.preview.dismiss()


class TaskPreview(ThemedWindow):
    """Undurchsichtig, weil DWM in transparente Fenster keine Live-Bilder zeichnen kann."""
    DELAY_SHOW, DELAY_HIDE = 350, 250

    def __init__(self, ctx):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(ctx, flags, translucent=False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("Panel")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.animator = WindowAnimator(ctx, self)
        self.button = None
        self._thumbnails: list[int] = []
        self._cards: list[WindowCard] = []
        self.placed: list[tuple[int, int, int, int]] = []
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(10, 8, 10, 10)
        self.outer.setSpacing(6)
        self.header = QHBoxLayout()
        self.name = QLabel(objectName="PanelTitle")
        self.kill = QPushButton("Task beenden", objectName="Danger")
        self.kill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.kill.setToolTip("Beendet die App sofort wie im Task-Manager. Ungespeichertes geht verloren.")
        self.kill.clicked.connect(self._kill)
        self.header.addWidget(self.name, 1)
        self.header.addWidget(self.kill)
        self.outer.addLayout(self.header)
        self.cards = QHBoxLayout()
        self.cards.setSpacing(6)
        self.outer.addLayout(self.cards)
        self._show_timer = QTimer(self, singleShot=True, interval=self.DELAY_SHOW)
        self._show_timer.timeout.connect(self._present)
        self._hide_timer = QTimer(self, singleShot=True, interval=self.DELAY_HIDE)
        self._hide_timer.timeout.connect(self.dismiss)
        self.setAcceptDrops(True)  # nur zum Hinhalten beim Ziehen; ablegen lässt sich hier nichts
        self._drag_hover = DragHover(self)
        self._drag_hover.held.connect(self._drag_held)

    # --- Steuerung durch die Taskbar ------------------------------------------
    def hover(self, button) -> None:
        if not self.ctx.config.get("taskbar.preview"):
            return
        self._hide_timer.stop()
        if button is self.button and self.isVisible() and not self.animator.closing:
            return  # zurück auf denselben Task: nichts neu aufbauen
        self.button = button
        self._show_timer.start(60 if self.isVisible() else self.DELAY_SHOW)

    def unhover(self) -> None:
        self._show_timer.stop()
        self._hide_timer.start()

    def enterEvent(self, event) -> None:
        self._hide_timer.stop()

    def leaveEvent(self, event) -> None:
        self._hide_timer.start()

    def dismiss(self) -> None:
        self._show_timer.stop()
        self._drag_hover.leave()
        self.animator.leave(self._release)

    # --- Hinhalten beim Ziehen ---------------------------------------------------
    def show_for_drag(self, button) -> None:
        """Die Taskbar hält etwas Gezogenes über einem Task mit mehreren Fenstern: sofort zeigen, auch ohne Einstellung."""
        self._hide_timer.stop()
        self._show_timer.stop()
        if button is self.button and self.isVisible() and not self.animator.closing:
            return
        self.button = button
        self._present()

    def _card_at(self, position: QPoint) -> "WindowCard | None":
        widget = self.childAt(position)
        while widget is not None and widget is not self:
            if isinstance(widget, WindowCard):
                return widget
            widget = widget.parentWidget()
        return None

    def dragEnterEvent(self, event) -> None:
        self._hide_timer.stop()
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:
        self._drag_hover.over(self._card_at(event.position().toPoint()))
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drag_hover.leave()
        self._hide_timer.start()

    def dropEvent(self, event) -> None:
        self._drag_hover.leave()
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.ignore()

    def _drag_held(self, card) -> None:
        if card in self._cards:
            win_api.activate_for_drop(card.window.hwnd)
            self.dismiss()

    def _release(self) -> None:
        for handle in self._thumbnails:
            win_api.thumbnail_unregister(handle)
        self._thumbnails.clear()

    # --- Anzeige ----------------------------------------------------------------
    def _present(self) -> None:
        button = self.button
        if button is None or not button.isVisible():
            return
        self._release()
        for card in self._cards:
            self.cards.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards.clear()
        windows = button.windows[:MAX_CARDS]
        self.kill.setVisible(bool(windows) and not any(w.popwin_app in win_api.INTERNAL_APPS for w in windows))
        # Lange Namen kürzen, sonst bestimmt der Name die Fensterbreite statt der Karten.
        room = max(1, len(windows)) * CARD_WIDTH - (HEADER_RESERVE if windows else 0)
        self.name.setText(self.name.fontMetrics().elidedText(button.display_name(), Qt.TextElideMode.ElideRight, room))
        below = button.dock.edge == "top"
        # Der Name steht immer an der Taskbar, die Tabs liegen davor.
        self.outer.setDirection(QBoxLayout.Direction.TopToBottom if below else QBoxLayout.Direction.BottomToTop)
        self.fill_cards(windows)
        self.apply_theme()
        # Sofort neu berechnen: Die alte Größe und alte Karten gelten beim Wechsel zwischen Tasks nicht mehr.
        self.outer.invalidate()
        self.outer.activate()
        self.resize(self.outer.sizeHint())
        self.outer.activate()
        anchor = button.mapToGlobal(QPoint(button.width() // 2, 0))
        area = button.screen().availableGeometry()
        x = max(area.left() + 6, min(anchor.x() - self.width() // 2, area.right() - self.width() - 6))
        y = button.mapToGlobal(QPoint(0, button.height())).y() + 10 if below else anchor.y() - self.height() - 10
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if button.dock.edge in ("left", "right"):
            side_y = button.mapToGlobal(QPoint(0, button.height() // 2)).y() - self.height() // 2
            y = max(area.top() + 6, min(side_y, area.bottom() - self.height() - 6))
            x = (button.dock.mapToGlobal(QPoint(button.dock.width(), 0)).x() + 10 if button.dock.edge == "left"
                 else button.dock.mapToGlobal(QPoint(0, 0)).x() - self.width() - 10)
        self.move(x, y)
        # --- [Antigravity Ende: Sidebar Support] ---
        fresh = not self.isVisible() or self.animator.closing  # [Antigravity: Auch beim Schließen neu einblenden]
        self.show()
        hwnd = int(self.winId())
        win_api.set_zorder(hwnd, win_api.HWND_TOPMOST)
        # Neue Karten bekommen ihre Position erst, wenn das Fenster sichtbar ist. Vorher gemessen landet
        # das Live-Bild am falschen Platz und überdeckt alles.
        QTimer.singleShot(0, self._attach_thumbnails)
        if fresh:
            self.animator.enter(1.0, slide=-8 if below else 8)

    def fill_cards(self, windows: list[win_api.WindowInfo]) -> None:
        for window in windows:
            card = WindowCard(self, window)
            self.cards.addWidget(card)
            # Sofort zeigen: Solange das Fenster noch offen ist, zählt die Höhe sonst ohne die Karten,
            # die Vorschau sitzt zu tief und wird in den Bildschirmrand gedrückt.
            card.show()
            self._cards.append(card)

    def _attach_thumbnails(self) -> None:
        self.outer.activate()
        hwnd = int(self.winId())
        ratio = self.devicePixelRatio()
        self._release()
        self.placed = []
        for card in self._cards:
            handle = 0 if card.blank else win_api.thumbnail_register(hwnd, card.window.hwnd)
            if not handle:
                continue
            self._thumbnails.append(handle)
            corner = card.thumb.mapTo(self, QPoint(0, 0))
            left, top = round(corner.x() * ratio), round(corner.y() * ratio)
            rect = (left, top, left + round(card.thumb.width() * ratio), top + round(card.thumb.height() * ratio))
            win_api.thumbnail_show(handle, rect)
            self.placed.append(rect)

    def _kill(self) -> None:
        if self.button:
            for pid in {w.main_pid for w in self.button.windows}:
                win_api.terminate_process(pid)
        self.dismiss()
