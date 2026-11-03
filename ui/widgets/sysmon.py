"""System-Monitor: CPU, RAM und GPU auf einen Blick."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar

from ui.base import G
from ui.widgets.base import DeskletWindow, Option
from ui.widgets.system import accent_bar


class SystemMonitorWidget(DeskletWindow):
    TITLE = "System-Monitor"
    GLYPH = G.CPU
    DESCRIPTION = "CPU, RAM und GPU zusammen"
    OPTIONS = (
        Option("show_cpu", "CPU anzeigen", "bool", True),
        Option("show_ram", "RAM anzeigen", "bool", True),
        Option("show_gpu", "GPU anzeigen", "bool", True),
    )

    def build(self) -> None:
        self.add_title()
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.rows = {}
        for index, (key, label) in enumerate((("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"))):
            name, bar = QLabel(label), QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            value = QLabel("–", objectName="Muted")
            grid.addWidget(name, index, 0)
            grid.addWidget(bar, index, 1)
            grid.addWidget(value, index, 2)
            self.rows[key] = (name, bar, value)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        self.ctx.status.updated.connect(self.update_status)
        self.update_status(self.ctx.status.state)

    def options_changed(self) -> None:
        for key, row in self.rows.items():
            visible = self.option(f"show_{key}")
            for part in row:
                part.setVisible(visible)
            accent_bar(row[1], self.option("accent"))

    def update_status(self, state: dict) -> None:
        for key, (_name, bar, label) in self.rows.items():
            value = state.get(key)
            if value is None:
                label.setText("–")
            else:
                bar.setValue(round(value))
                label.setText(f"{value:.0f} %")
