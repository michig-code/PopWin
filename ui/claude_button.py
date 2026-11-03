"""Nur im Dev-Build: Ganz links in der Taskbar sitzt ein Knopf für die Claude-App. Ihr Fenster steht dafür nicht mehr
zwischen den Tasks, so bleibt die Taskbar für die übrigen Programme frei.

Ein Klick öffnet Claude, holt es nach vorn oder legt es ab, wenn es schon vorn ist, wie bei einem Task.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath

from core import win_api
from ui.base import IconButton

FALLBACK_TARGET = "shell:AppsFolder\\Claude_pzs8sxrjxfjjc!Claude"
STORE_FAMILY = "claude_pzs8sxrjxfjjc"
RAYS = 11
LONG, SHORT = 0.47, 0.33  # Länge der Strahlen, abwechselnd, als Anteil der Knopfgröße


def is_claude_window(window) -> bool:
    """Das Fenster der Claude-App, nicht etwa ein Terminal, in dem Claude Code läuft."""
    exe = (window.exe or "").lower().replace("/", "\\")
    aumid = (window.aumid or "").lower()
    return aumid.startswith(STORE_FAMILY) or (f"\\windowsapps\\{STORE_FAMILY.split('_')[0]}_" in exe and exe.endswith("\\claude.exe"))


def claude_target(ctx) -> str:
    """Wo Claude installiert ist, steht im App-Verzeichnis; sonst die bekannte Store-Kennung."""
    found = next((app.target for app in ctx.apps.search("Claude", 10)
                  if app.name.lower() == "claude" and "shell:appsfolder" in app.target.lower()), "")
    return found or FALLBACK_TARGET


def claude_windows(ctx) -> list:
    return [window for window in ctx.windows() if is_claude_window(window)]


def press(ctx) -> str:
    """Was der Klick tut: öffnen, nach vorn holen oder ablegen. Liefert, was passiert ist, für die Tests."""
    windows = claude_windows(ctx)
    if not windows:
        win_api.shell_open(claude_target(ctx))
        return "geöffnet"
    foreground = win_api.foreground_window()
    front = next((window for window in windows if window.hwnd == foreground), None)
    if front is not None:
        win_api.minimize_window(front.hwnd)
        return "abgelegt"
    win_api.activate_window(windows[0].hwnd)
    return "nach vorn"


def paint_spark(painter: QPainter, center: QPointF, size: float, color: QColor) -> None:
    """Das Claude-Zeichen als einfarbiger Strahlenstern, ohne den farbigen Hintergrund des App-Symbols."""
    path = QPainterPath()
    for index in range(RAYS):
        angle = math.tau * index / RAYS - math.pi / 2
        length = size * (LONG if index % 2 == 0 else SHORT)
        width = size * 0.075
        tip = QPointF(center.x() + math.cos(angle) * length, center.y() + math.sin(angle) * length)
        side = QPointF(-math.sin(angle) * width, math.cos(angle) * width)
        ray = QPainterPath()
        ray.moveTo(center + side)
        ray.lineTo(tip + side * 0.35)
        ray.lineTo(tip - side * 0.35)
        ray.lineTo(center - side)
        ray.closeSubpath()
        path = path.united(ray)
    path = path.united(_disc(center, size * 0.12))
    painter.fillPath(path, color)


def _disc(center: QPointF, radius: float) -> QPainterPath:
    disc = QPainterPath()
    disc.addEllipse(center, radius, radius)
    return disc


class ClaudeButton(IconButton):
    def __init__(self, ctx):
        super().__init__("", "Claude", name="Trash")
        self.ctx = ctx
        self.clicked.connect(lambda: press(ctx))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # Hintergrund und Hover wie bei den anderen Knöpfen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        light = self.ctx.theme.mode == "light"
        size = min(self.width(), self.height()) * 0.62
        paint_spark(painter, QPointF(self.width() / 2, self.height() / 2), size, QColor("#202020" if light else "#FFFFFF"))
        painter.end()
