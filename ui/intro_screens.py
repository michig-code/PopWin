"""Einrichtung beim ersten Start und „Was ist neu“ nach einem Update.

Look wie eine dunkle Neon-Webseite: tiefes Violett, schwebende Lichtkugeln, feines Raster, ein Lichtstreif, der
langsam darüberzieht, Pillen-Knöpfe mit Verlauf. Bewegung wie in einer Handy-App: Seiten gleiten seitlich herein,
das große Symbol springt federnd auf, Inhalte steigen gestaffelt von unten ein, der aktive Seitenpunkt wird zur
Pille. Im Sparmodus bleibt der Hintergrund still und alles erscheint sofort."""
from __future__ import annotations

import math
import re
import time

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation, QRectF, QSequentialAnimationGroup, Qt,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton,
    QVBoxLayout, QWidget,
)

from core.version import VERSION

PURPLE, PINK, CYAN, GREEN = "#8B5CF6", "#FF2FB3", "#38E8FF", "#7CFF6B"
BG, BG2 = "#0B0816", "#120C24"
FRAME_MS = 33  # Hintergrund mit etwa 30 Bildern pro Sekunde, nur solange das Fenster offen ist
PAGE_MS = 380
STAGGER_MS = 70
SHEET = f"""
QWidget#IntroRoot {{ background: transparent; }}
QLabel {{ color: #F4F0FF; background: transparent; font-family: "Segoe UI Variable Display", "Segoe UI"; }}
QLabel#IntroTitle {{ font-size: 30px; font-weight: 800; }}
QLabel#IntroLead {{ font-size: 15px; color: rgba(244, 240, 255, 0.72); }}
QLabel#IntroSmall {{ font-size: 12px; color: rgba(244, 240, 255, 0.55); }}
QLabel#IntroBadge {{ font-size: 12px; font-weight: 700; color: {PINK}; letter-spacing: 2px; }}
QPushButton#IntroPrimary {{ color: #FFFFFF; font-size: 15px; font-weight: 700; border: none; border-radius: 20px;
    padding: 10px 30px; min-height: 20px; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PURPLE}, stop:1 {PINK}); }}
QPushButton#IntroPrimary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #A27BFF, stop:1 #FF5CC6); }}
QPushButton#IntroPrimary:pressed {{ padding-top: 11px; padding-bottom: 9px; }}
QPushButton#IntroGhost {{ color: #F4F0FF; font-size: 14px; font-weight: 600; border-radius: 20px; padding: 9px 24px; min-height: 20px;
    background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.14); }}
QPushButton#IntroGhost:hover {{ border-color: {PURPLE}; background: rgba(139, 92, 246, 0.14); }}
QPushButton#IntroLink {{ color: rgba(244, 240, 255, 0.6); border: none; background: transparent; font-size: 13px; padding: 6px 10px; }}
QPushButton#IntroLink:hover {{ color: #FFFFFF; }}
QToolButton#IntroCard {{ color: #F4F0FF; font-size: 13px; font-weight: 600; text-align: left; border-radius: 16px;
    padding: 14px; background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08); }}
QToolButton#IntroCard:hover {{ border-color: rgba(139, 92, 246, 0.8); background: rgba(139, 92, 246, 0.1); }}
QToolButton#IntroCard:checked {{ border: 2px solid {PINK}; background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 rgba(139, 92, 246, 0.28), stop:1 rgba(255, 47, 179, 0.22)); }}
QWidget#IntroPanel {{ background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; }}
QCheckBox {{ color: #F4F0FF; font-size: 14px; spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.3);
    background: rgba(255, 255, 255, 0.05); }}
QCheckBox::indicator:checked {{ border-color: {PINK}; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PURPLE}, stop:1 {PINK}); }}
"""


