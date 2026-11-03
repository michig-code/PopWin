"""Galerie: Widgets hinzufügen und jedes einzelne anpassen. Öffnet sich per Rechtsklick auf ein Widget."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import win_api
from ui.base import glyph_icon, resettable
from ui.widgets import WIDGET_TYPES
from ui.widgets.base import DeskletWindow, Option
from ui.window_frame import NativeWindow

COLUMNS = 2


class WidgetGallery(NativeWindow):
    def __init__(self, ctx):
        super().__init__(ctx, "PopWin Widgets", "apps/widgets")
        self.resize(960, 640)
        self.current: DeskletWindow | None = None
        layout = QHBoxLayout(self.content)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.addWidget(QLabel("Widget hinzufügen", objectName="PanelTitle"))
        cards = QWidget(objectName="SettingsPage")
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.cards: list[tuple[QPushButton, str]] = []
        for index, (kind, cls) in enumerate(WIDGET_TYPES.items()):
            card = QPushButton(f"{cls.TITLE}\n{cls.DESCRIPTION}", objectName="WidgetCard")
            card.setIconSize(QSize(22, 22))
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.clicked.connect(lambda _=False, k=kind: self._add(k))
            grid.addWidget(card, index // COLUMNS, index % COLUMNS)
            self.cards.append((card, cls.GLYPH))
        grid.setRowStretch(grid.rowCount(), 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(cards)
        left.addWidget(scroll, 1)
        layout.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(QLabel("Widget anpassen", objectName="PanelTitle"))
        self.chooser = QComboBox()
        self.chooser.currentIndexChanged.connect(self._chooser_changed)
        right.addWidget(self.chooser)
        self.form_host = QScrollArea()
        self.form_host.setWidgetResizable(True)
        self.form_host.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_host.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(self.form_host, 1)
        layout.addLayout(right, 2)

    # --- Öffnen -------------------------------------------------------------
    def open_for(self, window: DeskletWindow | None) -> None:
        self.apply_theme()
        color = "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"
        for card, glyph in self.cards:
            card.setIcon(glyph_icon(glyph, 22, color))
        self.current = window or self.current
        self._fill_chooser()
        # Auch nach dem Schließen oder Minimieren wieder nach vorn, nicht nur "sichtbar" im Hintergrund.
        self.showNormal()
        self.raise_()
        self.activateWindow()
        win_api.force_foreground(int(self.winId()))

    def refresh(self) -> None:
        self._fill_chooser()

    def _add(self, kind: str) -> None:
        window = self.ctx.widgets.add(kind)
        self.current = window
        self._fill_chooser()

    def _fill_chooser(self) -> None:
        windows = list(self.ctx.widgets.windows.items())
        if self.current not in [w for _id, w in windows]:
            self.current = windows[-1][1] if windows else None
        self.chooser.blockSignals(True)
        self.chooser.clear()
        for widget_id, window in windows:
            self.chooser.addItem(window.TITLE, widget_id)
        if self.current:
            self.chooser.setCurrentIndex(self.chooser.findData(self.current.spec["id"]))
        self.chooser.setEnabled(bool(windows))
        self.chooser.blockSignals(False)
        self._build_form()

    def _chooser_changed(self, _index: int) -> None:
        self.current = self.ctx.widgets.windows.get(self.chooser.currentData())
        self._build_form()

    # --- Formular -----------------------------------------------------------
    def _build_form(self) -> None:
        host = QWidget(objectName="SettingsPage")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 6, 8, 0)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)
        window = self.current
        if window is None:
            hint = QLabel("Noch kein Widget da. Wähle links eines aus.", objectName="Muted")
            hint.setWordWrap(True)
            box.addWidget(hint)
            self.form_host.setWidget(host)
            return
        form = QFormLayout()
        form.setVerticalSpacing(8)
        for option in window.all_options():
            self._add_field(form, window, option)
        layer = QComboBox()
        layer.addItem("Auf dem Desktop", "desktop")
        layer.addItem("Immer im Vordergrund", "overlay")
        layer.setCurrentIndex(layer.findData(window.spec.get("z", "desktop")))
        layer.currentIndexChanged.connect(lambda _i: window.set_layer(layer.currentData()))
        form.addRow("Ebene", resettable(layer, lambda: layer.setCurrentIndex(0)))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(20, 100)
        slider.setValue(round(window.spec.get("opacity", 1.0) * 100))
        slider.valueChanged.connect(lambda value: window.set_opacity(value / 100))
        form.addRow("Deckkraft", resettable(slider, lambda: slider.setValue(100)))
        box.addLayout(form)
        remove = QPushButton("Widget entfernen", objectName="Danger")
        remove.clicked.connect(lambda: self._remove(window))
        box.addWidget(remove)
        self.form_host.setWidget(host)

    def _add_field(self, form: QFormLayout, window: DeskletWindow, option: Option) -> None:
        default = window.default_of(option)
        value = window.option(option.key)
        if option.kind == "bool":
            box = QCheckBox(option.label)
            box.setChecked(value)
            box.toggled.connect(lambda checked: window.set_option(option.key, checked))
            form.addRow(box)
        elif option.kind == "int":
            spin = QSpinBox()
            spin.setRange(option.low, option.high)
            spin.setSuffix(" px")
            spin.setValue(value)
            spin.valueChanged.connect(lambda number: window.set_option(option.key, number))
            form.addRow(option.label, resettable(spin, lambda: spin.setValue(default)))
        elif option.kind == "choice":
            combo = QComboBox()
            for data, text in option.choices.items():
                combo.addItem(text, data)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.currentIndexChanged.connect(lambda _i: window.set_option(option.key, combo.currentData()))
            form.addRow(option.label, resettable(combo, lambda: combo.setCurrentIndex(combo.findData(default))))
        else:
            form.addRow(option.label, self._color_field(window, option, default))

    def _color_field(self, window: DeskletWindow, option: Option, default: str) -> QWidget:
        button = QPushButton(objectName="Secondary")

        def show(color: str) -> None:
            button.setText(color or "Farbe des UI-Stils")

        def pick() -> None:
            chosen = QColorDialog.getColor(window.accent(), self, option.label)
            if chosen.isValid():
                window.set_option(option.key, chosen.name())
                show(chosen.name())

        def reset() -> None:
            window.set_option(option.key, default)
            show(default)

        show(window.option(option.key))
        button.clicked.connect(pick)
        return resettable(button, reset)

    def _remove(self, window: DeskletWindow) -> None:
        self.ctx.widgets.remove(window.spec["id"])
        self.current = None
        self._fill_chooser()
