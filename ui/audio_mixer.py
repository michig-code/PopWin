"""Lautstärkemixer: ein Regler pro App und ein Live-Pegel, der zeigt, was gerade wirklich zu hören ist."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QSlider, QVBoxLayout, QWidget

from core import audio_mixer, system_status
from ui.base import G, IconButton, glyph_icon, icon_for_target, set_prop

METER_MS = 80
START_DELAY_MS = 40  # erst die Seite zeichnen lassen
LIST_EVERY = 25  # Takte: etwa alle zwei Sekunden nach neuen Apps schauen
METER_FALL = 0.82  # der Balken fällt weich statt zu springen
SILENT = 0.002
ICON = 22


class AppRow:
    def __init__(self, grid: QGridLayout, row: int, app: audio_mixer.AppAudio | None, label: str, color: str):
        self.app = app
        self.level = 0.0
        icon = QLabel()
        picture = icon_for_target(app.exe) if app and app.exe else None
        if picture is None or picture.isNull():
            picture = glyph_icon(G.VOLUME, ICON, color)
        icon.setPixmap(picture.pixmap(QSize(ICON, ICON)))
        self.name = QLabel(label)
        self.name.setToolTip(app.exe if app else "")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.value = QLabel(objectName="Muted")
        self.value.setFixedWidth(38)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mute = IconButton(G.VOLUME, "Stumm")
        self.mute.clicked.connect(self._toggle_mute)
        self.meter = QProgressBar()
        self.meter.setRange(0, 1000)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(4)
        self.state = QLabel(objectName="Muted")
        self.state.setFixedWidth(64)
        self.widgets = (icon, self.name, self.slider, self.value, self.mute, self.meter, self.state)
        grid.addWidget(icon, row * 2, 0, 2, 1)
        grid.addWidget(self.name, row * 2, 1)
        grid.addWidget(self.slider, row * 2, 2)
        grid.addWidget(self.value, row * 2, 3)
        grid.addWidget(self.mute, row * 2, 4, 2, 1)
        grid.addWidget(self.state, row * 2 + 1, 1)
        grid.addWidget(self.meter, row * 2 + 1, 2, 1, 2)
        self.slider.valueChanged.connect(self._changed)
        self.read()

    # --- die Geräte-Zeile hat statt einer App das ganze Gerät --------------------
    def _volume(self) -> int:
        return self.app.volume() if self.app else (system_status.read_volume() or (0, False))[0]

    def _muted(self) -> bool:
        return self.app.muted() if self.app else (system_status.read_volume() or (0, False))[1]

    def read(self) -> None:
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(self._volume())
            self.slider.blockSignals(False)
        self.value.setText(f"{self.slider.value()} %")
        muted = self._muted()
        self.mute.setText(G.MUTE if muted else G.VOLUME)
        set_prop(self.mute, "off", muted)

    def _changed(self, value: int) -> None:
        if self.app:
            self.app.set_volume(value)
        else:
            system_status.write_volume(value)
        self.value.setText(f"{value} %")

    def _toggle_mute(self) -> None:
        if self.app:
            self.app.set_muted(not self.app.muted())
        else:
            system_status.toggle_mute()
        self.read()

    def show_level(self, peak: float) -> None:
        self.level = max(peak, self.level * METER_FALL)
        self.meter.setValue(round(self.level ** 0.5 * 1000))  # Wurzel: leise Musik soll man noch sehen
        self.state.setText("spielt" if peak > SILENT else "still")

    def remove(self) -> None:
        for widget in self.widgets:
            widget.hide()
            widget.deleteLater()


class AudioMixer(QWidget):
    """Läuft nur, solange man es sieht: Der Pegel wird sonst umsonst gelesen."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.rows: dict[str, AppRow] = {}
        self.device: AppRow | None = None
        self.device_meter = None
        self._ticks = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(2, 1)
        self.grid.setColumnMinimumWidth(1, 150)
        box.addLayout(self.grid)
        self.empty = QLabel("Gerade spielt keine App Ton ab.", objectName="Muted")
        box.addWidget(self.empty)
        self.timer = QTimer(self, interval=METER_MS)
        self.timer.timeout.connect(self._tick)

    def _color(self) -> str:
        return "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Die Abfrage bei Windows dauert beim ersten Mal rund 0,2 s; erst die Seite zeigen, dann die Apps nachladen.
        QTimer.singleShot(START_DELAY_MS, self._start)

    def _start(self) -> None:
        if not self.isVisible():
            return
        if self.device is None:
            self.device = AppRow(self.grid, 0, None, "Gesamt", self._color())
        self.device_meter = audio_mixer.device_meter()
        self.refresh()
        self.timer.start()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        """Neue Apps kommen dazu, beendete fallen weg; Zeilen, die bleiben, zucken nicht."""
        found = audio_mixer.read_apps()
        wanted = {app.key: app for app in found}
        for key in [key for key in self.rows if key not in wanted]:
            self.rows.pop(key).remove()
        for app in found:
            if app.key in self.rows:
                self.rows[app.key].app = app
            else:
                self.rows[app.key] = AppRow(self.grid, self.grid.rowCount(), app, app.name, self._color())
        self.empty.setVisible(not self.rows)
        for row in self.rows.values():
            row.read()
        if self.device:
            self.device.read()

    def _tick(self) -> None:
        self._ticks += 1
        if self._ticks % LIST_EVERY == 0:
            self.refresh()
        for row in self.rows.values():
            row.show_level(row.app.peak())
        if self.device and self.device_meter is not None:
            try:
                self.device.show_level(self.device_meter.GetPeakValue())
            except OSError:
                self.device_meter = audio_mixer.device_meter()
