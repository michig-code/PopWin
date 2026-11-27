"""Weiches Ein- und Ausblenden für alle PopWin-Fenster, abschaltbar in den Einstellungen."""
from __future__ import annotations

from typing import Callable

import shiboken6
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPoint, Qt, QTimer, QVariantAnimation
from PySide6.QtWidgets import QLabel, QMenu, QWidget

_ACTIVE_GHOSTS: set[QWidget] = set()


def is_reduced(settings: dict | None) -> bool:
    return bool(settings and settings.get("reduced", False))


def animation_duration(settings: dict | None, scale: float = 1.0, *, max_reduced: int = 90, max_duration: int = 0,
                       level: str = "balanced") -> int:
    """Im Sparmodus (performance.level = "saver") gibt es keine Animationen, egal was unter Animationen steht."""
    if not settings or not settings.get("enabled", True) or level == "saver":
        return 0
    base = max(0, round(settings.get("duration", 220) * scale))
    if max_duration > 0:
        base = min(base, max_duration)
    if is_reduced(settings):
        return min(base, max_reduced) if max_reduced > 0 else 0
    return base


def duration_of(config, scale: float = 1.0, *, max_reduced: int = 90, max_duration: int = 0) -> int:
    return animation_duration(config.get("animations"), scale, max_reduced=max_reduced, max_duration=max_duration,
                              level=config.get("performance.level", "balanced"))


def fade_ghost(ctx, widget: QWidget) -> None:
    """Qt-Menüs verschwinden sofort; ein Standbild blendet an ihrer Stelle weich aus."""
    if not shiboken6.isValid(widget) or not widget.isVisible():
        return
    settings = ctx.config.get("animations")
    if is_reduced(settings):
        return
    duration = duration_of(ctx.config, scale=0.65, max_reduced=0)
    if duration <= 0:
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
    _ACTIVE_GHOSTS.add(ghost)
    ghost.destroyed.connect(lambda _o=None, g=ghost: _ACTIVE_GHOSTS.discard(g))
    ghost.show()
    fade = QVariantAnimation(ghost)
    fade.setEasingCurve(QEasingCurve.Type.OutCubic)
    fade.setDuration(duration)
    fade.setStartValue(ghost.windowOpacity())
    fade.setEndValue(0.0)

    def _step(value: float) -> None:
        if shiboken6.isValid(ghost):
            ghost.setWindowOpacity(value)

    def _finish() -> None:
        _ACTIVE_GHOSTS.discard(ghost)
        if shiboken6.isValid(ghost):
            ghost.close()

    fade.valueChanged.connect(_step)
    fade.finished.connect(_finish)
    fade.start()


def animate_menu(ctx, menu: QMenu) -> None:
    animator = WindowAnimator(ctx, menu, scale=0.65, max_reduced=60)
    opacity = menu.windowOpacity()

    def _enter_if_valid() -> None:
        if shiboken6.isValid(menu) and shiboken6.isValid(animator) and menu.isVisible():
            animator.enter(opacity, slide=-6)

    def _on_about_to_show() -> None:
        if shiboken6.isValid(animator):
            QTimer.singleShot(0, animator, _enter_if_valid)

    def _on_about_to_hide() -> None:
        if shiboken6.isValid(animator):
            animator._animation.stop()
        if shiboken6.isValid(menu):
            fade_ghost(ctx, menu)

    menu.aboutToShow.connect(_on_about_to_show)
    menu.aboutToHide.connect(_on_about_to_hide)


