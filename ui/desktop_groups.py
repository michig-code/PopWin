"""Beta: Der Desktop wie ein Handy-Startbildschirm. Zieht man ein Icon auf ein anderes, entsteht eine Gruppe.

Eine Gruppe zeigt zu nur kleine Icons, offen die ganzen Namen. Die Dateien bleiben, wo sie sind: Gruppen gibt es nur in PopWin,
schaltet man es aus, ist der Desktop wie vorher. Das Fenster ist nur dort dicht, wo Icons liegen, daneben bleibt der echte Desktop.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QRegion
from PySide6.QtWidgets import QGridLayout, QInputDialog, QLabel, QVBoxLayout, QWidget

from core import i18n, win_api
from ui.base import ThemedWindow, icon_for_target, themed_menu

STYLES = {"off": "Aus", "folder": "Handy-Ordner", "stack": "Stapel", "shelf": "Regal"}
GROUP_PREFIX = "group:"
CELL = QSize(96, 104)
OPEN_COLUMNS = {"folder": 4, "stack": 1, "shelf": 6}
ROW_CELL = QSize(260, 46)  # im Stapel steht der Name neben dem Icon
ICON = 46
MINI = 14
MARGIN = 14
DRAG_START = 8
BOTTOM_MS = 700
EMPTY_LAYOUT = {"order": [], "groups": {}}
PANEL_STYLE = "#Desklet { background: rgba(20, 20, 24, 245); border-radius: 16px; } QLabel { color: white; }"  # lesbar auf jedem Hintergrund


@dataclass(frozen=True)
class DesktopItem:
    name: str
    path: str


def desktop_folders() -> list[Path]:
    from win32com.shell import shell, shellcon
    return [Path(shell.SHGetFolderPath(0, folder, None, 0)) for folder in (shellcon.CSIDL_DESKTOPDIRECTORY, shellcon.CSIDL_COMMON_DESKTOPDIRECTORY)]


def scan(folders: list[Path]) -> list[DesktopItem]:
    items = []
    for folder in folders:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.name.lower() != "desktop.ini" and not path.name.startswith("~$"):
                items.append(DesktopItem(path.stem if path.suffix.lower() in (".lnk", ".url") else path.name, str(path)))
    return sorted(items, key=lambda item: item.name.lower())


def normalized(layout: dict, paths: list[str]) -> dict:
    """Gleicht die Anordnung mit dem Desktop ab: Gelöschtes fällt weg, Neues kommt ans Ende, Gruppen mit einem Icon lösen sich auf."""
    known = set(paths)
    groups, order = {}, []
    grouped_paths = {path for group in layout["groups"].values() for path in group["items"]}
    for entry in layout["order"]:
        group = layout["groups"].get(entry)
        if group is None:
            if entry in known and entry not in grouped_paths:
                order.append(entry)
            continue
        items = [path for path in group["items"] if path in known]
        if len(items) > 1:
            groups[entry] = {"name": group["name"], "items": items}
            order.append(entry)
        else:
            order += items
    used = set(order) | {path for group in groups.values() for path in group["items"]}
    return {"order": order + [path for path in paths if path not in used], "groups": groups}


def merged(layout: dict, dragged: str, target: str, name: str) -> dict:
    """Das Herz des Ganzen: ein Icon auf ein anderes. Liegt es auf einer Gruppe, kommt es hinein."""
    result = copy.deepcopy(layout)
    if dragged == target or dragged.startswith(GROUP_PREFIX):
        return result
    if target.startswith(GROUP_PREFIX):
        result["groups"][target]["items"].append(dragged)
    else:
        group = GROUP_PREFIX + uuid.uuid4().hex[:8]
        result["groups"][group] = {"name": name, "items": [target, dragged]}
        result["order"][result["order"].index(target)] = group
    result["order"].remove(dragged)
    return result


def taken_out(layout: dict, group: str, path: str, index: int) -> dict:
    result = copy.deepcopy(layout)
    result["groups"][group]["items"].remove(path)
    result["order"].insert(max(0, min(index, len(result["order"]))), path)
    if len(result["groups"][group]["items"]) == 1:  # eine Gruppe mit einem Icon ist keine mehr
        result["order"][result["order"].index(group)] = result["groups"].pop(group)["items"][0]
    return result


def moved_to(layout: dict, entry: str, index: int) -> dict:
    result = copy.deepcopy(layout)
    result["order"].remove(entry)
    result["order"].insert(max(0, min(index, len(result["order"]))), entry)
    return result


def dissolved(layout: dict, group: str) -> dict:
    result = copy.deepcopy(layout)
    index = result["order"].index(group)
    result["order"][index:index + 1] = result["groups"].pop(group)["items"]
    return result


def slot_rect(index: int, rows: int) -> QRect:
    """Von oben nach unten und dann eine Spalte weiter, wie die Symbole von Windows."""
    return QRect(QPoint(MARGIN + index // rows * CELL.width(), MARGIN + index % rows * CELL.height()), CELL)


def slot_at(point: QPoint, rows: int) -> int:
    column = max(0, (point.x() - MARGIN) // CELL.width())
    row = max(0, min(rows - 1, (point.y() - MARGIN) // CELL.height()))
    return column * rows + row


class Cell(QWidget):
    """Ein Icon oder eine geschlossene Gruppe. Text mit Schatten, damit er auf jedem Hintergrund lesbar bleibt."""

    def __init__(self, screen: "HomeScreen", entry: str, name: str, paths: list[str], parent: QWidget, row_mode: bool = False):
        super().__init__(parent)
        self.screen_ref = screen
        self.entry = entry
        self.name = name
        self.paths = paths
        self.row_mode = row_mode
        self.highlight = False
        self._press: QPoint | None = None
        self._dragging = False
        self.setFixedSize(ROW_CELL if row_mode else CELL)
        self.setToolTip(paths[0] if len(paths) == 1 else name)

    @property
    def is_group(self) -> bool:
        return self.entry.startswith(GROUP_PREFIX)

    def set_highlight(self, on: bool) -> None:
        if on != self.highlight:
            self.highlight = on
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.highlight:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 70))
            painter.drawRoundedRect(self.rect(), 12, 12)
        if self.row_mode:
            painter.drawPixmap(8, 7, icon_for_target(self.paths[0]).pixmap(32, 32))
            self._text(painter, QRect(50, 0, self.width() - 54, self.height()), Qt.AlignmentFlag.AlignVCenter)
        else:
            box = QRect((self.width() - ICON) // 2 - 6, 4, ICON + 12, ICON + 12)
            if self.is_group:
                self._paint_group(painter, box)
            else:
                painter.drawPixmap(box.adjusted(6, 6, -6, -6), icon_for_target(self.paths[0]).pixmap(ICON, ICON))
            self._text(painter, QRect(2, box.bottom() + 3, self.width() - 4, 36), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        painter.end()

    def _paint_group(self, painter: QPainter, box: QRect) -> None:
        painter.setPen(QColor(255, 255, 255, 60))
        painter.setBrush(QColor(255, 255, 255, 46))
        painter.drawRoundedRect(box, 14, 14)
        for index, path in enumerate(self.paths[:9]):
            x = box.x() + 7 + index % 3 * (MINI + 2)
            y = box.y() + 7 + index // 3 * (MINI + 2)
            painter.drawPixmap(x, y, icon_for_target(path).pixmap(MINI, MINI))

    def _text(self, painter: QPainter, rect: QRect, align) -> None:
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        text = QFontMetrics(font).elidedText(self.name, Qt.TextElideMode.ElideRight, rect.width() * 2 - 12)
        flags = align | Qt.TextFlag.TextWordWrap
        painter.setPen(QColor(0, 0, 0, 200))
        painter.drawText(rect.translated(1, 1), flags, text)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rect, flags, text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        if not self._dragging and (event.position().toPoint() - self._press).manhattanLength() >= DRAG_START:
            self._dragging = True
            self.screen_ref.drag_started(self)
        if self._dragging:
            self.screen_ref.drag_moved(self, event.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None:
            return
        self._press = None
        if self._dragging:
            self._dragging = False
            self.screen_ref.drag_ended(self)
        elif event.button() == Qt.MouseButton.LeftButton:
            self.screen_ref.activate(self)

    def contextMenuEvent(self, event) -> None:
        self.screen_ref.show_menu(self, event.globalPos())


class HomeScreen(ThemedWindow):
    def __init__(self, manager: "DesktopGroups", style: str):
        super().__init__(manager.ctx, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.manager = manager
        self.style_name = style
        self.cells: dict[str, Cell] = {}
        self.panel: QWidget | None = None
        self.panel_group = ""
        area = manager.ctx.primary_geometry()
        self.setGeometry(area)
        self.rows = max(1, (area.height() - 2 * MARGIN) // CELL.height())

    def layout_data(self) -> dict:
        return self.manager.layout

    def fill(self) -> None:
        self.close_panel()
        for cell in self.cells.values():
            cell.hide()
            cell.deleteLater()
        self.cells = {}
        layout, names = self.layout_data(), self.manager.names
        for index, entry in enumerate(layout["order"]):
            group = layout["groups"].get(entry)
            cell = Cell(self, entry, group["name"] if group else names.get(entry, entry), group["items"] if group else [entry], self)
            cell.setGeometry(slot_rect(index, self.rows))
            cell.show()
            self.cells[entry] = cell
        self.fit_mask()

    def fit_mask(self) -> None:
        """Nur wo Icons liegen, gehören die Klicks PopWin; sonst bleibt alles beim Desktop von Windows."""
        region = QRegion()
        for cell in self.cells.values():
            region = region.united(QRegion(cell.geometry()))
        if self.panel is not None:
            region = QRegion(self.rect())
        self.setMask(region)

    # --- Ziehen ------------------------------------------------------------------
    def drag_started(self, cell: Cell) -> None:
        self.clearMask()
        if cell.parent() is not self:  # aus einer offenen Gruppe heraus
            position = cell.mapTo(self, QPoint(0, 0))
            cell.setParent(self)
            cell.setFixedSize(CELL)
            cell.row_mode = False
            cell.move(position)
            cell.show()
        cell.raise_()

    def _under(self, cell: Cell) -> Cell | None:
        center = cell.geometry().center()
        return next((other for other in self.cells.values() if other is not cell and other.geometry().contains(center)), None)

    def drag_moved(self, cell: Cell, global_position: QPoint) -> None:
        cell.move(self.mapFromGlobal(global_position))
        target = self._under(cell)
        for other in self.cells.values():
            other.set_highlight(other is target)

    def drag_ended(self, cell: Cell) -> None:
        target = self._under(cell)
        layout = self.layout_data()
        if self.panel is not None:
            inside = self.panel.geometry().contains(cell.geometry().center())
            if not inside:
                index = slot_at(cell.geometry().center(), self.rows)
                self.manager.store(taken_out(layout, self.panel_group, cell.entry, index))
                return
            self.manager.store(layout)
            return
        if target is not None:
            self.manager.store(merged(layout, cell.entry, target.entry, self.manager.new_group_name()))
        else:
            self.manager.store(moved_to(layout, cell.entry, slot_at(cell.geometry().center(), self.rows)))

    # --- Klicks ------------------------------------------------------------------
    def activate(self, cell: Cell) -> None:
        if cell.is_group:
            self.open_panel(cell)
        else:
            self.close_panel()
            win_api.shell_open(cell.paths[0])

    def open_panel(self, cell: Cell) -> None:
        self.close_panel()
        self.panel_group = cell.entry
        columns = OPEN_COLUMNS.get(self.style_name, 4)
        row_mode = columns == 1
        panel = QWidget(self, objectName="Desklet")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        panel.setStyleSheet(PANEL_STYLE)
        box = QVBoxLayout(panel)
        title = QLabel(cell.name, objectName="PanelTitle")
        box.addWidget(title)
        grid = QGridLayout()
        grid.setSpacing(2)
        for index, path in enumerate(cell.paths):
            grid.addWidget(Cell(self, path, self.manager.names.get(path, path), [path], panel, row_mode), index // columns, index % columns)
        box.addLayout(grid)
        panel.adjustSize()
        x = min(cell.x(), self.width() - panel.width() - MARGIN)
        y = min(cell.y(), self.height() - panel.height() - MARGIN)
        panel.move(max(MARGIN, x), max(MARGIN, y))
        panel.show()
        self.panel = panel
        self.fit_mask()
        self.raise_()
        self.activateWindow()

    def close_panel(self) -> None:
        if self.panel is not None:
            self.panel.hide()
            self.panel.deleteLater()
            self.panel = None
            self.panel_group = ""
            self.fit_mask()

    def mousePressEvent(self, event) -> None:
        self.close_panel()  # ein Klick daneben schließt die Gruppe wie am Handy

    def event(self, event) -> bool:
        if event.type() == event.Type.WindowDeactivate:
            QTimer.singleShot(0, self.close_panel)
        return super().event(event)

    def show_menu(self, cell: Cell, position: QPoint) -> None:
        menu = themed_menu(self.ctx, cell)
        if cell.is_group:
            menu.addAction("Umbenennen", lambda: self._rename(cell))
            menu.addAction("Gruppe auflösen", lambda: self.manager.store(dissolved(self.layout_data(), cell.entry)))
        else:
            menu.addAction("Öffnen", lambda: win_api.shell_open(cell.paths[0]))
            if self.panel is not None:
                menu.addAction("Aus Gruppe nehmen", lambda: self.manager.store(
                    taken_out(self.layout_data(), self.panel_group, cell.entry, len(self.layout_data()["order"]))))
        menu.popup(position)

    def _rename(self, cell: Cell) -> None:
        name, ok = QInputDialog.getText(self, "Umbenennen", "Name:", text=cell.name)
        if ok and name.strip():
            layout = copy.deepcopy(self.layout_data())
            layout["groups"][cell.entry]["name"] = name.strip()
            self.manager.store(layout)

    def to_bottom(self) -> None:
        if self.panel is None and win_api.class_name(win_api.foreground_window()) not in ("Progman", "WorkerW"):
            win_api.set_zorder(int(self.winId()), win_api.HWND_BOTTOM)


class DesktopGroups:
    """Blendet die Windows-Symbole nur aus, solange es läuft, und holt sie beim Ausschalten zurück."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.screen: HomeScreen | None = None
        self.layout = copy.deepcopy(EMPTY_LAYOUT)
        self.names: dict[str, str] = {}
        self.hid_icons = False
        self.watcher = QFileSystemWatcher()
        self.watcher.directoryChanged.connect(lambda _folder: self._later.start())
        self._later = QTimer(singleShot=True, interval=800)
        self._later.timeout.connect(self.rebuild)
        self._bottom = QTimer(interval=BOTTOM_MS)
        self._bottom.timeout.connect(lambda: self.screen and self.screen.to_bottom())

    def style(self) -> str:
        style = self.ctx.config.get("desktop_groups.style")
        return style if style in OPEN_COLUMNS else "off"

    def new_group_name(self) -> str:
        return i18n.translate("Gruppe {}").replace("{}", str(len(self.layout["groups"]) + 1))

    def sync(self) -> None:
        if self.style() == "off":
            self.stop()
            return
        if not self.watcher.directories():
            self.watcher.addPaths([str(folder) for folder in desktop_folders()])
        if win_api.desktop_icon_state()["visible"]:
            win_api.desktop_command(win_api.DESKTOP_TOGGLE_ICONS)
            self.hid_icons = True
        self.rebuild()
        self._bottom.start()

    def rebuild(self) -> None:
        style = self.style()
        if style == "off":
            return
        items = scan(desktop_folders())
        self.names = {item.path: item.name for item in items}
        self.layout = normalized(self.ctx.config.get("desktop_groups.layout"), [item.path for item in items])
        if self.screen is None or self.screen.style_name != style:
            self._close_screen()
            self.screen = HomeScreen(self, style)
            self.screen.apply_theme()
            self.screen.show()
        self.screen.fill()

    def store(self, layout: dict) -> None:
        """Erst nach dem Mausereignis neu bauen: Die gezogene Zelle wird dabei gelöscht."""
        self.layout = layout
        self.ctx.config.set("desktop_groups.layout", layout)
        QTimer.singleShot(0, self.screen.fill if self.screen else lambda: None)

    def _close_screen(self) -> None:
        if self.screen is not None:
            self.screen.hide()
            self.screen.deleteLater()
            self.screen = None

    def stop(self) -> None:
        self._bottom.stop()
        self._close_screen()
        if self.hid_icons:
            self.hid_icons = False
            win_api.desktop_command(win_api.DESKTOP_TOGGLE_ICONS)
