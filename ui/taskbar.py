"""Dock: Start, angeheftete und laufende Apps, Now Playing, Papierkorb."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QBoxLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget,  # [Antigravity]
)

from core import win_api
from core.monitor_manager import native_rect
from ui.animation import duration_of
from core.notifications import badge_count, badge_text, notice_labels, visible_notices
from core.replacements import launch, taskbar_slot
from core.dev import is_dev
from ui.claude_button import ClaudeButton, is_claude_window
from ui.clock_place import ClockFace, ClockPill
from ui.dock_tools import TOOL_ITEMS, ToolRow
from ui.drag_switch import DragHover
from ui import sidebar  # [Antigravity]
from ui.base import BarWindow, G, IconButton, NovaButton, glyph_icon, icon_for_target, in_hot_zone, set_prop, themed_menu  # noqa: F401


NOVA_ICON = 24


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{value:.1f} TB".replace(".", ",")


def aligned_x(align: str, left: int, right: int, width: int) -> int:
    return {"left": left, "right": right - width}.get(align, (left + right - width) // 2)


def pin_for_path(path: str) -> dict:
    """Pin für eine abgelegte Datei. Bei Verknüpfungen merkt "exe", welches Programm dahintersteckt,
    damit das laufende Programm mit dem Pin zusammenfällt statt ein zweites Icon zu bekommen."""
    name = os.path.splitext(os.path.basename(path))[0] or path
    pin = {"name": name, "target": path}
    if path.lower().endswith(".lnk"):
        exe = os.path.basename(win_api.shortcut_target(path)).lower()
        if exe == "update.exe":  # Discord & Co. starten über einen Updater, laufen aber als <Name>.exe
            exe = f"{name.lower()}.exe"
        elif not exe and "explorer" in name.lower():  # Die Explorer-Verknüpfung hat kein Dateiziel
            exe = "explorer.exe"
        if exe:
            pin["exe"] = exe
    return pin


def pin_key(pin: dict) -> str:
    """Store-Apps haben kein .exe-Ziel; "exe" verknüpft den Pin mit dem laufenden Prozess."""
    return pin.get("exe") or os.path.basename(pin["target"]).lower()


MIN_NAME_MATCH = 4  # kürzere Namen träfen zufällig fremde Programme


def plain_name(text: str) -> str:
    return "".join(character for character in text.lower() if character.isalnum())


def pin_key_of_window(exe: str, pins: list[dict]) -> str:
    """Der Pin, zu dem ein laufendes Programm gehört, auch wenn die .exe anders heißt als die Verknüpfung:
    Die WhatsApp-Verknüpfung startet zum Beispiel WhatsApp.Root.exe aus dem Store-Paket. Leer, wenn keiner passt."""
    package = win_api.packaged_app_target(exe).lower()
    stem = plain_name(os.path.basename(exe).split(".")[0])
    for pin in pins:
        if package and pin["target"].lower() == package:
            return pin_key(pin)
    for pin in pins:
        if len(stem) >= MIN_NAME_MATCH and plain_name(pin["name"]) == stem:
            return pin_key(pin)
    return ""


def web_app_name(app_id: str, title: str) -> str:
    """15EF7777.Crunchyroll_mgdgtskya6f22!App heißt Crunchyroll; installierte Seiten nennt nur der Fenstertitel."""
    match = re.match(r"[^.]+\.([^_!]+)_", app_id)
    return match[1] if match else title.split(" - ")[0].strip()


def web_app_key(app_id: str, name: str, pins: list[dict]) -> str:
    """Web-Apps bekommen ein eigenes Icon und fallen mit ihrem Pin zusammen, nie mit dem Browser."""
    target = f"shell:AppsFolder\\{app_id}".lower()
    for pin in pins:
        if pin["target"].lower() == target or (len(plain_name(name)) >= MIN_NAME_MATCH and plain_name(pin["name"]) == plain_name(name)):
            return pin_key(pin)
    return f"webapp:{app_id.lower()}"


def merge_pins(pinned: list[dict], new: list[dict]) -> list[dict]:
    known = {pin_key(p) for p in pinned}
    merged = list(pinned)
    for pin in new:
        if pin_key(pin) not in known:
            known.add(pin_key(pin))
            merged.append(pin)
    return merged


def ordered_keys(keys: list[str], order: list[str]) -> list[str]:
    """Feste Reihenfolge: Bekannte stehen wie gespeichert, Neue kommen hinten dazu."""
    position = {key: index for index, key in enumerate(order)}
    return sorted(keys, key=lambda key: position.get(key, len(order)))


def moved_key(keys: list[str], key: str, index: int) -> list[str]:
    rest = [k for k in keys if k != key]
    rest.insert(max(0, min(index, len(rest))), key)
    return rest


def synced_order(shared: list[str], pins: list[str], groups: list[str]) -> list[str]:
    """Für jede Taskbar dieselbe Folge: angeheftete Apps vorn, offene dahinter, verschoben wird nur innerhalb."""
    pinned = [key for key in shared if key in pins] + [key for key in pins if key not in shared]
    running = [key for key in shared if key not in pins] + [key for key in groups if key not in pins and key not in shared]
    return pinned + running


def shows_all_windows(window_filter: str, primary: bool) -> bool:
    """smart: Hauptmonitor zeigt alles, Nebenmonitore ihre eigenen. own/all: überall gleich."""
    if window_filter == "smart":
        return primary
    return window_filter == "all"


MEDIA_WIDTHS = {"controls": 118, "cover": 88}  # die anderen Stile nehmen die eingestellte Breite
SEARCH_WIDTHS = {"pill": 96, "field": 170, "wide": 260}
GLIDE_MS = 170  # wie schnell die Icons Platz machen
SHARED_ORDER = "alle"  # Eintrag in taskbar.order, wenn die Icons synchron sind
UNLIMITED_WIDTH = 16777215  # Qts QWIDGETSIZE_MAX, in PySide nicht verfügbar
LOGO = Path(__file__).resolve().parent.parent / "assets" / "popwin.png"


class MediaBox(QWidget):
    """Now Playing - wird auch vom Desktop-Widget genutzt."""

    def __init__(self, ctx, compact: bool = False, width: int = 250):
        super().__init__(objectName="MediaBox")
        self.ctx = ctx
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(width)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)
        self.thumb = QLabel()
        text = QVBoxLayout()
        text.setSpacing(0)
        text.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(objectName="MediaTitle")
        self.artist = QLabel(objectName="MediaArtist")
        text.addStretch(1)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addStretch(1)
        self.prev = IconButton(G.PREV, "Zurück")
        self.play = IconButton(G.PLAY, "Wiedergabe/Pause")
        self.next = IconButton(G.NEXT, "Weiter")
        self.prev.clicked.connect(ctx.media.previous)
        self.play.clicked.connect(ctx.media.play_pause)
        self.next.clicked.connect(ctx.media.next)
        row.addWidget(self.thumb)
        row.addLayout(text, 1)
        row.addWidget(self.prev)
        row.addWidget(self.play)
        row.addWidget(self.next)
        self._thumb_data = None
        self._last = None
        self.allowed = True
        self.style_name = "full"
        self.full_width = width
        self.set_compact(compact)
        ctx.media.updated.connect(self.update_media)
        self.update_media(ctx.last_media)

    def set_compact(self, compact: bool) -> None:
        """Vom UI-Stil vorgegeben: Kompakte Themes haben keinen Platz für Cover und Künstler."""
        self.theme_compact = compact
        self._apply_style()

    def set_style(self, style: str, width: int) -> None:
        self.style_name = style
        self.full_width = width
        self._apply_style()

    def _apply_style(self) -> None:
        style = self.style_name
        self.compact = self.theme_compact or style == "compact"
        side = 26 if self.compact else 38
        self.thumb.setVisible(style == "cover" or (style == "full" and not self.compact))
        self.thumb.setFixedSize(side, side)
        self.title.setVisible(style in ("full", "compact"))
        self.artist.setVisible(style == "full" and not self.compact)
        self.prev.setVisible(style != "cover")
        self.next.setVisible(style != "cover")
        self.setFixedWidth(MEDIA_WIDTHS.get(style, self.full_width))
        self._thumb_data = None  # das Cover wird in der neuen Größe neu zugeschnitten

    def update_media(self, media: dict | None) -> None:
        self._last = media
        self.setVisible(self.allowed and bool(media and media["title"]))
        if not media:
            return
        width = max(60, self.width() - (100 if self.compact else 150))
        metrics = self.title.fontMetrics()
        self.title.setText(metrics.elidedText(media["title"], Qt.TextElideMode.ElideRight, width))
        self.artist.setText(self.artist.fontMetrics().elidedText(media["artist"], Qt.TextElideMode.ElideRight, width))
        self.title.setToolTip(f"{media['title']}\n{media['artist']}".strip())
        self.play.setText(G.PAUSE if media["playing"] else G.PLAY)
        if media["thumbnail"] != self._thumb_data:
            self._thumb_data = media["thumbnail"]
            pixmap = QPixmap()
            if self._thumb_data and pixmap.loadFromData(self._thumb_data):
                side = self.thumb.height()
                # Viele Streams liefern 16:9 - mittig quadratisch zuschneiden.
                scaled = pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
                x, y = (scaled.width() - side) // 2, (scaled.height() - side) // 2
                self.thumb.setPixmap(scaled.copy(x, y, side, side))
            else:
                self.thumb.clear()


class Trash(IconButton):
    def __init__(self, ctx, theme_name: str):
        super().__init__(G.TRASH, "Papierkorb", name="Trash")
        self.ctx = ctx
        self.theme_name = theme_name
        self.setAcceptDrops(True)
        self.clicked.connect(lambda: os.startfile("shell:RecycleBinFolder"))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            set_prop(self, "dropping", True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        set_prop(self, "dropping", False)

    def dropEvent(self, event) -> None:
        set_prop(self, "dropping", False)
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        event.acceptProposedAction()
        # Der Explorer wartet, bis dieser Handler zurückkehrt - erst danach löschen.
        QTimer.singleShot(0, lambda: win_api.move_to_recycle_bin(paths))

    def contextMenuEvent(self, event) -> None:
        # --- [Antigravity Start] Selbstreparatur bei Rechtsklick auf den Papierkorb (Rest: Claude Code) ---
        if isinstance(self.window(), Dock):
            self.window().repair(rebuild=False)
        # --- [Antigravity Ende] ---
        count, size = win_api.recycle_bin_info()
        menu = themed_menu(self.ctx, self, self.theme_name)
        info = menu.addAction(f"{count} Elemente · {format_size(size)}")
        info.setEnabled(False)
        menu.addSeparator()
        empty = menu.addAction("Papierkorb leeren")
        empty.setEnabled(count > 0)
        empty.triggered.connect(lambda: win_api.empty_recycle_bin(int(self.winId())))
        menu.popup(event.globalPos())
        win_api.force_foreground(int(menu.winId()))


MAGNIFY_TOP = 1.4  # so viel größer wird das Icon direkt unter der Maus
MAGNIFY_REACH = 2.5  # so viele Icon-Breiten weit wirkt die Vergrößerung zur Seite
MAGNIFY_MS = 16
APPROACH_MS = 90  # so oft schaut die Taskbar, ob die Maus näher kommt, solange die Vergrößerung an ist
APPROACH_ZONE = 1.6  # ab so vielen Leistenhöhen Abstand beginnen die Icons zu wachsen
FIXED_KEYS = ("start", "search", "apps", "media", "nova", "trash", "desktop")
FIXED_GLIDE_MS = 170


def dock_order_of(value) -> list[str]:
    """Bereinigte Reihenfolge der festen Knöpfe: Unbekanntes fällt weg, Fehlendes kommt an seinen Standardplatz."""
    order = [key for key in dict.fromkeys(value or []) if key in FIXED_KEYS]
    for index, key in enumerate(FIXED_KEYS):
        if key not in order:
            before = [k for k in FIXED_KEYS[:index] if k in order]
            order.insert(order.index(before[-1]) + 1 if before else 0, key)
    return order


def nearness(cursor_x: int, cursor_y: int, left: int, top: int, right: int, bottom: int, edge: str, zone: float,
             side_reach: float) -> float:
    """0 = weit weg, 1 = auf der Leiste. Zählt nur der Abstand zur Bildschirmseite hin (von oben bei unten
    liegender Taskbar); seitlich gilt ein Streifen in Breite der Leiste plus `side_reach`."""
    if edge in ("left", "right"):
        if not top - side_reach <= cursor_y <= bottom + side_reach:
            return 0.0
        distance = (cursor_x - right) if edge == "left" else (left - cursor_x)
    else:
        if not left - side_reach <= cursor_x <= right + side_reach:
            return 0.0
        distance = (top - cursor_y) if edge == "bottom" else (cursor_y - bottom)
    if distance <= 0:  # auf der Leiste oder zwischen ihr und dem Bildschirmrand
        return 1.0
    value = max(0.0, 1.0 - distance / max(zone, 1.0))
    return value * value * (3 - 2 * value)  # weich hinein, kein Ruck an der Grenze


def magnify_scale(distance: float, reach: float, top: float = MAGNIFY_TOP) -> float:
    """Wie am Mac: direkt unter der Maus am größten, zur Seite weich abfallend, ab `reach` normal groß."""
    if reach <= 0 or distance >= reach:
        return 1.0
    return 1.0 + (top - 1.0) * 0.5 * (1.0 + math.cos(math.pi * max(0.0, distance) / reach))


def mark_y(button_top: int, button_height: int, icon: int, bar_height: int, mark: int) -> float:
    """Genau mittig zwischen Unterkante des Icons und Rand der Leiste."""
    icon_bottom = button_top + (button_height + icon) / 2
    return round((icon_bottom + bar_height - 1) / 2 - mark / 2)


class TaskMarks(QWidget):
    """Malt die Indikatoren über die ganze Leiste: Der Knopf endet vor dem Rand, in ihm kämen sie nicht bis zur Mitte."""

    def __init__(self, dock: "Dock", parent: QWidget):
        super().__init__(parent)
        self.dock = dock
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for button in self.dock._buttons.values():
            if button.isVisible():
                button.paint_mark(painter, self.mapFromGlobal(button.mapToGlobal(QPoint(0, 0))), self.height())
        painter.end()


class TaskButton(QPushButton):
    # Breite des Indikators; die Form verrät den Zustand auch ohne Farben.
    WIDTH_MINIMIZED, WIDTH_SHOWN, WIDTH_FOCUSED = 4, 12, 20
    MARK_HEIGHT = 3
    DOT, DOT_FOCUSED = 4, 6
    COUNT_SIZE = 14
    LABEL_WIDTH = 110

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.dock.marks.update()  # beim Ziehen wandert der Indikator mit

    def mark_color(self) -> tuple[int, QColor]:
        meta = self.dock.meta()
        light = self.dock.ctx.theme.mode == "light"
        if self.property("focused"):
            width, color = self.WIDTH_FOCUSED, QColor(meta["ind_active_light" if light else "ind_active"])
        elif self.property("shown"):
            width, color = self.WIDTH_SHOWN, QColor(meta["ind_light" if light else "ind"])
        else:
            width, color = self.WIDTH_MINIMIZED, QColor(128, 128, 128, 170)
        return width, color

    def paint_mark(self, painter: QPainter, origin: QPoint, bar_height: int) -> None:
        if not self.property("running") or self.dock.task_indicator == "none":
            return
        width, color = self.mark_color()
        height = self.MARK_HEIGHT
        if self.dock.task_indicator == "dot":
            width = height = self.DOT_FOCUSED if self.property("focused") else self.DOT
        painter.setBrush(color)
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.dock.edge in ("left", "right"):
            mark_w, mark_h = (width, width) if self.dock.task_indicator == "dot" else (height, width)
            x = 2.0 if self.dock.edge == "left" else (self.dock.root.width() - mark_w - 2.0)
            y = origin.y() + (self.height() - mark_h) / 2
            painter.drawRoundedRect(QRectF(x, y, mark_w, mark_h), mark_w / 2, mark_w / 2)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        x = origin.x() + (self.width() - width) / 2
        y = mark_y(origin.y(), self.height(), self.iconSize().height(), bar_height, height)
        painter.drawRoundedRect(QRectF(x, y, width, height), height / 2, height / 2)

    BADGE_COLOR = "#E5484D"

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        counts = self.property("running") and self.dock.task_counts and len(self.windows) > 1
        if counts or self.badge:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            if counts:  # mit rotem Punkt wandert die Fensterzahl nach links oben
                self._paint_count(painter, self.mark_color()[1], left=bool(self.badge))
            if self.badge:
                self._paint_badge(painter)
            painter.end()

    def _paint_badge(self, painter: QPainter) -> None:
        """Roter Punkt mit der Zahl ungelesener Benachrichtigungen, wie bei Handy-Apps."""
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        width = max(self.COUNT_SIZE, painter.fontMetrics().horizontalAdvance(self.badge) + 6)
        box = QRectF(self.width() - width - 1, 1, width, self.COUNT_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.BADGE_COLOR))
        painter.drawRoundedRect(box, self.COUNT_SIZE / 2, self.COUNT_SIZE / 2)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.badge)
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_count(self, painter: QPainter, color: QColor, left: bool = False) -> None:
        box = QRectF(1 if left else self.width() - self.COUNT_SIZE - 1, 1, self.COUNT_SIZE, self.COUNT_SIZE)
        painter.setBrush(color)
        painter.drawEllipse(box)
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF" if color.lightness() < 150 else "#111111"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(min(len(self.windows), 9)))

    def __init__(self, dock: "Dock", key: str):
        super().__init__(objectName="TaskButton")
        self.dock = dock
        self.key = key
        self.windows: list[win_api.WindowInfo] = []
        self.target = ""
        self.name = ""
        self.badge = ""  # Text im roten Punkt, leer = kein Punkt
        self._press: QPoint | None = None
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._activate)

    def display_name(self) -> str:
        if self.name:
            return self.name
        return os.path.splitext(self.key)[0].title()

    # --- Vorschau und freies Verschieben -----------------------------------------
    # --- [Antigravity Start] Reset & Schutz gegen hängende Klick-/Drag-Zustände (Rest: Claude Code) ---
    def reset_state(self) -> None:
        """Räumt hängengebliebene Maus- oder Zieh-Zustände auf, damit der nächste Linksklick sofort wieder zählt."""
        self._dragging = False
        self._press = None
        self.setDown(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
    # --- [Antigravity Ende] ---

    def enterEvent(self, event) -> None:
        self.dock.start_magnify()
        if not self._dragging:
            self.dock.ctx.task_preview.hover(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.dock.ctx.task_preview.unhover()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self.dock.ctx.task_preview.dismiss()
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False  # [Antigravity]
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            # --- [Antigravity Start] Verwaistes Ziehen abfangen ---
            if not self.dock._left_held():
                self.reset_state()
                self.dock.finish_drag()
                return
            # --- [Antigravity Ende] ---
            distance = (event.position().toPoint() - self._press).manhattanLength()
            if not self._dragging and distance >= QApplication.startDragDistance():
                self._dragging = True
                self.setDown(False)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
                if self.dock.edge in ("left", "right"):
                    self.dock.drag_task(self, event.globalPosition().toPoint().y(), self.mapToGlobal(self._press).y())
                else:
                    self.dock.drag_task(self, event.globalPosition().toPoint().x(), self.mapToGlobal(self._press).x())
                # --- [Antigravity Ende: Sidebar Support] ---
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self.reset_state()  # [Antigravity]
            self.dock.finish_drag()
            event.accept()
            return
        self._press = None
        super().mouseReleaseEvent(event)

    def _activate(self) -> None:
        # --- [Antigravity Start] Nur noch existierende Fenster beachten & minimierte Vordergrundfenster aktivieren ---
        live = [w for w in self.windows if win_api.is_window(w.hwnd)] or list(self.windows)
        if not live:
            target = self.target or (self.key if self.key.startswith("popwin:") else "")
            if target:
                launch(self.dock.ctx, target)
        elif len(live) == 1:
            hwnd = live[0].hwnd
            if win_api.foreground_window() == hwnd and not win_api.is_minimized(hwnd):
                win_api.minimize_window(hwnd)
            else:
                win_api.activate_window(hwnd)
        else:
            menu = self._menu()
            for window in live:
                menu.addAction(window.title[:60], lambda h=window.hwnd: win_api.activate_window(h))
            self._popup(menu)
        # --- [Antigravity Ende] ---

    def _menu(self) -> QMenu:
        return themed_menu(self.dock.ctx, self, self.dock.theme_name)

    def _popup(self, menu: QMenu) -> None:
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.dock.edge == "left":
            position = self.mapToGlobal(self.rect().topRight())
            menu.popup(position)
            win_api.force_foreground(int(menu.winId()))
            return
        if self.dock.edge == "right":
            position = self.mapToGlobal(self.rect().topLeft()) - QPoint(menu.sizeHint().width(), 0)
            menu.popup(position)
            win_api.force_foreground(int(menu.winId()))
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        if self.dock.edge == "bottom":
            position = self.mapToGlobal(self.rect().topLeft()) - QPoint(0, menu.sizeHint().height())
        else:
            position = self.mapToGlobal(self.rect().bottomLeft())
        menu.popup(position)
        win_api.force_foreground(int(menu.winId()))

    def contextMenuEvent(self, event) -> None:
        self.dock.repair(rebuild=False)  # [Antigravity: Rechtsklick repariert hängende Taskbar sofort]
        menu = self._menu()
        pinned = self.dock.is_pinned(self.key)
        target = self.target or (self.key if self.key.startswith("popwin:") else "")
        if self.windows and target:
            menu.addAction("Neues Fenster", lambda: launch(self.dock.ctx, target))
        if pinned or target:
            label = "Von Taskbar lösen" if pinned else "An Taskbar anheften"
            menu.addAction(label, lambda: self.dock.toggle_pin(self))
        if self.windows:
            menu.addSeparator()
            label = "Fenster schließen" if len(self.windows) == 1 else "Alle Fenster schließen"
            menu.addAction(label, lambda: [win_api.close_window(w.hwnd) for w in self.windows])
            if not any(w.popwin_app in win_api.INTERNAL_APPS for w in self.windows):
                menu.addAction("Task beenden",
                               lambda: [win_api.terminate_process(p) for p in {w.main_pid for w in self.windows}])
        menu.addSeparator()
        menu.addAction("Taskbar neu laden", self.dock.reload_bar)  # [Antigravity]
        menu.addAction("Taskbar-Einstellungen", lambda: self.dock.ctx.open_settings_at("Taskbar"))
        self.dock.ctx.task_preview.dismiss()
        menu.popup(event.globalPos())
        win_api.force_foreground(int(menu.winId()))


class Dock(BarWindow):
    SIZE_KEY = "dock"

    def __init__(self, ctx, screen, edge: str, theme_name: str = ""):
        super().__init__(ctx, screen, edge, theme_name)
        self._buttons: dict[str, TaskButton] = {}
        self._signature = None
        self.setAcceptDrops(True)
        self._drag_hover = DragHover(self)  # Hinhalten beim Ziehen wechselt zur App
        self._drag_hover.held.connect(self._drag_held)

        row = QHBoxLayout(self.root)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(3)

        self.start = IconButton(G.START, "Start", name="StartButton")
        self.start.clicked.connect(lambda: ctx.toggle_start_menu(self.start))
        self.search = IconButton(G.SEARCH, "Suche", name="StartButton")
        self.search.clicked.connect(ctx.show_launcher)
        # pad gleicht rechts Now Playing und Papierkorb aus, damit "Mitte" die echte Bildschirmmitte ist.
        self.pad = QWidget()
        self.lead = QWidget()
        self.trail = QWidget()
        for spacer in (self.pad, self.lead, self.trail):
            spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.lead.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.trail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.claude = ClaudeButton(ctx) if is_dev() else None  # nur im Dev-Build, ganz links
        if self.claude is not None:
            row.addWidget(self.claude)
        row.addWidget(self.pad)
        row.addWidget(self.lead, 1)
        row.addWidget(self.start)
        row.addWidget(self.search)
        self.apps = QHBoxLayout()
        self.apps.setSpacing(3)
        row.addLayout(self.apps)
        row.addWidget(self.trail, 1)

        self.media = MediaBox(ctx)
        self.trash = Trash(ctx, theme_name)
        self.desktop = IconButton(G.DISPLAY, "Desktop anzeigen", name="Trash")
        self.desktop.clicked.connect(win_api.toggle_desktop)
        self.nova = NovaButton(ctx, NOVA_ICON, name="Trash")
        self.nova.clicked.connect(ctx.nova.toggle)
        row.addWidget(self.media)
        row.addWidget(self.nova)
        row.addWidget(self.trash)
        row.addWidget(self.desktop)
        self.pad_end = QWidget()  # das Gegenstück zu pad, wenn links mehr steht als rechts
        self.pad_end.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.pad_end)
        self.clock = ClockFace(lambda: ctx.config.monitor(self.screen_id))
        self.clock.hide()
        self.pill: ClockPill | None = None
        self._pill_timer = QTimer(self, interval=200)
        self._pill_timer.timeout.connect(self._follow_pill)
        self._clock_at = ""
        self.tools: ToolRow | None = None
        self._tools_moved: list[str] = []
        self.task_indicator = "bar"
        self.task_counts = False
        self.task_badges = True
        self.task_labels = False

        self._order: list[str] = list(ctx.config.get("taskbar.order").get(self.screen_id, []))
        self._dragged: TaskButton | None = None
        self._dropping: TaskButton | None = None
        self._grab_offset = 0
        self._slot_origin: int | None = None
        self._order_held = False  # eine andere Taskbar zieht gerade
        self._glides: dict[TaskButton, QPropertyAnimation] = {}
        self.marks = TaskMarks(self, self.root)
        self._magnify_timer = QTimer(self, interval=MAGNIFY_MS)  # läuft nur, solange die Maus auf den Icons ist
        self._magnify_timer.timeout.connect(self._magnify)
        self._approach_timer = QTimer(self, interval=APPROACH_MS)  # nur bei eingeschalteter Vergrößerung
        self._approach_timer.timeout.connect(self._check_approach)
        self._fixed_order: list[str] = []
        self._fixed_press: tuple[str, QPoint] | None = None
        self._fixed_drag = ""
        self._fixed_glides: dict[QWidget, QPropertyAnimation] = {}
        self.timer = QTimer(self, interval=700)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        ctx.config.changed.connect(self._on_config)
        ctx.media.updated.connect(self._fit_width)  # Now Playing macht die Leiste breiter
        self._on_config("profile")

    def contextMenuEvent(self, event) -> None:
        """Rechtsklick auf die freie Fläche: repariert hängende Zustände sofort und zeigt das Menü."""
        self.repair(rebuild=True)  # [Antigravity: Rechtsklick repariert die Taskbar automatisch]
        menu = themed_menu(self.ctx, self, self.theme_name)
        self.add_bar_options(menu)
        menu.popup(event.globalPos())
        win_api.force_foreground(int(menu.winId()))

    def add_bar_options(self, menu: QMenu) -> None:
        config = self.ctx.config

        def toggle(label: str, field: str) -> None:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.option(field))
            action.triggered.connect(lambda checked: config.set_monitor(self.screen_id, field, checked))

        toggle("Automatisch ausblenden", "auto_hide")
        toggle("Home-Knopf", "show_start")
        toggle("Suche", "show_search")
        toggle("Player", "show_media")
        toggle("Papierkorb", "show_recycle")
        toggle("Nova", "show_nova_button")
        toggle("Knopf „Desktop anzeigen“", "show_desktop")
        if is_dev():  # [Antigravity: Claude-Knopf umschaltbar wie andere Hauptbuttons]
            toggle("Claude-Knopf", "dev_claude_button")
        toggle("App-Namen", "task_labels")
        toggle("Schwebendes Dock", "dock_floating")
        menu.addSeparator()
        for label, value in (("Ganze Bildschirmbreite", "full"), ("Nur so breit wie die Icons", "fit")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.option("dock_style") == value)
            action.triggered.connect(lambda _=False, v=value: config.set_monitor(self.screen_id, "dock_style", v))
        menu.addSeparator()
        menu.addAction("Taskbar neu laden", self.reload_bar)  # [Antigravity]
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if sidebar.sidebar_active():
            pos_menu = menu.addMenu("Taskbar-Position")
            for edge_key, edge_label in sidebar.taskbar_positions().items():
                action = pos_menu.addAction(edge_label)
                action.setCheckable(True)
                action.setChecked(self.edge == edge_key)
                action.triggered.connect(lambda _=False, e=edge_key: config.set_monitor(self.screen_id, "taskbar_position", e))
        # --- [Antigravity Ende: Sidebar Support] ---
        menu.addAction("Task-Manager öffnen", lambda: win_api.shell_open("taskmgr.exe"))
        menu.addAction("Taskbar-Einstellungen", lambda: self.ctx.open_settings_at("Taskbar"))

    # --- [Antigravity Start] Taskbar-Selbstreparatur & manuelles Neu-Laden (Rest: Claude Code) ---
    def _left_held(self) -> bool:
        if not bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            return False
        if self.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
            return True
        return win_api.left_button_down()

    def repair(self, rebuild: bool = True) -> None:
        """Setzt feststeckende Maus-, Drag-, Animations- und Fokus-Zustände der Taskbar zurück."""
        grabber = QWidget.mouseGrabber()
        if grabber is not None and QApplication.activePopupWidget() is None:
            grabber.releaseMouse()
        if getattr(self.ctx, "task_preview", None) is not None:
            self.ctx.task_preview.dismiss()
        for animation in list(self._glides.values()):
            animation.stop()
        self._glides.clear()
        self._dragged = None
        self._dropping = None
        self._order_held = False
        self._slot_origin = None
        self._drag_hover.leave()
        set_prop(self.root, "dropping", False)
        for button in list(self._buttons.values()):
            button.reset_state()
        for dock in self._other_docks():
            dock._order_held = False
            dock._slot_origin = None
        self.setEnabled(True)
        self.root.setEnabled(True)
        if getattr(self, "animator", None) is not None and (self.animator.closing or self.windowOpacity() < 1.0):
            self.animator._animation.stop()
            self.animator._closing = False
            self.setWindowOpacity(1.0)
        if hasattr(self.ctx, "_windows_time"):
            self.ctx._windows_time = 0.0
        self._enforce_placement()
        self._place_in_z()
        if rebuild:
            self._signature = None
        elif self._buttons:
            self._rearrange({key: None for key in self._shown_keys()})
        self.refresh(force=rebuild)

    def reload_bar(self) -> None:
        """Lädt die Taskbar und ihre Icons komplett neu."""
        self.repair(rebuild=True)
        for dock in self._other_docks():
            dock.repair(rebuild=True)
    # --- [Antigravity Ende] ---

    # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
    def _sync_orientation(self) -> None:
        """Schaltet das Layout für die Sidebar (links/rechts) auf vertikal um."""
        vertical = self.edge in ("left", "right")
        row = self.root.layout()
        direction = QBoxLayout.Direction.TopToBottom if vertical else QBoxLayout.Direction.LeftToRight
        if row.direction() != direction:
            row.setDirection(direction)
        if self.apps.direction() != direction:
            self.apps.setDirection(direction)
        if vertical:
            row.setContentsMargins(3, 6, 3, 6)
            self.lead.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.trail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        else:
            row.setContentsMargins(6, 3, 6, 3)
            self.lead.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.trail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    # --- [Antigravity Ende: Sidebar Support] ---

    def _fit_width(self) -> None:
        if self._area and self._fits_content():
            self.apply_geometry()

    def set_theme_name(self, name: str) -> None:
        self.trash.theme_name = name
        super().set_theme_name(name)

    def on_meta_changed(self, meta: dict) -> None:
        self.media.set_compact(meta["compact"] == "1")
        self.media.update_media(self.media._last)
        self._apply_native_backdrop()
        self._round_corners()
        self.update()

    def _apply_native_backdrop(self) -> None:
        """Einzige Stelle, die Blur und Windows-Ecken der Taskbar setzt.
        Den Blur malt Windows immer als Rechteck (höchstens 8 px rund), die runde Maske ignoriert er.
        Bei den runderen Stilen kommt der Hintergrund deshalb von PopWin selbst, siehe paintEvent."""
        hwnd = int(self.winId())
        if self.paints_backdrop():
            win_api.set_blur(hwnd, "none")
            win_api.set_rounded(hwnd, False)
        else:
            meta = self.meta()
            win_api.set_blur(hwnd, meta["blur"], self.ctx.theme.tint(self.theme_name))
            win_api.set_rounded(hwnd, meta["rounded"] == "1" or self._fits_content())
        self._style_search()
        self.refresh(force=True)

    def corner_radius(self) -> int:
        """Windows rundet Fenster höchstens um 8 px. Die Kachel-Stile wollen rundere Ecken (Meta „corner“),
        „Komplett rund“ macht die Leiste zur Pille. Alle anderen Stile behalten die Ecken von Windows."""
        if self.floating():
            return min(self.height(), self.width()) // 2
        radius = int(self.meta().get("corner") or 0)
        if radius and self.ctx.theme.tile_shape == "round":
            radius = self.height()
        return min(radius, self.height() // 2, self.width() // 2)

    def paints_backdrop(self) -> bool:
        """Stile mit runderen Ecken als 8 px bekommen ihren Hintergrund von PopWin statt vom Windows-Blur."""
        return self.floating() or int(self.meta().get("corner") or 0) > 8

    def backdrop_color(self) -> QColor:
        """Die Tönung des Stils, deckender als beim Blur, damit die Leiste ohne Weichzeichner lesbar bleibt."""
        value = self.ctx.theme.tint(self.theme_name).lstrip("#").ljust(8, "F")
        color = QColor(f"#{value[:6]}")
        color.setAlpha(max(int(value[6:8], 16), 0xE0))
        return color

    def _round_corners(self) -> None:
        """Schneidet das Fenster rund zu; außerhalb gehen Klicks an Windows durch.
        Die Maske ist 1 px größer als die gemalte Form, damit deren weiche Kante nicht abgeschnitten wird."""
        radius = self.corner_radius()
        if radius <= 8:
            if not self.mask().isEmpty():
                self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, event) -> None:
        radius = self.corner_radius()
        if radius <= 8 or not self.paints_backdrop():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.backdrop_color())
        if self.floating():  # feiner heller Rand wie bei einer Glas-Pille
            light = self.ctx.theme.mode == "light"
            painter.setPen(QColor(0, 0, 0, 40) if light else QColor(255, 255, 255, 34))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        else:
            painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._round_corners()

    def _style_start(self) -> None:
        style = self.option("start_style")
        if style == "logo":
            self.start.setText("")
            self.start.setIcon(QIcon(str(LOGO)))
            self.start.setIconSize(QSize(22, 22))
        else:
            self.start.setIcon(QIcon())
            self.start.setText(G.WINDOWS if style == "windows" else G.START)

    def _style_search(self) -> None:
        """Lupe oder Suchfeld; die Form kommt aus dem Theme, die Breite aus dem gewählten Stil."""
        style = self.option("search_style")
        field = style in SEARCH_WIDTHS
        color = "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"
        self.search.setText("Suche" if field else G.SEARCH)
        self.search.setIcon(glyph_icon(G.SEARCH, 15, color) if field else QIcon())
        if field:
            self.search.setFixedWidth(SEARCH_WIDTHS[style])
        else:
            self.search.setMinimumWidth(0)
            self.search.setMaximumWidth(UNLIMITED_WIDTH)
        self.search.setObjectName("SearchField" if field else "StartButton")
        self.search.setProperty("searchStyle", style)
        self.search.style().unpolish(self.search)
        self.search.style().polish(self.search)

    def _on_config(self, key: str) -> None:
        if key == "profile" or key.startswith("taskbar") or key in ("nova.enabled", "performance.level"):
            # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
            self._sync_orientation()
            # --- [Antigravity Ende: Sidebar Support] ---
            self._arrange_fixed()
            self._arrange_tools()  # zuerst: Umhängen in ein anderes Fenster blendet ein Widget aus
            self.media.allowed = self.option("show_media") and self.edge not in ("left", "right")  # [Antigravity: Sidebar schmal halten]
            self.media.set_style(self.option("media_style"), self.option("media_width"))
            self.media.update_media(self.media._last)
            if self.claude is not None:  # [Antigravity: Claude-Knopf sofort aktualisieren]
                self.claude.setVisible(self.claude_on_button())
            self.trash.setVisible(self.option("show_recycle"))
            self.nova.setVisible(self.option("show_nova_button") and self.ctx.nova_enabled())
            self.desktop.setVisible(self.option("show_desktop"))
            self.start.setVisible(self.option("show_start"))
            self.search.setVisible(self.option("show_search") and self.edge not in ("left", "right"))  # [Antigravity: Sidebar]
            self._style_start()
            self._style_search()
            self._place_clock()
            self.task_indicator = self.option("task_indicator")
            self.task_counts = self.option("task_counts")
            self.task_badges = self.option("task_badges")
            # Sparsam: seltener nach neuen Fenstern sehen
            self.timer.setInterval(1500 if self.ctx.config.get("performance.level") == "saver" else 700)
            if self.magnifies():
                self._approach_timer.start()
            else:
                self._approach_timer.stop()
            self.task_labels = self.option("task_labels")
            self.apps.setSpacing(self.option("icon_spacing"))
            set_prop(self.root, "popFit", self._fits_content())
            self._style_floating()
            self._apply_native_backdrop()
            self._round_corners()
            self.refresh(force=True)
            self.reposition()
            if self._registered:
                self._sync_auto_hide()

    def _fits_content(self) -> bool:
        return self.option("dock_style") == "fit" or self.floating()

    def floating(self) -> bool:
        """Schwebendes Dock: runde Pille mit Abstand zum Bildschirmrand, nur so breit wie die Icons."""
        return bool(self.option("dock_floating"))

    def _style_floating(self) -> None:
        """Der Stil malt sonst ein eckiges Rechteck über die runde Pille, die PopWin selbst zeichnet."""
        sheet = "#Bar { background: transparent; border: none; }" if self.floating() else ""
        if self.root.styleSheet() != sheet:
            self.root.setStyleSheet(sheet)

    def _place_clock(self) -> None:
        """Ganz links, ganz rechts oder als kleine Pille über der Mitte; sonst steht die Uhr in der Topbar."""
        place = self.option("clock_place")
        row = self.root.layout()
        side = place if place in ("dock_left", "dock_right") else ""
        if side != self._clock_at:
            self._clock_at = side
            row.removeWidget(self.clock)
            if side == "dock_left":
                row.insertWidget(0, self.clock)
            elif side:
                row.addWidget(self.clock)
        self.clock.setVisible(bool(side))
        self.clock.arrange(one_line=self.bar_size() < 40 or None)
        if place == "dock_center":
            if self.pill is None:
                self.pill = ClockPill(self)
                self.destroyed.connect(self.pill.deleteLater)
            self.pill.face.tick()
            self._pill_timer.start()
        else:
            self._pill_timer.stop()
            if self.pill is not None:
                self.pill.hide()
        self._follow_pill()

    def _follow_pill(self) -> None:
        if self.pill is not None:
            self.pill.follow()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._follow_pill()
        if self.tools is not None:
            self.tools.follow()

    # --- Symbolleiste über der Taskbar (Beta) ---------------------------------------------
    def tools_mode(self) -> str:
        return self.option("dock_tools")

    def _tool_widgets(self) -> dict[str, QWidget]:
        return {"start": self.start, "search": self.search, "media": self.media, "nova": self.nova, "trash": self.trash,
                "desktop": self.desktop}

    def _arrange_tools(self) -> None:
        """Hängt die gewählten Knöpfe in die Leiste darüber; ohne Leiste stehen alle wieder an ihrem Platz."""
        mode = self.tools_mode()
        moved = [item for item in TOOL_ITEMS if mode != "off" and item in self.option("dock_tools_items")]
        if moved == self._tools_moved:
            if self.tools is not None and mode != "off":
                self.tools.start()
            return
        self._tools_moved = moved
        widgets = self._tool_widgets()
        row = self.root.layout()
        for widget in widgets.values():
            row.removeWidget(widget)
            if self.tools is not None:
                self.tools.row.removeWidget(widget)
        left = [widgets[item] for item in ("start", "search") if item not in moved]
        for offset, widget in enumerate(left):
            row.insertWidget(row.indexOf(self.lead) + 1 + offset, widget)
        for item in ("media", "nova", "trash", "desktop"):
            if item not in moved:
                # Immer vor dem rechten Ausgleich; nach der Uhr gerichtet landeten sie bei der Uhr links ganz links.
                row.insertWidget(row.indexOf(self.pad_end), widgets[item])
        if moved:
            if self.tools is None:
                self.tools = ToolRow(self)
                self.destroyed.connect(self.tools.deleteLater)
            self.tools.set_widgets([widgets[item] for item in moved])
            self.tools.start()
        elif self.tools is not None:
            self.tools.stop()

    # --- Auto-Hide ---------------------------------------------------------------
    def reserves_space(self) -> bool:
        return self.option("reserve_space") and not self.auto_hides()

    def _keeps_open(self) -> bool:
        """Offene Menüs gehören zur Taskbar: Verschwände sie, stünde das Menü ohne Boden da."""
        menu = self.ctx.start_menu
        mine = menu.anchor is self.start or (menu.anchor is None and self.ctx.monitors.is_primary(self.bar_screen))
        on_tools = self.tools is not None and self.tools.isVisible() and self.tools.frameGeometry().contains(QCursor.pos())
        return (on_tools or self._dragged is not None or self._dropping is not None or self._drag_hover.active()
                or QApplication.activePopupWidget() is not None
                or self.ctx.task_preview.isVisible() or (menu.isVisible() and not menu.animator.closing and mine))

    def bar_size(self) -> int:
        return self.option("dock_height") or super().bar_size()

    def bar_margin(self) -> int:
        if self.floating():
            return max(0, int(self.option("dock_gap")))
        # Die kompakte Leiste schwebt, sie braucht auch ohne Theme-Abstand etwas Luft zum Rand.
        return max(super().bar_margin(), 6) if self._fits_content() else super().bar_margin()

    def apply_geometry(self) -> None:
        if not self._fits_content():
            super().apply_geometry()
            return
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.edge in ("left", "right"):
            top, bottom, x, size = self._area
            layout = self.root.layout()
            layout.invalidate()
            ratio = self.bar_screen.devicePixelRatio()
            height = min(round(layout.sizeHint().height() * ratio), bottom - top)
            self._place(x, aligned_x(self.option("dock_align"), top, bottom, height), size, height)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        left, right, y, size = self._area
        layout = self.root.layout()
        layout.invalidate()
        ratio = self.bar_screen.devicePixelRatio()
        width = min(round(layout.sizeHint().width() * ratio), right - left)
        self._place(aligned_x(self.option("dock_align"), left, right, width), y, width, size)

    def _align_icons(self) -> None:
        """Verteilt in der vollen Breite den freien Platz um die Icons."""
        align = self.option("dock_align")
        full = not self._fits_content()
        self.lead.setVisible(full and align != "left")
        self.trail.setVisible(full and align != "right")
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.edge in ("left", "right"):
            self.pad.setVisible(False)
            self.pad_end.setVisible(False)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        self.pad.setVisible(full and align == "center")
        self.pad_end.setVisible(full and align == "center")
        if align == "center":
            spacing = self.root.layout().spacing()
            right = sum(w.width() + spacing for w in (self.media, self.nova, self.trash, self.desktop) if w.isVisibleTo(self))
            left = self.claude.width() + spacing if self.claude is not None and self.claude.isVisibleTo(self) else 0
            clock = self.clock.width() + spacing if self.clock.isVisibleTo(self) else 0
            right, left = (right + clock, left) if self._clock_at == "dock_right" else (right, left + clock)
            # Was auf einer Seite mehr steht, gleicht ein leerer Platz auf der anderen aus; sonst rutschen die Icons
            # aus der echten Mitte, etwa wenn rechts alles in der Symbolleiste darüber steht und links der Claude-Knopf.
            self.pad.setFixedWidth(max(0, right - left))
            self.pad_end.setFixedWidth(max(0, left - right))

    def _icon_size(self) -> int:
        return min(self.option("icon_size"), self.bar_size() - 12)

    # --- Anheften per Drag and Drop, Hinhalten wechselt die App -----------------
    @staticmethod
    def _pinnable(mime) -> bool:
        urls = mime.urls()
        return bool(urls) and all(u.isLocalFile() for u in urls)

    def _drag_target(self, position: QPoint) -> QWidget | None:
        """Der Task mit offenen Fenstern oder der Knopf „Desktop anzeigen“ unter dem Gezogenen, sonst nichts."""
        if not self.ctx.config.get("taskbar.drag_switch", True):
            return None
        widget = self.childAt(position)
        while widget is not None and widget is not self:
            if widget is self.desktop or (isinstance(widget, TaskButton) and widget.windows):
                return widget
            widget = widget.parentWidget()
        return None

    def dragEnterEvent(self, event) -> None:
        # Alles annehmen, auch Text und Bilder: Ohne das meldet Qt keine Bewegung, und das Hinhalten bliebe unbemerkt.
        if self._pinnable(event.mimeData()):
            set_prop(self.root, "dropping", True)
            event.acceptProposedAction()
        elif self.ctx.config.get("taskbar.drag_switch", True):
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.accept()

    def dragMoveEvent(self, event) -> None:
        target = self._drag_target(event.position().toPoint())
        self._drag_hover.over(target)
        pinnable = target is None and self._pinnable(event.mimeData())
        set_prop(self.root, "dropping", pinnable)
        if pinnable:
            event.acceptProposedAction()
        else:  # über einem Task lässt sich nichts ablegen, wie bei Windows
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drag_hover.leave()
        set_prop(self.root, "dropping", False)
        # Zeigt die Vorschau gerade die Fenster zum Ablegen, bleibt sie kurz stehen, bis der Zeiger auf ihr ist.
        if self.ctx.task_preview.isVisible():
            self.ctx.task_preview.unhover()

    def dropEvent(self, event) -> None:
        target = self._drag_target(event.position().toPoint())
        self._drag_hover.leave()
        set_prop(self.root, "dropping", False)
        if target is not None or not self._pinnable(event.mimeData()):
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.ignore()
            return
        paths = [os.path.normpath(u.toLocalFile()) for u in event.mimeData().urls()]
        event.acceptProposedAction()
        # Der Explorer wartet, bis dieser Handler zurückkehrt.
        QTimer.singleShot(0, lambda: self.pin_paths(paths))

    def _drag_held(self, target) -> None:
        """Das Gezogene stand lange genug über `target`: zur App wechseln, damit man dort ablegen kann."""
        if target is self.desktop:
            win_api.toggle_desktop()
            return
        if not isinstance(target, TaskButton) or target.key not in self._buttons:
            return
        live = [w for w in target.windows if win_api.is_window(w.hwnd)]
        if len(live) == 1:
            win_api.activate_for_drop(live[0].hwnd)
        elif live:  # mehrere Fenster: Die Vorschau zeigt sie, Hinhalten über einer Karte wählt eines
            self.ctx.task_preview.show_for_drag(target)

    def pin_paths(self, paths: list[str]) -> None:
        self._store_pins(merge_pins(self._own_pins(), [pin_for_path(p) for p in paths]))

    @staticmethod
    def _key(path: str) -> str:
        return os.path.basename(path).lower()

    # --- Pins: Der Hauptmonitor nutzt die gemeinsame Liste, Nebenmonitore ihre eigene ------
    def _primary(self) -> bool:
        return self.ctx.monitors.is_primary(self.bar_screen)

    def _synced(self) -> bool:
        """Alle Taskbars zeigen dieselben Icons: dieselben Apps, alle Fenster, gleiche Reihenfolge."""
        return self.ctx.config.get("taskbar.sync_icons")

    def _own_pins(self) -> list[dict]:
        if self._primary() or self._synced():
            return list(self.ctx.config.get("taskbar.pinned"))
        return list(self.ctx.config.get("taskbar.screen_pins").get(self.screen_id, []))

    def _store_pins(self, pins: list[dict]) -> None:
        if self._primary() or self._synced():
            self.ctx.config.set("taskbar.pinned", pins)
        else:
            self.ctx.config.set_screen_pins(self.screen_id, pins)

    def visible_pins(self) -> list[dict]:
        shared = self.ctx.config.get("taskbar.pinned")
        if self._primary() or self._synced():
            return list(shared)
        extra = self.ctx.config.get("taskbar.screen_pins").get(self.screen_id, [])
        return extra if self.ctx.config.get("taskbar.pin_scope") == "primary" else merge_pins(shared, extra)

    def is_pinned(self, key: str) -> bool:
        return any(pin_key(p) == key for p in self.visible_pins())

    def toggle_pin(self, button: TaskButton) -> None:
        own = self._own_pins()
        if any(pin_key(p) == button.key for p in own):
            self._store_pins([p for p in own if pin_key(p) != button.key])
        elif self.is_pinned(button.key):
            shared = self.ctx.config.get("taskbar.pinned")  # gemeinsame Pins dürfen auch von hier gelöst werden
            self.ctx.config.set("taskbar.pinned", [p for p in shared if pin_key(p) != button.key])
        else:
            target = button.target or (button.key if button.key.startswith("popwin:") else "")
            self._store_pins(own + [{"name": button.display_name(), "target": target, "exe": button.key}])

    # --- Feste Reihenfolge und freies Verschieben ---------------------------------
    def _shown_keys(self) -> list[str]:
        return [key for key in self._order if key in self._buttons]

    def _slots(self, keys: list[str]) -> dict[str, int]:
        """Wo jedes Icon bei dieser Reihenfolge steht. Das Layout selbst bleibt beim Ziehen unangetastet, es gibt nur das Maß."""
        if not keys or self.apps.count() == 0:
            return {}
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        vertical = self.edge in ("left", "right")
        if vertical:
            origin = self._slot_origin
            if origin is None:
                origin = min((self.apps.itemAt(i).geometry().y() for i in range(self.apps.count())), default=0)
            gap = max(0, self.apps.spacing())
            slots, pos = {}, origin
            for key in keys:
                if key in self._buttons:
                    slots[key] = pos
                    pos += self._buttons[key].height() + gap
            return slots
        # --- [Antigravity Ende: Sidebar Support] ---
        """Wo jedes Icon bei dieser Reihenfolge steht. Das Layout selbst bleibt beim Ziehen unangetastet, es gibt nur das Maß."""
        if not keys or self.apps.count() == 0:
            return {}
        origin = self._slot_origin
        if origin is None:
            origin = min((self.apps.itemAt(i).geometry().x() for i in range(self.apps.count())), default=0)
        gap = max(0, self.apps.spacing())
        slots, x = {}, origin
        for key in keys:
            if key in self._buttons:
                slots[key] = x
                x += self._buttons[key].width() + gap
        return slots

    def _glide(self, button: TaskButton, x: int) -> None:
        if (button.y() if self.edge in ("left", "right") else button.x()) == x and button not in self._glides:  # [Antigravity: Sidebar Support]
            return
        animation = self._glides.get(button)
        if animation is None:
            animation = self._glides[button] = QPropertyAnimation(button, b"pos", self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(lambda b=button: self._glide_done(b))
        animation.stop()
        duration = GLIDE_MS if self.ctx.config.get("animations.enabled") else 0
        animation.setDuration(duration)
        animation.setStartValue(button.pos())
        animation.setEndValue(QPoint(button.x(), x) if self.edge in ("left", "right") else QPoint(x, button.y()))  # [Antigravity: Sidebar Support]
        animation.start()

    def _glide_done(self, button: TaskButton) -> None:
        self._glides.pop(button, None)
        if self._dropping is button:
            self._commit_drop()

    def _other_docks(self) -> list["Dock"]:
        return [bar for bar in self.ctx.bars if isinstance(bar, Dock) and bar is not self]

    def preview_order(self, keys: list[str]) -> None:
        """Eine andere Taskbar zieht gerade: Hier wandern die Icons live mit."""
        if self._slot_origin is None and self.apps.count() > 0:
            self._slot_origin = min((self.apps.itemAt(i).geometry().x() for i in range(self.apps.count())), default=0)
        self._order_held = True
        mine = [key for key in keys if key in self._buttons]
        mine += [key for key in self._shown_keys() if key not in mine]
        self._order = mine
        for key, x in self._slots(mine).items():
            self._glide(self._buttons[key], x)

    def drag_task(self, button: TaskButton, global_x: int, grabbed_x: int | None = None) -> None:
        if not self._left_held() and self._dragged is button:
            self.finish_drag()  # das Loslassen kam nicht an, etwa weil ein Fenster dazwischenging
            return
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.edge in ("left", "right"):
            if self._dragged is not button:
                self._slot_origin = min((self.apps.itemAt(i).geometry().y() for i in range(self.apps.count())), default=0)
                self._dragged = button
                self._dropping = None
                self._grab_offset = (global_x if grabbed_x is None else grabbed_x) - button.mapToGlobal(QPoint(0, 0)).y()
                animation = self._glides.pop(button, None)
                if animation:
                    animation.stop()
                button.raise_()
            keys = self._shown_keys()
            slots = self._slots(keys)
            if not slots:
                return
            low, high = min(slots.values()), max(slots.values())
            y_pos = max(low, min(high, button.parentWidget().mapFromGlobal(QPoint(0, global_x)).y() - self._grab_offset))
            button.move(button.x(), y_pos)
            others = [key for key in keys if key != button.key]
            centers = [slots[key] + self._buttons[key].height() / 2 for key in keys if key != button.key]
            index = 0 if y_pos <= low else len(others) if y_pos >= high else sum(y_pos + button.height() / 2 > center for center in centers)
            order = moved_key(keys, button.key, min(index, len(others)))
            if order != keys:
                self._order = order + [k for k in self._order if k not in order]
                for key, target in self._slots(order).items():
                    if key != button.key:
                        self._glide(self._buttons[key], target)
                if self._synced():
                    for dock in self._other_docks():
                        dock.preview_order(order)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        if self._dragged is not button:
            self._slot_origin = min((self.apps.itemAt(i).geometry().x() for i in range(self.apps.count())), default=0)
            self._dragged = button
            self._dropping = None
            self._grab_offset = (global_x if grabbed_x is None else grabbed_x) - button.mapToGlobal(QPoint(0, 0)).x()
            animation = self._glides.pop(button, None)
            if animation:
                animation.stop()
            button.raise_()
        keys = self._shown_keys()
        slots = self._slots(keys)
        if not slots:
            return
        low, high = min(slots.values()), max(slots.values())
        x = max(low, min(high, button.parentWidget().mapFromGlobal(QPoint(global_x, 0)).x() - self._grab_offset))
        button.move(x, button.y())  # das Icon bleibt in der Hand: Es folgt der Maus ohne Umweg über das Layout
        others = [key for key in keys if key != button.key]
        centers = [slots[key] + self._buttons[key].width() / 2 for key in keys if key != button.key]
        index = 0 if x <= low else len(others) if x >= high else sum(x + button.width() / 2 > center for center in centers)
        order = moved_key(keys, button.key, min(index, len(others)))
        if order != keys:
            self._order = order + [k for k in self._order if k not in order]
            for key, target in self._slots(order).items():
                if key != button.key:
                    self._glide(self._buttons[key], target)
            if self._synced():
                for dock in self._other_docks():
                    dock.preview_order(order)

    def finish_drag(self) -> None:
        button = self._dragged
        if button is None:
            return
        button.reset_state()
        self._dragged = None
        self._dropping = button
        slots = self._slots(self._shown_keys())
        if button.key in slots:
            self._glide(button, slots[button.key])
        if button not in self._glides:  # es stand schon genau auf seinem Platz
            self._commit_drop()

    def _commit_drop(self) -> None:
        self._dropping = None
        self._slot_origin = None
        self.save_order()
        self.apps.activate()
        self.refresh()
        if self._synced():
            for dock in self._other_docks():
                dock._order_held = False
                dock._slot_origin = None
                dock.refresh()

    def save_order(self) -> None:
        orders = self.ctx.config.get("taskbar.order")
        orders[SHARED_ORDER if self._synced() else self.screen_id] = self._order[-200:]
        self.ctx.config.set("taskbar.order", orders, notify=False)

    def claude_on_button(self) -> bool:
        """Mit dem Claude-Knopf steht Claude nicht noch einmal zwischen den Tasks."""
        return self.claude is not None and bool(self.option("dev_claude_button"))

    def _windows_here(self) -> list[win_api.WindowInfo]:
        windows = self.ctx.windows()
        if self.claude is not None:
            self.claude.setVisible(self.claude_on_button())
            if self.claude_on_button():
                windows = [window for window in windows if not is_claude_window(window)]
        if self._synced():
            return windows
        window_filter = self.ctx.config.get("taskbar.window_filter")
        if shows_all_windows(window_filter, self.ctx.monitors.is_primary(self.bar_screen)):
            return windows
        monitor = native_rect(self.bar_screen)
        return [w for w in windows if win_api.window_monitor(w.hwnd) == monitor]

    # --- Icons wachsen unter der Maus (Beta) ---------------------------------------
    def magnifies(self) -> bool:
        return bool(self.ctx.config.get("taskbar.magnify")) and self.ctx.config.get("performance.level") != "saver"

    def start_magnify(self) -> None:
        if self.magnifies() and not self._magnify_timer.isActive():
            self._magnify_timer.start()

    def _nearness(self, cursor: QPoint) -> float:
        frame = self.frameGeometry()
        side = MAGNIFY_REACH * self._icon_size()
        return nearness(cursor.x(), cursor.y(), frame.left(), frame.top(), frame.right(), frame.bottom(), self.edge,
                        APPROACH_ZONE * self.bar_size(), side)

    def _check_approach(self) -> None:
        """Die Maus kommt von oben (oder von der Seite) näher: schon dann sanft wachsen, nicht erst auf dem Icon."""
        if not self.isVisible() or self.behind_game() or self._magnify_timer.isActive():
            return
        if self._nearness(QCursor.pos()) > 0:
            self.start_magnify()

    def _magnify(self) -> None:
        base = self._icon_size()
        top = max(base, min(round(base * MAGNIFY_TOP), self.bar_size() - 8))
        cursor = QCursor.pos()
        near = self._nearness(cursor) if self.magnifies() and self._dragged is None and not self._fixed_drag else 0.0
        inside = near > 0
        vertical = self.edge in ("left", "right")
        changed = False
        for button in self._buttons.values():
            size = base
            if inside:
                center = button.mapToGlobal(button.rect().center())
                distance = abs((cursor.y() - center.y()) if vertical else (cursor.x() - center.x()))
                reach = MAGNIFY_REACH * (button.height() if vertical else button.width())
                grow = (magnify_scale(distance, reach) - 1.0) / (MAGNIFY_TOP - 1.0)
                size = round(base + (top - base) * grow * near)
            if button.iconSize().width() != size:
                button.setIconSize(QSize(size, size))
                changed = True
        if changed:
            self.marks.update()
            self._fit_width()
        elif not inside:
            self._magnify_timer.stop()  # alles wieder normal groß: keine Arbeit mehr

    # --- Feste Knöpfe umstellen (Start, Suche, Player, Nova, Papierkorb, Desktop) ---------------------------
    def _fixed_widgets(self) -> dict[str, QWidget]:
        return {"start": self.start, "search": self.search, "media": self.media, "nova": self.nova,
                "trash": self.trash, "desktop": self.desktop}

    def _arrange_fixed(self, order: list[str] | None = None, animate: bool = False) -> None:
        """Stellt die festen Knöpfe in die gewünschte Reihenfolge; vor "apps" heißt links neben den App-Icons."""
        order = dock_order_of(order if order is not None else self.option("dock_order"))
        if order == self._fixed_order:
            return
        widgets = self._fixed_widgets()
        before = {widget: widget.mapTo(self.root, QPoint(0, 0)) for widget in widgets.values() if widget.isVisible()}
        row = self.root.layout()
        for widget in widgets.values():
            row.removeWidget(widget)
        row.removeItem(self.apps)
        split = order.index("apps")
        index = row.indexOf(self.lead) + 1
        for key in order[:split]:
            row.insertWidget(index, widgets[key])
            index += 1
        row.insertLayout(index, self.apps)
        index = row.indexOf(self.trail) + 1
        for key in order[split + 1:]:
            row.insertWidget(index, widgets[key])
            index += 1
        self._fixed_order = order
        for widget in widgets.values():
            if widget.property("fixedDrag") is None:
                widget.setProperty("fixedDrag", True)
                widget.installEventFilter(self)
        if animate and duration_of(self.ctx.config):
            row.activate()
            for widget, old in before.items():
                if self._fixed_drag and widgets.get(self._fixed_drag) is widget:
                    continue  # das gezogene Icon hängt an der Maus
                self._fixed_glide(widget, old)

    def _fixed_glide(self, widget: QWidget, start: QPoint) -> None:
        end = widget.pos()
        if start == end:
            return
        animation = self._fixed_glides.get(widget)
        if animation is None:
            animation = self._fixed_glides[widget] = QPropertyAnimation(widget, b"pos", self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setDuration(FIXED_GLIDE_MS)
        animation.stop()
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.start()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        widgets = self._fixed_widgets()
        key = next((name for name, widget in widgets.items() if widget is watched), "")
        if not key:
            return super().eventFilter(watched, event)
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._fixed_press = (key, event.globalPosition().toPoint())
        elif kind == QEvent.Type.MouseMove and self._fixed_press and self._fixed_press[0] == key:
            point = event.globalPosition().toPoint()
            if not self._fixed_drag and (point - self._fixed_press[1]).manhattanLength() >= QApplication.startDragDistance():
                self._fixed_drag = key
                self._fixed_grab = point - watched.mapToGlobal(QPoint(0, 0))
                watched.raise_()
                if isinstance(watched, QAbstractButton):
                    watched.setDown(False)
            if self._fixed_drag:
                self._drag_fixed(watched, point)
                return True
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            dragged = self._fixed_drag
            self._fixed_press = None
            if dragged:
                self._finish_fixed(watched)
                return True  # kein Klick nach dem Ziehen
        return super().eventFilter(watched, event)

    def _drag_fixed(self, widget: QWidget, point: QPoint) -> None:
        vertical = self.edge in ("left", "right")
        local = widget.parentWidget().mapFromGlobal(point - self._fixed_grab)
        widget.move(widget.x() if vertical else local.x(), local.y() if vertical else widget.y())
        # Wohin gehört es? Nach den Mitten der anderen sichtbaren Teile, die App-Icons zählen als ein Block.
        centers = []
        widgets = self._fixed_widgets()
        for key in self._fixed_order:
            if key == self._fixed_drag:
                continue
            if key == "apps":
                box = self.apps.geometry()
                if box.isEmpty():
                    box = self.trail.geometry()
                center = box.center()
                centers.append((key, self.root.mapToGlobal(center)))
            elif widgets[key].isVisible():
                centers.append((key, widgets[key].mapToGlobal(widgets[key].rect().center())))
        mine = widget.mapToGlobal(widget.rect().center())
        place = sum((mine.y() > c.y()) if vertical else (mine.x() > c.x()) for _key, c in centers)
        visible = [key for key, _c in centers]
        anchor_after = visible[place - 1] if place > 0 else None
        order = [key for key in self._fixed_order if key != self._fixed_drag]
        order.insert(order.index(anchor_after) + 1 if anchor_after else 0, self._fixed_drag)
        if order != self._fixed_order:
            held = widget.pos()
            self._arrange_fixed(order, animate=True)
            widget.move(held)  # bleibt unter der Maus, die anderen machen Platz

    def _finish_fixed(self, widget: QWidget) -> None:
        start = widget.pos()
        self._fixed_drag = ""
        self.root.layout().activate()
        self._fixed_glide(widget, start)
        if self._fixed_order != dock_order_of(self.option("dock_order")):
            self.ctx.config.set_monitor(self.screen_id, "dock_order", list(self._fixed_order))

    def peek(self) -> None:
        super().peek()
        self.refresh()  # hinter dem Spiel lief kein Abgleich, die Icons sollen beim Hereinkommen stimmen

    def refresh(self, force: bool = False) -> None:
        if not force and self.behind_game() and self._dragged is None:
            return  # im Spiel nicht alle 0,7 s Fenster abfragen, solange die Taskbar unsichtbar ist
        # Beim Ziehen darf nichts neu gebaut werden: Der Knopf in der Hand würde gelöscht und das Icon fiele heraus.
        held = self._left_held()
        if not held:
            for button in self._buttons.values():
                if button._dragging or button._press is not None or button.isDown():
                    button.reset_state()
        if self._dragged is not None or self._dropping is not None or self._order_held:
            if not held:
                if self._dragged is not None:
                    self.finish_drag()
                if self._dropping is not None and self._dropping not in self._glides:
                    self._commit_drop()
                if self._order_held and not any(d._dragged is not None or d._dropping is not None for d in self._other_docks()):
                    self._order_held = False
                    self._slot_origin = None
                    if self._buttons:
                        self._rearrange({key: None for key in self._shown_keys()})
            if self._dragged is not None or self._dropping is not None or self._order_held:
                return
        # Während ein Knopf gedrückt gehalten wird, nicht neu aufbauen, sonst geht das Loslassen (clicked) verloren.
        if not force and held and any(b._press is not None or b.isDown() for b in self._buttons.values()):
            return
        pins = self.visible_pins()
        groups: dict[str, dict] = {pin_key(p): {"target": p["target"], "name": p["name"], "windows": [], "pinned": True} for p in pins}
        # Nach Fensterkennung sortiert: Die Reihenfolge von Windows ändert sich bei jedem Fokuswechsel.
        for window in sorted(self._windows_here(), key=lambda w: w.hwnd):
            if window.popwin_app:
                key, new_group = taskbar_slot(self.ctx, window.popwin_app)
            elif window.app_id:
                name = web_app_name(window.app_id, window.title)
                key = web_app_key(window.app_id, name, pins)
                new_group = {"target": f"shell:AppsFolder\\{window.app_id}", "name": name, "windows": []}
            else:
                key = self._key(window.exe) or f"hwnd{window.hwnd}"
                if key not in groups:
                    key = pin_key_of_window(window.exe, pins) or key
                new_group = {"target": window.exe, "name": "", "windows": []}
            groups.setdefault(key, new_group)["windows"].append(window)
        if self._synced():
            shared = self.ctx.config.get("taskbar.order").get(SHARED_ORDER, [])
            self._order = synced_order(shared, [pin_key(p) for p in self.visible_pins()], list(groups))
        else:
            self._order += [key for key in groups if key not in self._order]
        groups = {key: groups[key] for key in ordered_keys(list(groups), self._order)}

        foreground = win_api.foreground_window()
        notices = visible_notices(self.ctx.notifications.notices, self.ctx.config.get("notifications.hidden"))             if self.task_badges else []
        labels = notice_labels(notices, self.ctx.apps.apps) if notices else {}
        signature = (tuple(groups), self._icon_size())
        if force or signature != self._signature:
            only_order = not force and self._signature is not None and signature[1] == self._signature[1] \
                and set(signature[0]) == set(self._signature[0])
            self._signature = signature
            self._rearrange(groups) if only_order else self._rebuild(groups)
        for key, group in groups.items():
            button = self._buttons[key]
            button.windows = group["windows"]
            button.target = group["target"]
            button.name = group["name"]
            label = button.fontMetrics().elidedText(button.display_name(), Qt.TextElideMode.ElideRight, button.LABEL_WIDTH)
            button.setText(label if (self.task_labels and self.edge not in ("left", "right")) else "")  # [Antigravity: Sidebar]
            titles = [w.title for w in group["windows"]]
            set_prop(button, "running", bool(titles))
            # Bei mehreren Fenstern einer App reicht ein nicht minimiertes.
            set_prop(button, "shown", any(not win_api.is_minimized(w.hwnd) for w in group["windows"]))
            set_prop(button, "focused", any(w.hwnd == foreground for w in group["windows"]))
            button.badge = badge_text(badge_count(notices, key, button.target, button.name or button.display_name(),
                                                  labels)) if notices else ""
            button.update()
        self.marks.raise_()
        self.marks.update()
        self._align_icons()
        self._fit_width()  # die Leiste wächst und schrumpft mit den Apps

    def _rearrange(self, groups: dict) -> None:
        """Nur die Reihenfolge ist neu: Die Knöpfe bleiben dieselben, ein Neuaufbau ließe alle Icons kurz aufblitzen."""
        for key in groups:
            self.apps.removeWidget(self._buttons[key])
        for key in groups:
            self.apps.addWidget(self._buttons[key])
        self.apps.activate()

    def _rebuild(self, groups: dict) -> None:
        for animation in list(self._glides.values()):
            animation.stop()
        self._glides.clear()
        before = set(self._buttons)
        for button in self._buttons.values():
            self.apps.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._buttons.clear()
        size = self._icon_size()
        for key, group in groups.items():
            button = TaskButton(self, key)
            button.setIconSize(QSize(size, size))
            button.setIcon(self._icon(group))
            self._buttons[key] = button
            self.apps.addWidget(button)
            button.show()  # sonst fehlt das Icon noch in der Breitenberechnung und die Leiste zuckt
            if before and key not in before:
                self._pop_in(button, size)

    def _pop_in(self, button: "TaskButton", size: int) -> None:
        """Eine neu geöffnete App ploppt in die Taskbar wie bei Windows: Das Icon wächst federnd auf seine Größe,
        die Nachbarn rücken dabei weich zur Seite."""
        length = duration_of(self.ctx.config)
        if not length:
            return
        animation = QVariantAnimation(button, duration=max(160, length + 60), startValue=0.25, endValue=1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutBack)

        def grow(value) -> None:
            edge = max(4, round(size * float(value)))
            button.setIconSize(QSize(edge, edge))
            self.marks.update()

        animation.valueChanged.connect(grow)
        animation.finished.connect(lambda: (button.setIconSize(QSize(size, size)), self._fit_width()))
        grow(0.25)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _icon(self, group: dict) -> QIcon:
        """Wie die Windows-Taskleiste: angeheftet das Icon der Verknüpfung, sonst das der App-Kennung des Fensters,
        bei Programmen wie javaw.exe das Fenster-Icon und erst danach das Icon der .exe."""
        if group.get("icon"):
            return icon_for_target(group["icon"])
        target = group["target"]
        windows = group["windows"]
        if group.get("pinned") or not windows:
            return self._first_icon(lambda: icon_for_target(target) if target else QIcon(), lambda: self._window_icon(windows))
        aumid = next((w.aumid for w in windows if w.aumid), "")
        exe = windows[0].exe
        steps = [lambda: icon_for_target(f"shell:AppsFolder\\{aumid}") if aumid else QIcon()]
        if win_api.is_host_exe(exe) or (exe and win_api.has_generic_icon(exe)):
            steps.append(lambda: self._window_icon(windows))
        steps += [lambda: icon_for_target(target) if target else QIcon(), lambda: self._window_icon(windows)]
        return self._first_icon(*steps)

    def _first_icon(self, *steps) -> QIcon:
        for step in steps:
            icon = step()
            if not icon.isNull():
                return icon
        color = "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"
        return glyph_icon(G.APP, self._icon_size(), color)

    @staticmethod
    def _window_icon(windows: list) -> QIcon:
        for window in windows:
            handle = win_api.window_hicon(window.hwnd)
            if handle:  # gehört dem Fenster, also nicht freigeben
                image = QImage.fromHICON(handle)
                if not image.isNull():
                    return QIcon(QPixmap.fromImage(image))
        return QIcon()
