"""Modulares Widget-System. Neue Typen werden nur in WIDGET_TYPES eingetragen."""
from __future__ import annotations

import uuid

from PySide6.QtCore import QTimer
from core import win_api
from ui.widgets.base import DeskletWindow
from ui.widgets.clock import ClockWidget
from ui.widgets.media import MediaWidget
from ui.widgets.metrics import CpuWidget, GpuWidget, NetworkWidget, RamWidget
from ui.widgets.notes import NotesWidget
from ui.widgets.sysmon import SystemMonitorWidget
from ui.widgets.system import DisksWidget, ProcessesWidget, UptimeWidget
from ui.widgets.time_widgets import DateWidget, StopwatchWidget

WIDGET_TYPES: dict[str, type[DeskletWindow]] = {
    "cpu": CpuWidget,
    "gpu": GpuWidget,
    "ram": RamWidget,
    "sysmon": SystemMonitorWidget,
    "network": NetworkWidget,
    "disks": DisksWidget,
    "processes": ProcessesWidget,
    "clock": ClockWidget,
    "date": DateWidget,
    "stopwatch": StopwatchWidget,
    "uptime": UptimeWidget,
    "notes": NotesWidget,
    "media": MediaWidget,
}
WIDGET_LABELS = {kind: cls.TITLE for kind, cls in WIDGET_TYPES.items()}

ICON_CHECK_MS = 2500


def _overlap(first: tuple, second: tuple) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def clear_icons(icons: list, step: tuple[int, int], blocked: list[tuple], areas: list[tuple]) -> dict[int, tuple[int, int]]:
    """Wohin Symbole müssen, die unter einem Widget liegen. Jedes geht auf das nächste freie Rasterfeld seines Monitors."""
    width, height = step
    if not width or not height:
        return {}

    def box(x: int, y: int) -> tuple:
        return x, y, x + width, y + height
    taken = [box(*icon) for icon in icons if icon]
    moves: dict[int, tuple[int, int]] = {}
    for index, icon in enumerate(icons):
        if not icon or not any(_overlap(box(*icon), rect) for rect in blocked):
            continue
        area = next((a for a in areas if a[0] <= icon[0] + width // 2 < a[2] and a[1] <= icon[1] + height // 2 < a[3]), None)
        if area is None:
            continue
        spots = ((x, y) for x in range(area[0], area[2] - width + 1, width) for y in range(area[1], area[3] - height + 1, height))
        free = [spot for spot in spots if not any(_overlap(box(*spot), rect) for rect in blocked + taken)]
        if free:
            target = min(free, key=lambda spot: (spot[0] - icon[0]) ** 2 + (spot[1] - icon[1]) ** 2)
            moves[index] = target
            taken.append(box(*target))
    return moves


class WidgetManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.windows: dict[str, DeskletWindow] = {}
        self.gallery = None
        self._icon_timer = QTimer(interval=ICON_CHECK_MS)
        self._icon_timer.timeout.connect(self.keep_icons_clear)

    def start(self) -> None:
        for spec in self.ctx.config.get("widgets"):
            self._open(spec)
        self._icon_timer.start()

    def keep_icons_clear(self) -> None:
        """Windows kennt die Widgets nicht und ließe Symbole darunter verschwinden, also rücken wir sie zur Seite."""
        if not self.ctx.config.get("keep_icons_off_widgets") or not self.ctx.config.get("widgets_visible"):
            return
        shown = [window for window in self.windows.values() if window.isVisible()]
        if not shown or win_api.desktop_icon_state()["arrange"]:  # „Automatisch anordnen“ schöbe sie sofort zurück
            return
        icons, step = win_api.desktop_icon_positions()
        blocked = [win_api.window_rect(int(window.winId())) for window in shown]
        for index, (x, y) in clear_icons(icons, step, blocked, win_api.work_areas()).items():
            win_api.move_desktop_icon(index, x, y)

    def _open(self, spec: dict) -> DeskletWindow | None:
        cls = WIDGET_TYPES.get(spec.get("type"))
        if not cls:
            return None
        window = cls(self.ctx, spec)
        self.windows[spec["id"]] = window
        if self.ctx.config.get("widgets_visible"):
            window.show_desklet()
        return window

    def set_visible(self, visible: bool) -> None:
        """Blendet alle Widgets aus oder ein, ohne sie zu entfernen."""
        self.ctx.config.set("widgets_visible", visible)
        for window in self.windows.values():
            if visible:
                window.show_desklet()
            else:
                window.animator.leave()

    def add(self, kind: str) -> DeskletWindow | None:
        if not self.ctx.config.get("widgets_visible"):
            self.set_visible(True)
        screen = self.ctx.primary_geometry()
        offset = 40 * (len(self.windows) % 8)
        spec = {"id": uuid.uuid4().hex[:8], "type": kind, "x": screen.x() + 80 + offset, "y": screen.y() + 80 + offset,
                "z": "desktop", "opacity": 1.0, "data": {}, "options": {}}
        specs = list(self.ctx.config.get("widgets"))
        specs.append(spec)
        self.ctx.config.set("widgets", specs)
        return self._open(spec)

    def remove(self, widget_id: str) -> None:
        window = self.windows.pop(widget_id, None)
        if window:
            window.close()
            window.deleteLater()
        self.ctx.config.set("widgets", [s for s in self.ctx.config.get("widgets") if s["id"] != widget_id])
        if self.gallery and self.gallery.isVisible():
            self.gallery.current = None if self.gallery.current is window else self.gallery.current
            self.gallery.refresh()

    def update_spec(self, widget_id: str, **fields) -> None:
        specs = self.ctx.config.get("widgets")
        for spec in specs:
            if spec["id"] == widget_id:
                spec.update(fields)
        self.ctx.config.set("widgets", specs)

    def open_gallery(self, window: DeskletWindow | None = None) -> None:
        from ui.widgets.gallery import WidgetGallery  # die Galerie braucht ihrerseits die Typenliste
        if self.gallery is None:
            self.gallery = WidgetGallery(self.ctx)
        self.gallery.open_for(window if isinstance(window, DeskletWindow) else None)

    def shutdown(self) -> None:
        for window in self.windows.values():
            window.close()
        if self.gallery:
            self.gallery.close()