# --- Changelog kurz und knapp -------------------------------------------------------------
def changelog_sections(text: str) -> list[tuple[str, list[str]]]:
    """[(Version, [Punkte])] aus CHANGELOG.md, neueste zuerst."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections and line.lstrip().startswith(("- ", "* ")):
            sections[-1][1].append(line.lstrip()[2:].strip())
    return sections


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3]) or (0,)


def sections_since(sections: list[tuple[str, list[str]]], seen: str, limit: int = 3) -> list[tuple[str, list[str]]]:
    """Was seit `seen` dazugekommen ist; ohne bekannte Vorversion nur die neueste."""
    if not seen:
        return sections[:1]
    newer = [section for section in sections if version_key(section[0]) > version_key(seen)]
    return newer[:limit]


def short_point(line: str, limit: int = 96) -> str:
    """Ein Stichpunkt: die fette Überschrift, sonst der Teil vor dem ersten Doppelpunkt oder Satzende."""
    bold = re.match(r"\*\*(.+?)\*\*", line)
    if bold:
        text = bold.group(1).rstrip(":").strip()
        rest = line[bold.end():].lstrip(" :")
        detail = re.split(r"(?<=[.!?])\s|;\s", rest, maxsplit=1)[0].strip() if rest else ""
        if detail and len(text) + len(detail) + 3 <= limit:
            text = f"{text}: {detail.rstrip('.')}"
    else:
        text = re.sub(r"\*\*|`", "", line)
        text = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        if len(text) > limit:  # lieber an einer natürlichen Stelle kürzen als mitten im Satz
            text = re.split(r" \(|; | – |, (?=[a-zäöüß])", text, maxsplit=1)[0]
    text = text.strip().rstrip(".")
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


# --- Bausteine ------------------------------------------------------------------------------
class GlowBackdrop(QWidget):
    """Dunkler Verlauf, drei schwebende Lichtkugeln, feines Raster und ein Lichtstreif."""

    def __init__(self, parent: QWidget, animated: bool):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animated = animated
        self.start = time.monotonic()
        self.phase = 0.0
        self.timer = QTimer(self, interval=FRAME_MS)
        self.timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.animated:
            self.timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.timer.stop()

    def _tick(self) -> None:
        self.phase = time.monotonic() - self.start
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 22, 22)
        painter.setClipPath(clip)
        base = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        base.setColorAt(0, QColor(BG2))
        base.setColorAt(1, QColor(BG))
        painter.fillRect(rect, base)
        t, w, h = self.phase, rect.width(), rect.height()
        size = max(w, h)
        for color, x, y, radius, speed, alpha in ((PURPLE, 0.1, 0.0, 0.55, 26, 150), (PINK, 0.95, 0.15, 0.48, 32, 110),
                                                    (CYAN, 0.45, 1.05, 0.42, 36, 80)):
            drift = math.sin(t * 2 * math.pi / speed)
            center = QPointF(w * (x + 0.07 * drift), h * (y + 0.08 * math.cos(t * 2 * math.pi / speed)))
            glow = QRadialGradient(center, size * radius * (1 + 0.06 * drift))
            tone = QColor(color)
            tone.setAlpha(alpha)
            glow.setColorAt(0, tone)
            tone.setAlpha(0)
            glow.setColorAt(1, tone)
            painter.fillRect(rect, glow)
        # Raster, das langsam nach unten wandert und zur Mitte hin sichtbarer ist
        step = 44
        offset = (t * 6) % step
        for index in range(int(h // step) + 2):
            y = index * step + offset - step
            fade = max(0.0, 1 - abs(y - h * 0.35) / (h * 0.7))
            painter.setPen(QPen(QColor(139, 92, 246, int(34 * fade)), 1))
            painter.drawLine(QPointF(0, y), QPointF(w, y))
        for index in range(int(w // step) + 1):
            x = index * step
            fade = max(0.0, 1 - abs(x - w / 2) / (w * 0.6))
            painter.setPen(QPen(QColor(255, 47, 179, int(26 * fade)), 1))
            painter.drawLine(QPointF(x, 0), QPointF(x, h))
        # Lichtstreif, alle 14 Sekunden einmal quer darüber
        sweep = ((t % 14) / 14) * (w * 2.2) - w * 0.6
        light = QLinearGradient(QPointF(sweep - 160, 0), QPointF(sweep + 160, h * 0.3))
        light.setColorAt(0, QColor(255, 255, 255, 0))
        light.setColorAt(0.5, QColor(255, 255, 255, 16))
        light.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(rect, light)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)


class HeroIcon(QWidget):
    """Großes rundes Symbol mit Verlauf und Leuchten; springt federnd auf (wie in RemoteMC)."""

    def __init__(self, glyph: str, size: int = 112):
        super().__init__()
        self.glyph = glyph
        self.scale = 1.0
        self.setFixedSize(size + 40, size + 40)
        self.diameter = size
        self.animation = QVariantAnimation(self, startValue=0.7, endValue=1.0)
        bounce = QEasingCurve(QEasingCurve.Type.OutBack)
        bounce.setOvershoot(2.2)  # federnd wie ein Frühling, nicht nur ein kleiner Überschwinger
        self.animation.setEasingCurve(bounce)
        self.animation.valueChanged.connect(self._scaled)

    def pop(self, duration: int) -> None:
        if not duration:
            self._scaled(1.0)
            return
        self.animation.stop()
        self.animation.setDuration(int(duration * 1.6))
        self.animation.start()

    def _scaled(self, value) -> None:
        self.scale = float(value)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = self.diameter / 2 * self.scale
        halo = QRadialGradient(center, radius * 1.35)
        halo.setColorAt(0.6, QColor(255, 47, 179, 90))
        halo.setColorAt(1, QColor(255, 47, 179, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, radius * 1.35, radius * 1.35)
        fill = QLinearGradient(center - QPointF(radius, radius), center + QPointF(radius, radius))
        fill.setColorAt(0, QColor(PURPLE))
        fill.setColorAt(1, QColor(PINK))
        painter.setBrush(fill)
        painter.drawEllipse(center, radius, radius)
        font = QFont("Segoe Fluent Icons")
        font.setPixelSize(max(8, int(radius * 0.9)))
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
                         Qt.AlignmentFlag.AlignCenter, self.glyph)


class PageDots(QWidget):
    """Seitenpunkte; der aktive wächst zur Pille."""

    def __init__(self, count: int):
        super().__init__()
        self.count = count
        self.current = 0
        self.widths = [24.0] + [8.0] * (count - 1)
        self.setFixedSize(count * 16 + 24, 12)
        self.animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=260)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._step)
        self._from: list[float] = list(self.widths)

    def set_current(self, index: int, animate: bool = True) -> None:
        self.current = index
        self._from = list(self.widths)
        if animate:
            self.animation.stop()
            self.animation.start()
        else:
            self._step(1.0)

    def _step(self, value) -> None:
        value = float(value)
        targets = [24.0 if i == self.current else 8.0 for i in range(self.count)]
        self.widths = [a + (b - a) * value for a, b in zip(self._from, targets)]
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        x = (self.width() - (sum(self.widths) + 8 * (self.count - 1))) / 2
        for index, width in enumerate(self.widths):
            if index == self.current:
                fill = QLinearGradient(x, 0, x + width, 0)
                fill.setColorAt(0, QColor(PURPLE))
                fill.setColorAt(1, QColor(PINK))
                painter.setBrush(fill)
            else:
                painter.setBrush(QColor(255, 255, 255, 50))
            painter.drawRoundedRect(QRectF(x, 2, width, 8), 4, 4)
            x += width + 8


def rise_in(widgets: list[QWidget], duration: int, owner: QWidget) -> None:
    """Gestaffelt von unten einblenden (fadeUp). Danach wird der Effekt wieder entfernt, er kostet sonst Zeit."""
    if not duration:
        return
    for number, widget in enumerate(widgets):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", owner)
        fade.setDuration(duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        group = QSequentialAnimationGroup(owner)
        group.addPause(number * STAGGER_MS)
        group.addAnimation(fade)
        group.finished.connect(lambda w=widget: w.setGraphicsEffect(None) if _alive(w) else None)
        group.start(QSequentialAnimationGroup.DeletionPolicy.DeleteWhenStopped)


def _alive(widget: QWidget) -> bool:
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


class IntroWindow(QWidget):
    """Rahmenloses Fenster mit Neon-Hintergrund und gleitenden Seiten."""
    finished = Signal()

    def __init__(self, ctx, size=(980, 640)):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.ctx = ctx
        self.setObjectName("IntroRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(SHEET)
        self.resize(*size)
        level = ctx.config.get("performance.level", "balanced")
        self.duration = 0 if level == "saver" or not ctx.config.get("animations.enabled", True) else PAGE_MS
        self.backdrop = GlowBackdrop(self, animated=bool(self.duration))
        self.pages: list[QWidget] = []
        self.index = -1
        self.stage = QWidget(self)
        self._drag: QPoint | None = None
        self._slides: QParallelAnimationGroup | None = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.backdrop.setGeometry(self.rect())
        self.stage.setGeometry(self.rect().adjusted(0, 0, 0, -self.footer_height()))
        for page in self.pages:
            page.resize(self.stage.size())

    def footer_height(self) -> int:
        return 0

    def add_page(self, page: QWidget) -> QWidget:
        page.setParent(self.stage)
        page.resize(self.stage.size())
        page.hide()
        self.pages.append(page)
        return page

    def go(self, index: int) -> None:
        if not 0 <= index < len(self.pages) or index == self.index:
            return
        old = self.pages[self.index] if self.index >= 0 else None
        forward = index > self.index
        self.index = index
        new = self.pages[index]
        new.resize(self.stage.size())
        new.show()
        new.raise_()
        width = self.stage.width()
        if self._slides is not None:
            self._slides.stop()
            for page in self.pages:
                if page is not new:
                    page.move(0, 0)
                    if page is not old:
                        page.hide()
        if old is not None and self.duration:
            group = QParallelAnimationGroup(self)
            for page, start, end in ((new, width if forward else -width, 0), (old, 0, -width // 3 if forward else width // 3)):
                slide = QPropertyAnimation(page, b"pos", group)
                slide.setDuration(self.duration)
                slide.setStartValue(QPoint(start, 0))
                slide.setEndValue(QPoint(end, 0))
                slide.setEasingCurve(QEasingCurve.Type.OutCubic)
                group.addAnimation(slide)
            group.finished.connect(lambda o=old: (o.hide(), o.move(0, 0)) if _alive(o) and o is not self.pages[self.index] else None)
            self._slides = group
            group.start()
        else:
            if old is not None:
                old.hide()
            new.move(0, 0)
        self.page_shown(index)

    def page_shown(self, index: int) -> None:
        page = self.pages[index]
        hero = page.findChild(HeroIcon)
        if hero is not None:
            hero.pop(self.duration)
        rise_in([w for w in page.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                 if not isinstance(w, HeroIcon) and w.isVisibleTo(page)], self.duration, self)

    def present(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        self.setWindowOpacity(0.0 if self.duration else 1.0)
        self.show()
        self.raise_()
        self.activateWindow()
        if self.duration:
            fade = QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(260)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def done(self) -> None:
        self.finished.emit()
        if not self.duration:
            self.close()
            return
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(200)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.finished.connect(self.close)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # Fenster an jeder freien Stelle verschieben
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.done()
        else:
            super().keyPressEvent(event)


def _label(text: str, name: str, wrap: bool = True, center: bool = True) -> QLabel:
    label = QLabel(text, objectName=name)
    label.setWordWrap(wrap)
    if center:
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _page(glyph: str, badge: str, title: str, lead: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    box = QVBoxLayout(page)
    box.setContentsMargins(70, 40, 70, 10)
    box.setSpacing(10)
    box.addStretch(1)
    if glyph:
        hero = HeroIcon(glyph)
        box.addWidget(hero, 0, Qt.AlignmentFlag.AlignHCenter)
    box.addWidget(_label(badge, "IntroBadge"))
    box.addWidget(_label(title, "IntroTitle"))
    box.addWidget(_label(lead, "IntroLead"))
    return page, box


def _card(title: str, text: str, group: list[QToolButton]) -> QToolButton:
    card = QToolButton(objectName="IntroCard")
    card.setText(f"{title}\n{text}")
    card.setCheckable(True)
    card.setAutoExclusive(False)
    card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    card.setMinimumSize(220, 76)
    card.setCursor(Qt.CursorShape.PointingHandCursor)
    group.append(card)
    return card


def _row(*widgets: QWidget) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 6, 0, 0)
    row.setSpacing(12)
    row.addStretch(1)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return holder


# --- Einrichtung ------------------------------------------------------------------------------
class SetupWizard(IntroWindow):
    STEPS = ("Willkommen", "Theme", "Leisten", "Nova", "Leistung", "Fertig")

    def __init__(self, ctx):
        super().__init__(ctx)
        from ui import base
        G = base.G
        self.footer = QWidget(self)
        foot = QHBoxLayout(self.footer)
        foot.setContentsMargins(28, 0, 28, 18)
        self.back = QPushButton("Zurück", objectName="IntroLink")
        self.back.clicked.connect(lambda: self.go(self.index - 1))
        self.skip = QPushButton("Überspringen", objectName="IntroLink")
        self.skip.clicked.connect(self.done)
        self.dots = PageDots(len(self.STEPS))
        self.next = QPushButton("Weiter", objectName="IntroPrimary")
        self.next.clicked.connect(self._next)
        foot.addWidget(self.back)
        foot.addWidget(self.skip)
        foot.addStretch(1)
        foot.addWidget(self.dots)
        foot.addStretch(1)
        foot.addWidget(self.next)

        page, box = _page(G.START, f"POPWIN {VERSION}", "Willkommen bei PopWin",
                          "Dein Windows, nur schöner: eigene Leisten, Widgets, Themes und auf Wunsch eine KI. "
                          "In einer Minute ist alles eingerichtet, und alles lässt sich später ändern.")
        box.addStretch(1)
        self.add_page(page)

        page, box = _page("", "SCHRITT 1", "Wähle dein Theme", "Ein Klick zeigt es sofort. Eigene Themes baust du später unter Einstellungen → Themes.")
        from ui.theme_page import ThemeCard
        grid_holder = QWidget()
        grid = QGridLayout(grid_holder)
        grid.setSpacing(10)
        self.theme_cards: list[ThemeCard] = []
        for number, (name, label) in enumerate(ctx.theme.all_themes().items()):
            card = ThemeCard(name, label)
            card.setFixedSize(132, 96)
            card.clicked.connect(lambda _c=False, n=name: self._pick_theme(n))
            grid.addWidget(card, number // 5, number % 5)
            self.theme_cards.append(card)
        box.addWidget(grid_holder, 0, Qt.AlignmentFlag.AlignHCenter)
        self.light = QCheckBox("Heller Modus")
        self.light.setChecked(ctx.theme.mode == "light")
        self.light.toggled.connect(lambda on: ctx.set_dark_mode("light" if on else "dark"))
        box.addWidget(_row(self.light))
        box.addStretch(1)
        self._mark_theme()
        self.add_page(page)

        page, box = _page(G.APP, "SCHRITT 2", "Deine Leisten", "Wie soll die Taskbar aussehen?")
        self.dock_cards: list[QToolButton] = []
        cards = {"full": _card("Ganze Breite", "Klassisch wie Windows", self.dock_cards),
                 "fit": _card("Nur die Icons", "Schmal in der Mitte", self.dock_cards),
                 "float": _card("Schwebendes Dock", "Runde Pille mit Abstand", self.dock_cards)}
        for key, card in cards.items():
            card.clicked.connect(lambda _c=False, k=key: self._pick_dock(k))
        box.addWidget(_row(*cards.values()))
        self.magnify = QCheckBox("Icons wachsen, wenn die Maus näher kommt (Beta)")
        self.magnify.setChecked(bool(ctx.config.get("taskbar.magnify")))
        self.magnify.toggled.connect(lambda on: ctx.config.set("taskbar.magnify", on))
        box.addWidget(_row(self.magnify))
        box.addStretch(1)
        self._mark_dock()
        self.add_page(page)

        page, box = _page(G.NOVA, "SCHRITT 3", "Das ist Nova",
                          "Nova ist deine KI direkt in PopWin: Sie beantwortet Fragen, erklärt, was auf dem Bildschirm "
                          "ist, und erledigt Dinge für dich. Möchtest du Nova haben?")
        self.nova_yes = QPushButton("Ja, gerne", objectName="IntroPrimary")
        self.nova_no = QPushButton("Nein danke", objectName="IntroGhost")
        self.nova_yes.clicked.connect(lambda: self._nova(True))
        self.nova_no.clicked.connect(lambda: self._nova(False))
        self.nova_choice = _row(self.nova_yes, self.nova_no)
        box.addWidget(self.nova_choice)
        self.nova_answer = _label("", "IntroLead")
        self.nova_answer.hide()
        box.addWidget(self.nova_answer)
        box.addStretch(1)
        self.add_page(page)

        page, box = _page(G.QUICK, "SCHRITT 4", "Leistung", "Wie viel darf PopWin für Effekte tun?")
        self.power_cards: list[QToolButton] = []
        power = {"saver": _card("Sparsam", "Keine Animationen, ideal für schwache PCs", self.power_cards),
                 "balanced": _card("Ausgewogen", "Empfohlen", self.power_cards),
                 "beautiful": _card("Schön", "Alle Effekte", self.power_cards)}
        for key, card in power.items():
            card.clicked.connect(lambda _c=False, k=key: self._pick_power(k))
        box.addWidget(_row(*power.values()))
        self.game = QCheckBox("Spielmodus: Leisten ruhen, solange ein Vollbild-Spiel läuft")
        self.game.setChecked(bool(ctx.config.get("performance.game_mode", True)))
        self.game.toggled.connect(lambda on: ctx.config.set("performance.game_mode", on))
        box.addWidget(_row(self.game))
        box.addStretch(1)
        self._mark_power()
        self.add_page(page)

        page, box = _page("\uE73E", "FERTIG", "Alles bereit!",
                          "Diese Einrichtung findest du jederzeit wieder unter Einstellungen → Setup.")
        self.autostart = QCheckBox("PopWin mit Windows starten")
        box.addWidget(_row(self.autostart))
        box.addStretch(1)
        self.add_page(page)
        self.go(0)
        self._update_footer()

    def footer_height(self) -> int:
        return 76

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.footer.setGeometry(0, self.height() - self.footer_height(), self.width(), self.footer_height())

    def go(self, index: int) -> None:
        super().go(index)
        if hasattr(self, "dots"):
            self.dots.set_current(self.index, bool(self.duration))
            self._update_footer()

    def _update_footer(self) -> None:
        last = self.index == len(self.pages) - 1
        self.back.setVisible(self.index > 0)
        self.skip.setVisible(not last)
        self.next.setText("PopWin starten" if last else "Los geht's" if self.index == 0 else "Weiter")

    def _next(self) -> None:
        if self.index == len(self.pages) - 1:
            self._finish()
        else:
            self.go(self.index + 1)

    def _finish(self) -> None:
        if self.autostart.isChecked():
            from core import win_api
            from core.paths import app_shortcut
            try:
                win_api.set_autostart(True, *app_shortcut("main"))
            except Exception:  # Autostart ist nett, aber kein Grund, die Einrichtung abzubrechen
                pass
        self.done()

    # Auswahl
    def _pick_theme(self, name: str) -> None:
        self.ctx.theme.set_theme(name)
        self._mark_theme()

    def _mark_theme(self) -> None:
        for card in self.theme_cards:
            card.setChecked(card.name == self.ctx.theme.current)

    def dock_kind(self) -> str:
        profile = self.ctx.config.monitor(None)
        return "float" if profile.get("dock_floating") else profile.get("dock_style", "full")

    def _pick_dock(self, kind: str) -> None:
        config = self.ctx.config
        config.set_monitor(None, "dock_floating", kind == "float")
        config.set_monitor(None, "dock_style", "fit" if kind == "float" else kind)
        self._mark_dock()

    def _mark_dock(self) -> None:
        kind = self.dock_kind()
        for key, card in zip(("full", "fit", "float"), self.dock_cards):
            card.setChecked(key == kind)

    def _pick_power(self, level: str) -> None:
        self.ctx.config.set("performance.level", level)
        self._mark_power()

    def _mark_power(self) -> None:
        level = self.ctx.config.get("performance.level", "balanced")
        for key, card in zip(("saver", "balanced", "beautiful"), self.power_cards):
            card.setChecked(key == level)

    def _nova(self, wanted: bool) -> None:
        """Bei Nein verschwinden die Knöpfe, und Nova verkriecht sich in den Einstellungen."""
        self.ctx.config.set("nova.enabled", wanted)
        hotkey = str(self.ctx.config.get("nova.hotkey") or "").replace("+", " + ").title()
        self.nova_answer.setText(
            f"Super! Nova sitzt in der Taskbar{f' und kommt mit {hotkey}' if hotkey else ''}." if wanted else
            "Alles klar. Nova hat sich in den Einstellungen verkrochen. Unter Einstellungen → Nova holst du sie "
            "jederzeit zurück.")
        self.nova_choice.hide()
        self.nova_answer.show()
        rise_in([self.nova_answer], self.duration, self)


# --- Was ist neu ------------------------------------------------------------------------------
class WhatsNew(IntroWindow):
    def __init__(self, ctx, sections: list[tuple[str, list[str]]]):
        super().__init__(ctx, size=(760, 600))
        from ui import base
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(48, 34, 48, 26)
        box.setSpacing(10)
        hero = HeroIcon(base.G.QUICK, 76)
        box.addWidget(hero, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(_label("UPDATE", "IntroBadge"))
        box.addWidget(_label(f"Neu in PopWin {sections[0][0] if sections else VERSION}", "IntroTitle"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                             "QScrollBar:vertical { background: transparent; width: 8px; }"
                             f"QScrollBar::handle:vertical {{ background: {PURPLE}; border-radius: 4px; min-height: 40px; }}"
                             "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
                             "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }")
        holder = QWidget()
        list_box = QVBoxLayout(holder)
        list_box.setContentsMargins(0, 6, 0, 0)
        list_box.setSpacing(10)
        scroll.setWidget(holder)
        for version, points in sections:
            panel = QWidget(objectName="IntroPanel")
            panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            inner = QVBoxLayout(panel)
            inner.setContentsMargins(18, 12, 18, 12)
            inner.setSpacing(6)
            if len(sections) > 1:
                inner.addWidget(_label(f"Version {version}", "IntroBadge", center=False))
            for point in points[:8]:
                line = _label(f"<span style='color:{PINK}'>●</span>&nbsp;&nbsp;{short_point(point)}", "IntroLead",
                              center=False)
                line.setTextFormat(Qt.TextFormat.RichText)
                inner.addWidget(line)
            if len(points) > 8:
                inner.addWidget(_label(f"… und {len(points) - 8} weitere Verbesserungen", "IntroSmall", center=False))
            list_box.addWidget(panel)
        list_box.addStretch(1)
        box.addWidget(scroll, 1)
        close = QPushButton("Los geht's", objectName="IntroPrimary")
        close.clicked.connect(self.done)
        box.addWidget(_row(close))
        self.add_page(page)
        self.go(0)