class WindowAnimator(QObject):
    """Blendet ein Fenster ein oder aus und schiebt es dabei ein Stück.

    Leisten stehen in echten Pixeln und übergeben deshalb mit `shift` ihre eigene Verschiebung.
    """

    def __init__(self, ctx, window, shift: Callable[[int], None] | None = None, *,
                 scale: float = 0.9, max_reduced: int = 80, max_duration: int = 0):
        super().__init__(window)
        self.ctx = ctx
        self.window = window
        self.closing = False
        self._own_shift = shift is None
        self._shift = shift or self._move
        self._base = QPoint()
        self._offset = 0  # zuletzt von _move gesetzter Versatz
        self._slide = 0
        self._target = 1.0
        self._after: Callable[[], None] | None = None
        self.scale = scale
        self.max_reduced = max_reduced
        self.max_duration = max_duration
        self.length = 0  # eigene Dauer statt der allgemeinen, aus bleibt aus
        self._quick = 0  # Dauer nur für das laufende Ein- oder Ausblenden
        self._hide = True
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._done)

    @property
    def running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def _move(self, offset: int) -> None:
        if shiboken6.isValid(self.window):
            self._offset = offset
            self.window.move(self._base + QPoint(0, offset))

    def _step(self, value: float) -> None:
        if not shiboken6.isValid(self.window):
            self._animation.stop()
            return
        self.window.setWindowOpacity(self._target * value)
        self._shift(round((1 - value) * self._slide))

    def duration(self) -> int:
        settings = self.ctx.config.get("animations")
        if self.ctx.config.get("performance.level", "balanced") == "saver":
            return 0
        custom = self._quick or self.length
        if custom:
            if not settings or not settings.get("enabled", True):
                return 0
            if is_reduced(settings):
                return min(custom, self.max_reduced) if self.max_reduced > 0 else 0
            return custom
        return animation_duration(
            settings, scale=self.scale, max_reduced=self.max_reduced, max_duration=self.max_duration
        )

    def _run(self, start: float, end: float) -> None:
        self._animation.stop()
        self._animation.setDuration(self.duration())
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def enter(self, target: float = 1.0, slide: int = 0, length: int = 0) -> None:
        if not shiboken6.isValid(self.window):
            return
        was_running = self.running
        was_closing = self.closing
        self.closing = False
        self._after = None
        self._quick = length
        self._target = target
        self._slide = 0 if is_reduced(self.ctx.config.get("animations")) else slide
        if not was_running:
            self._base = self.window.pos()
        elif was_closing and self._own_shift and self.window.pos() != self._base + QPoint(0, self._offset):
            # Mitten im Ausblenden neu geöffnet und dabei woanders hingesetzt (z. B. an die Leiste eines anderen
            # Monitors): Die neue Stelle gilt, sonst schiebt jeder Schritt das Fenster an die alte zurück.
            self._base = self.window.pos()
        if self.duration() == 0:
            self._animation.stop()
            self._shift(0)
            self.window.setWindowOpacity(target)
            return
        # Sofort durchsichtig, nicht erst mit dem ersten Animationsschritt: Sonst blitzt das Fenster
        # einen Moment voll auf und kommt danach noch einmal herein.
        self._animation.stop()
        self._step(0.0)
        self._run(0.0, 1.0)

    def leave(self, after: Callable[[], None] | None = None, hide: bool = True, length: int = 0) -> None:
        """hide=False: Nur ausblenden, das Fenster bleibt offen und steht danach wieder voll da - `after` legt es vorher weg."""
        if not shiboken6.isValid(self.window) or self.closing or not self.window.isVisible():
            return
        self.closing = True
        self._after = after
        self._hide = hide
        self._quick = length
        running = self.running
        current = self._animation.currentValue() if running else 1.0
        if not running:  # mitten im Einblenden ist die aktuelle Lage noch verschoben, _base stimmt schon
            self._base = self.window.pos()
        if is_reduced(self.ctx.config.get("animations")):
            self._slide = 0
        if self.duration() == 0:
            self._animation.stop()
            self._done()
            return
        self._run(current, 0.0)

    def _done(self) -> None:
        if not self.closing:
            return
        self.closing = False
        after, self._after = self._after, None
        if not shiboken6.isValid(self.window):
            self._hide = True
        elif not self._hide:
            # Erst wegräumen (etwa hinter das Spiel), dann wieder voll zeigen - sonst blitzte es vorn noch einmal auf.
            self._hide = True
            if after:
                after()
            self._shift(0)
            self.window.setWindowOpacity(self._target)
            return
        else:
            self.window.hide()
            self._shift(0)
            self.window.setWindowOpacity(self._target)
        if after:
            after()
