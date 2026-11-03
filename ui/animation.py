"""Weiches Ein- und Ausblenden für alle PopWin-Fenster, abschaltbar in den Einstellungen."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPoint, Qt, QTimer, QVariantAnimation
from PySide6.QtWidgets import QLabel, QMenu, QWidget


def animation_duration(settings: dict) -> int:
    return settings["duration"] if settings["enabled"] else 0


def fade_ghost(ctx, widget: QWidget) -> None:
    """Qt-Menüs verschwinden sofort; ein Standbild blendet an ihrer Stelle weich aus."""
    duration = animation_duration(ctx.config.get("animations"))
    if not duration or not widget.isVisible():
        return
    ghost = QLabel(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
                   | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                   | Qt.WindowType.WindowDoesNotAcceptFocus)
    ghost.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    ghost.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    ghost.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    ghost.setPixmap(widget.grab())
    ghost.setGeometry(widget.geometry())
    ghost.setWindowOpacity(widget.windowOpacity())
    ghost.show()
    fade = QVariantAnimation(ghost)
    fade.setDuration(duration)
    fade.setStartValue(ghost.windowOpacity())
    fade.setEndValue(0.0)
    fade.valueChanged.connect(ghost.setWindowOpacity)
    fade.finished.connect(ghost.close)
    fade.start()


def animate_menu(ctx, menu: QMenu) -> None:
    animator = WindowAnimator(ctx, menu)
    opacity = menu.windowOpacity()
    menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: animator.enter(opacity, slide=-6)))
    menu.aboutToHide.connect(lambda: fade_ghost(ctx, menu))


class WindowAnimator(QObject):
    """Blendet ein Fenster ein oder aus und schiebt es dabei ein Stück.

    Leisten stehen in echten Pixeln und übergeben deshalb mit `shift` ihre eigene Verschiebung.
    """

    def __init__(self, ctx, window, shift: Callable[[int], None] | None = None):
        super().__init__(window)
        self.ctx = ctx
        self.window = window
        self.closing = False
        self._shift = shift or self._move
        self._base = QPoint()
        self._slide = 0
        self._target = 1.0
        self._after: Callable[[], None] | None = None
        self.length = 0  # eigene Dauer statt der allgemeinen, aus bleibt aus
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._done)

    @property
    def running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def _move(self, offset: int) -> None:
        self.window.move(self._base + QPoint(0, offset))

    def _step(self, value: float) -> None:
        self.window.setWindowOpacity(self._target * value)
        self._shift(round((1 - value) * self._slide))

    def duration(self) -> int:
        duration = animation_duration(self.ctx.config.get("animations"))
        return self.length if duration and self.length else duration

    def _run(self, start: float, end: float) -> None:
        self._animation.stop()
        self._animation.setDuration(self.duration())
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def enter(self, target: float = 1.0, slide: int = 0) -> None:
        self.closing = False
        self._after = None
        self._target, self._slide = target, slide
        self._base = self.window.pos()
        if self.duration() == 0:
            self.window.setWindowOpacity(target)
            return
        self._run(0.0, 1.0)

    def leave(self, after: Callable[[], None] | None = None) -> None:
        if self.closing or not self.window.isVisible():
            return
        self.closing = True
        self._after = after
        running = self.running
        current = self._animation.currentValue() if running else 1.0
        if not running:  # mitten im Einblenden ist die aktuelle Lage noch verschoben, _base stimmt schon
            self._base = self.window.pos()
        if self.duration() == 0:
            self._done()
            return
        self._run(current, 0.0)

    def _done(self) -> None:
        if not self.closing:
            return
        self.closing = False
        self.window.hide()
        self._shift(0)
        self.window.setWindowOpacity(self._target)
        after, self._after = self._after, None
        if after:
            after()
