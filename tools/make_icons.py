"""Zeichnet die Symbole der PopWin-Apps schlicht und im Stil von Windows neu: python tools/make_icons.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPen  # noqa: E402

from ui.base import G  # noqa: E402

SIZE = 256


def folder(painter: QPainter) -> None:
    """Die gelbe Mappe, die man vom Windows-Explorer kennt."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#E9A820"))
    painter.drawRoundedRect(QRectF(16, 44, 104, 72), 16, 16)
    painter.drawRoundedRect(QRectF(16, 66, 224, 146), 16, 16)
    front = QLinearGradient(0, 88, 0, 216)
    front.setColorAt(0, QColor("#FFD766"))
    front.setColorAt(1, QColor("#FFC22E"))
    painter.setBrush(front)
    painter.drawRoundedRect(QRectF(16, 88, 224, 128), 16, 16)


def glyph(character: str, top: str, bottom: str, weight: float = 0):
    """Ein einzelnes Zeichen ohne Hintergrund, in einem ruhigen Verlauf."""

    def draw(painter: QPainter) -> None:
        font = QFont("Segoe Fluent Icons")
        font.setPixelSize(216)
        painter.setFont(font)
        fill = QLinearGradient(0, 20, 0, 236)
        fill.setColorAt(0, QColor(top))
        fill.setColorAt(1, QColor(bottom))
        painter.setPen(QPen(fill, weight or 1))
        painter.drawText(QRectF(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, character)
    return draw


def console(fill: str):
    """Das schwarze oder blaue Fenster mit Eingabezeichen, wie Windows Terminal und PowerShell."""

    def draw(painter: QPainter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(14, 34, 228, 188), 22, 22)
        pen = QPen(QColor("#FFFFFF"), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(58, 88), QPointF(104, 128), QPointF(58, 168)])
        painter.drawLine(QPointF(124, 172), QPointF(190, 172))
    return draw


def page(painter: QPainter) -> None:
    """Das Notizblatt von Notepad."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#F4F7FB"))
    painter.drawRoundedRect(QRectF(40, 22, 176, 216), 20, 20)
    painter.setBrush(QColor("#3E8EDE"))
    painter.drawRoundedRect(QRectF(40, 22, 176, 44), 20, 20)
    painter.setBrush(QColor("#8FA3B7"))
    for y in (96, 132, 168):
        painter.drawRoundedRect(QRectF(66, y, 124, 12), 6, 6)


ICONS = {
    "terminal": console("#20242B"),
    "studio": console("#1F5FA8"),
    "editor": page,
    "explorer": folder,
    "settings": glyph(G.SETTINGS, "#9FB0C2", "#6F8296"),
    "nova": glyph(G.NOVA, "#6FB7F2", "#3E8EDE"),
    "widgets": glyph(G.WIDGETS, "#6FB7F2", "#3E8EDE"),
    "stream": glyph(G.DISPLAY, "#9FA8F2", "#5865F2"),
}


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 - Schriften brauchen eine laufende Anwendung
    target = ROOT / "assets" / "apps"
    target.mkdir(exist_ok=True)
    for name, draw in ICONS.items():
        image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        draw(painter)
        painter.end()
        image.save(str(target / f"{name}.png"))
        image.save(str(target / f"{name}.ico"))


if __name__ == "__main__":
    main()
