"""Dock: Start, angeheftete und laufende Apps, Now Playing, Papierkorb."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core import win_api
from core.monitor_manager import native_rect
from core.replacements import launch, taskbar_slot
from core.dev import is_dev
from ui.claude_button import ClaudeButton, is_claude_window
from ui.clock_place import ClockFace, ClockPill
from ui.dock_tools import TOOL_ITEMS, ToolRow
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
        x = origin.x() + (self.width() - width) / 2
        y = mark_y(origin.y(), self.height(), self.iconSize().height(), bar_height, height)
        painter.drawRoundedRect(QRectF(x, y, width, height), height / 2, height / 2)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.property("running") and self.dock.task_counts and len(self.windows) > 1:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            self._paint_count(painter, self.mark_color()[1])
            painter.end()

    def _paint_count(self, painter: QPainter, color: QColor) -> None:
        box = QRectF(self.width() - self.COUNT_SIZE - 1, 1, self.COUNT_SIZE, self.COUNT_SIZE)
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
    def enterEvent(self, event) -> None:
        if not self._dragging:
            self.dock.ctx.task_preview.hover(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.dock.ctx.task_preview.unhover()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self.dock.ctx.task_preview.dismiss()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position().toPoint() - self._press).manhattanLength()
            if not self._dragging and distance >= QApplication.startDragDistance():
                self._dragging = True
                self.setDown(False)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                self.dock.drag_task(self, event.globalPosition().toPoint().x(), self.mapToGlobal(self._press).x())
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self._press = None
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.dock.finish_drag()
            event.accept()
            return
        self._press = None
        super().mouseReleaseEvent(event)

    def _activate(self) -> None:
        if not self.windows:
            if self.target:
                launch(self.dock.ctx, self.target)
        elif len(self.windows) == 1:
            hwnd = self.windows[0].hwnd
            if win_api.foreground_window() == hwnd:
                win_api.minimize_window(hwnd)
            else:
                win_api.activate_window(hwnd)
        else:
            menu = self._menu()
            for window in self.windows:
                menu.addAction(window.title[:60], lambda h=window.hwnd: win_api.activate_window(h))
            self._popup(menu)

    def _menu(self) -> QMenu:
        return themed_menu(self.dock.ctx, self, self.dock.theme_name)

    def _popup(self, menu: QMenu) -> None:
        if self.dock.edge == "bottom":
            position = self.mapToGlobal(self.rect().topLeft()) - QPoint(0, menu.sizeHint().height())
        else:
            position = self.mapToGlobal(self.rect().bottomLeft())
        menu.popup(position)
        win_api.force_foreground(int(menu.winId()))

    def contextMenuEvent(self, event) -> None:
        menu = self._menu()
        pinned = self.dock.is_pinned(self.key)
        if self.windows and self.target:
            menu.addAction("Neues Fenster", lambda: launch(self.dock.ctx, self.target))
        if pinned or self.target:
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
        self.task_labels = False

        self._order: list[str] = list(ctx.config.get("taskbar.order").get(self.screen_id, []))
        self._dragged: TaskButton | None = None
        self._dropping: TaskButton | None = None
        self._grab_offset = 0
        self._order_held = False  # eine andere Taskbar zieht gerade
        self._glides: dict[TaskButton, QPropertyAnimation] = {}
        self.marks = TaskMarks(self, self.root)
        self.timer = QTimer(self, interval=700)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        ctx.config.changed.connect(self._on_config)
        ctx.media.updated.connect(self._fit_width)  # Now Playing macht die Leiste breiter
        self._on_config("profile")

    def contextMenuEvent(self, event) -> None:
        """Rechtsklick auf die freie Fläche: das Wichtigste direkt, der Rest in den Einstellungen."""
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
        toggle("App-Namen", "task_labels")
        menu.addSeparator()
        for label, value in (("Ganze Bildschirmbreite", "full"), ("Nur so breit wie die Icons", "fit")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.option("dock_style") == value)
            action.triggered.connect(lambda _=False, v=value: config.set_monitor(self.screen_id, "dock_style", v))
        menu.addSeparator()
        menu.addAction("Task-Manager öffnen", lambda: win_api.shell_open("taskmgr.exe"))
        menu.addAction("Taskbar-Einstellungen", lambda: self.ctx.open_settings_at("Taskbar"))

    def _fit_width(self) -> None:
        if self._area and self._fits_content():
            self.apply_geometry()

    def set_theme_name(self, name: str) -> None:
        self.trash.theme_name = name
        super().set_theme_name(name)

    def on_meta_changed(self, meta: dict) -> None:
        self.media.set_compact(meta["compact"] == "1")
        self.media.update_media(self.media._last)
        win_api.set_rounded(int(self.winId()), meta["rounded"] == "1" or self._fits_content())
        self._style_search()
        self.refresh(force=True)

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
        if key == "profile" or key.startswith("taskbar"):
            self._arrange_tools()  # zuerst: Umhängen in ein anderes Fenster blendet ein Widget aus
            self.media.allowed = self.option("show_media")
            self.media.set_style(self.option("media_style"), self.option("media_width"))
            self.media.update_media(self.media._last)
            self.trash.setVisible(self.option("show_recycle"))
            self.nova.setVisible(self.option("show_nova_button"))
            self.desktop.setVisible(self.option("show_desktop"))
            self.start.setVisible(self.option("show_start"))
            self.search.setVisible(self.option("show_search"))
            self._style_start()
            self._style_search()
            self._place_clock()
            self.task_indicator = self.option("task_indicator")
            self.task_counts = self.option("task_counts")
            self.task_labels = self.option("task_labels")
            self.apps.setSpacing(self.option("icon_spacing"))
            set_prop(self.root, "popFit", self._fits_content())
            win_api.set_rounded(int(self.winId()), self.meta()["rounded"] == "1" or self._fits_content())
            self.refresh(force=True)
            self.reposition()
            if self._registered:
                self._sync_auto_hide()

    def _fits_content(self) -> bool:
        return self.option("dock_style") == "fit"

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
        return (on_tools or self._dragged is not None or self._dropping is not None or QApplication.activePopupWidget() is not None
                or self.ctx.task_preview.isVisible() or (menu.isVisible() and mine))

    def bar_size(self) -> int:
        return self.option("dock_height") or super().bar_size()

    def bar_margin(self) -> int:
        # Die kompakte Leiste schwebt, sie braucht auch ohne Theme-Abstand etwas Luft zum Rand.
        return max(super().bar_margin(), 6) if self._fits_content() else super().bar_margin()

    def apply_geometry(self) -> None:
        if not self._fits_content():
            super().apply_geometry()
            return
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

    # --- Anheften per Drag and Drop -------------------------------------------
    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and all(u.isLocalFile() for u in urls):
            set_prop(self.root, "dropping", True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        set_prop(self.root, "dropping", False)

    def dropEvent(self, event) -> None:
        set_prop(self.root, "dropping", False)
        paths = [os.path.normpath(u.toLocalFile()) for u in event.mimeData().urls()]
        event.acceptProposedAction()
        # Der Explorer wartet, bis dieser Handler zurückkehrt.
        QTimer.singleShot(0, lambda: self.pin_paths(paths))

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
            self._store_pins(own + [{"name": button.display_name(), "target": button.target, "exe": button.key}])

    # --- Feste Reihenfolge und freies Verschieben ---------------------------------
    def _shown_keys(self) -> list[str]:
        return [key for key in self._order if key in self._buttons]

    def _slots(self, keys: list[str]) -> dict[str, int]:
        """Wo jedes Icon bei dieser Reihenfolge steht. Das Layout selbst bleibt beim Ziehen unangetastet, es gibt nur das Maß."""
        first = self.apps.itemAt(0).geometry()
        second = self.apps.itemAt(1).geometry() if self.apps.count() > 1 else first
        gap = max(0, second.x() - first.right() - 1)
        slots, x = {}, first.x()
        for key in keys:
            slots[key] = x
            x += self._buttons[key].width() + gap
        return slots

    def _glide(self, button: TaskButton, x: int) -> None:
        if button.x() == x and button not in self._glides:
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
        animation.setEndValue(QPoint(x, button.y()))
        animation.start()

    def _glide_done(self, button: TaskButton) -> None:
        self._glides.pop(button, None)
        if self._dropping is button:
            self._commit_drop()

    def _other_docks(self) -> list["Dock"]:
        return [bar for bar in self.ctx.bars if isinstance(bar, Dock) and bar is not self]

    def preview_order(self, keys: list[str]) -> None:
        """Eine andere Taskbar zieht gerade: Hier wandern die Icons live mit."""
        self._order_held = True
        mine = [key for key in keys if key in self._buttons]
        mine += [key for key in self._shown_keys() if key not in mine]
        self._order = mine
        for key, x in self._slots(mine).items():
            self._glide(self._buttons[key], x)

    def drag_task(self, button: TaskButton, global_x: int, grabbed_x: int | None = None) -> None:
        if not QApplication.mouseButtons() & Qt.MouseButton.LeftButton and self._dragged is button:
            self.finish_drag()  # das Loslassen kam nicht an, etwa weil ein Fenster dazwischenging
            return
        if self._dragged is not button:
            self._dragged = button
            self._dropping = None
            self._grab_offset = (global_x if grabbed_x is None else grabbed_x) - button.mapToGlobal(QPoint(0, 0)).x()
            animation = self._glides.pop(button, None)
            if animation:
                animation.stop()
            button.raise_()
        keys = self._shown_keys()
        slots = self._slots(keys)
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
        self._dragged = None
        self._dropping = button
        self._glide(button, self._slots(self._shown_keys())[button.key])
        if button not in self._glides:  # es stand schon genau auf seinem Platz
            self._commit_drop()

    def _commit_drop(self) -> None:
        self._dropping = None
        self.save_order()
        self.refresh()
        if self._synced():
            for dock in self._other_docks():
                dock._order_held = False
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

    def refresh(self, force: bool = False) -> None:
        # Beim Ziehen darf nichts neu gebaut werden: Der Knopf in der Hand würde gelöscht und das Icon fiele heraus.
        if self._dragged is not None or self._dropping is not None or self._order_held:
            if self._dragged is not None and not QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                self.finish_drag()
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
            button.setText(label if self.task_labels else "")
            titles = [w.title for w in group["windows"]]
            set_prop(button, "running", bool(titles))
            # Bei mehreren Fenstern einer App reicht ein nicht minimiertes.
            set_prop(button, "shown", any(not win_api.is_minimized(w.hwnd) for w in group["windows"]))
            set_prop(button, "focused", any(w.hwnd == foreground for w in group["windows"]))
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
