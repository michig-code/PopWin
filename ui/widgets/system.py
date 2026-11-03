"""Laufwerke, aktive Programme und Laufzeit."""
from __future__ import annotations

import time

import psutil
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar

from ui.base import G, glyph_icon, icon_for_target
from ui.widgets.base import DeskletWindow, Option


def accent_bar(bar: QProgressBar, color: str) -> None:
    bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}" if color else "")


def format_uptime(seconds: float, show_seconds: bool = False) -> str:
    total = int(seconds)
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, rest = divmod(total, 60)
    parts = [f"{days} T"] if days else []
    parts += [f"{hours} Std", f"{minutes} Min"]
    if show_seconds:
        parts.append(f"{rest} Sek")
    return " ".join(parts)


class DisksWidget(DeskletWindow):
    TITLE = "Laufwerke"
    GLYPH = G.DISK
    DESCRIPTION = "Belegung aller Laufwerke"
    DEFAULT_WIDTH = 260
    OPTIONS = (Option("free", "Freien Platz statt Belegung zeigen", "bool", False),)

    def build(self) -> None:
        self.add_title()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.body.addLayout(self.grid)
        self.rows: dict[str, tuple[QProgressBar, QLabel]] = {}
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def update_status(self, state: dict) -> None:
        for disk, used, total in state["disks"]:
            if disk not in self.rows:
                bar, label = QProgressBar(), QLabel(objectName="Muted")
                bar.setRange(0, 100)
                bar.setTextVisible(False)
                row = self.grid.rowCount()
                self.grid.addWidget(QLabel(disk), row, 0)
                self.grid.addWidget(bar, row, 1)
                self.grid.addWidget(label, row, 2)
                self.grid.setColumnStretch(1, 1)
                self.rows[disk] = (bar, label)
                self.options_changed()
            bar, label = self.rows[disk]
            bar.setValue(round(used / total * 100) if total else 0)
            shown = total - used if self.option("free") else used
            label.setText(f"{shown / 1024 ** 3:.0f} GB")
        self.adjustSize()

    def options_changed(self) -> None:
        for bar, _label in self.rows.values():
            accent_bar(bar, self.option("accent"))


class ProcessesWidget(DeskletWindow):
    TITLE = "Aktive Programme"
    GLYPH = G.LIST
    DESCRIPTION = "Was gerade am meisten Leistung zieht"
    DEFAULT_WIDTH = 280
    OPTIONS = (
        Option("count", "Anzahl", "int", 5, 3, 8),
        Option("sort", "Sortieren nach", "choice", "cpu", choices={"cpu": "CPU", "ram": "Arbeitsspeicher"}),
    )

    def build(self) -> None:
        self.ctx.status.want_processes = True
        self.add_title()
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.body.addLayout(self.grid)
        self.rows = []
        for index in range(8):
            icon, name, value = QLabel(), QLabel(), QLabel(objectName="Muted")
            self.grid.addWidget(icon, index, 0)
            self.grid.addWidget(name, index, 1)
            self.grid.addWidget(value, index, 2)
            self.rows.append((icon, name, value))
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def update_status(self, state: dict) -> None:
        by_ram = self.option("sort") == "ram"
        items = sorted(state["processes"], key=lambda p: p[2] if by_ram else p[1], reverse=True)
        count = self.option("count")
        for index, (icon, name, value) in enumerate(self.rows):
            visible = index < min(count, len(items))
            icon.setVisible(visible)
            name.setVisible(visible)
            value.setVisible(visible)
            if visible:
                process, cpu, memory, exe = items[index]
                label = process.removesuffix(".exe")
                icon.setPixmap(self._process_icon(exe or process).pixmap(QSize(18, 18)))
                name.setText(name.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, self.width() - 138))
                value.setText(f"{memory if by_ram else cpu:.0f} %")
        self.adjustSize()

    def _process_icon(self, target: str) -> QIcon:
        """Windows verrät den Pfad von Systemprozessen nicht; sie werden über ihren Namen gesucht."""
        icon = icon_for_target(target)
        if not icon.isNull():
            return icon
        return glyph_icon(G.APP, 18, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020")

    def options_changed(self) -> None:
        self.update_status(self.ctx.status.state)


class UptimeWidget(DeskletWindow):
    TITLE = "PC läuft seit"
    GLYPH = G.UPTIME
    DESCRIPTION = "Zeit seit dem letzten Start"
    DEFAULT_WIDTH = 210
    OPTIONS = (Option("seconds", "Sekunden anzeigen", "bool", False),)

    def build(self) -> None:
        self.add_title()
        self.value = QLabel(objectName="MediaTitle")
        self.body.addWidget(self.value)
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def update_status(self, _state: dict) -> None:
        self.value.setText(format_uptime(time.time() - psutil.boot_time(), self.option("seconds")))
