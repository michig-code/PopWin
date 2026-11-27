"""Hinhalten beim Ziehen: Bleibt ein gezogenes Element kurz über einem Ziel stehen, meldet sich das Ziel.
So wechselt die Taskbar zur App unter dem Zeiger, ohne dass man loslassen muss. Kennt Windows nicht, nur Qt."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

HOLD_MS = 500  # so lange muss das Gezogene über einem Task stehen, wie bei der Windows-Taskleiste


class DragHover(QObject):
    """Ein Ziel und eine Uhr. Die Uhr läuft nur neu an, wenn das Ziel wechselt: Wer auf einem Task bleibt,
    löst genau einmal aus, auch wenn Windows die Bewegung laufend neu meldet."""
    held = Signal(object)

    def __init__(self, parent=None, hold_ms: int = HOLD_MS):
        super().__init__(parent)
        self.target = None
        self._timer = QTimer(self, singleShot=True, interval=hold_ms)
        self._timer.timeout.connect(self._fire)

    def active(self) -> bool:
        return self.target is not None

    def over(self, target) -> None:
        """`None` heißt: über nichts, das wechseln kann."""
        if target is self.target or (target is not None and target == self.target):
            return
        self.target = target
        if target is None:
            self._timer.stop()
        else:
            self._timer.start()

    def leave(self) -> None:
        self.over(None)

    def _fire(self) -> None:
        if self.target is not None:
            self.held.emit(self.target)  # das Ziel bleibt gemerkt: kein zweites Auslösen ohne Wechsel
