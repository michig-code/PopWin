"""Einstellungsseite „Themes“: Galerie aller Themes, Modus und Kacheln, und ein Editor für eigene Themes mit
Live-Vorschau. Eigene Themes lassen sich als .popwin-theme-Datei teilen."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from core import theme_maker
from core.theme_manager import THEME_DIR, TILE_MODES, TILE_SHAPES

# Farben für die kleinen Vorschaukarten der eingebauten Themes: (Hintergrund, Fläche, Akzent, Akzent 2)
SWATCHES = {
    "liquid_glass": ("#1B2230", "#3A4458", "#FFFFFF", "#9CC8FF"),
    "pop_basic": ("#2A2A2A", "#3C3C3C", "#48B9C7", "#FAA41A"),
    "clean": ("#202020", "#2E2E2E", "#0078D4", "#60CDFF"),
    "minimalist": ("#111111", "#1E1E1E", "#E6E6E6", "#9A9A9A"),
    "tiles_graphite": ("#1A1C20", "#2A2D33", "#C9CED6", "#8A919C"),
    "tiles_obsidian": ("#0C0D12", "#1A1C24", "#7F8BA8", "#B7C1D9"),
    "tiles_frost": ("#1C2633", "#2E3B4C", "#D8ECFF", "#8FC3F0"),
    "glass_clear": ("#141922", "#2A3241", "#FFFFFF", "#BFD8FF"),
    "glass_smoke": ("#101114", "#24262C", "#D0D0D0", "#8E8E95"),
}


def swatch(name: str) -> tuple[str, str, str, str]:
    if name == "neon_night":
        spec = theme_maker.NEON_SPEC
    elif theme_maker.is_custom(name):
        spec = theme_maker.load_spec(name) or theme_maker.DEFAULT_SPEC
    else:
        return SWATCHES.get(name, ("#1E1E1E", "#2E2E2E", "#FFFFFF", "#AAAAAA"))
    return spec["bg"], spec["bg2"], spec["accent"], spec["accent2"]


class ThemeCard(QToolButton):
    """Kleine gemalte Vorschau: Fenster mit Leiste, Karte und Knopf in den Farben des Themes."""

    def __init__(self, name: str, label: str):
        super().__init__(objectName="ThemeCard")
        self.name = name
        self.colors = swatch(name)
        self.setText(label)
        self.setCheckable(True)
        self.setFixedSize(150, 112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)

    def paintEvent(self, _event) -> None:
        bg, surface, accent, accent2 = (QColor(c) for c in self.colors)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        gradient = QLinearGradient(box.topLeft(), box.bottomRight())
        gradient.setColorAt(0, bg)
        gradient.setColorAt(1, surface)
        painter.setPen(QPen(accent2 if self.isChecked() else QColor(255, 255, 255, 40 if self.underMouse() else 22),
                            2.5 if self.isChecked() else 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(box, 12, 12)
        # Leiste oben, Karte, zwei Knöpfe
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRoundedRect(QRectF(box.left() + 10, box.top() + 10, box.width() - 20, 10), 5, 5)
        painter.setBrush(QColor(surface.red(), surface.green(), surface.blue(), 210))
        painter.drawRoundedRect(QRectF(box.left() + 10, box.top() + 28, box.width() * 0.55, 34), 8, 8)
        pill = QLinearGradient(box.left() + 10, 0, box.left() + 70, 0)
        pill.setColorAt(0, accent)
        pill.setColorAt(1, accent2)
        painter.setBrush(pill)
        painter.drawRoundedRect(QRectF(box.right() - 52, box.top() + 30, 40, 12), 6, 6)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(QRectF(box.right() - 52, box.top() + 48, 40, 12), 6, 6)
        painter.setPen(QColor("#14171D") if theme_maker.is_light(self.colors[0]) else QColor("#FFFFFF"))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(box.left() + 10, box.bottom() - 30, box.width() - 20, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()


def swatch_icon(name: str, size: int = 18) -> QIcon:
    """Kleiner runder Farbklecks des Themes für Listen und das Dropdown."""
    bg, _surface, accent, accent2 = (QColor(c) for c in swatch(name))
    pixmap = QPixmap(size * 2, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
    painter.setBrush(bg)
    painter.drawRoundedRect(QRectF(0.5, 0.5, size * 2 - 1, size - 1), size / 2, size / 2)
    gradient = QLinearGradient(size * 0.6, 0, size * 1.6, 0)
    gradient.setColorAt(0, accent)
    gradient.setColorAt(1, accent2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(size * 0.7, size * 0.22, size * 0.56, size * 0.56))
    painter.drawEllipse(QRectF(size * 1.2, size * 0.22, size * 0.56, size * 0.56))
    painter.end()
    return QIcon(pixmap)


class ThemeQuickPick(QWidget):
    """Für die Übersicht: das aktuelle Theme groß als Vorschau und daneben ein eigenes Dropdown zum Wechseln."""

    def __init__(self, ctx, menu_factory=None):
        super().__init__()
        self.ctx = ctx
        self.menu_factory = menu_factory
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        self.card = ThemeCard(ctx.theme.current, "")
        self.card.setFixedSize(190, 130)
        self.card.setChecked(True)
        self.card.clicked.connect(lambda: self.card.setChecked(True))  # nur Vorschau
        row.addWidget(self.card)
        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(QLabel("Aktuelles Theme", objectName="Muted"))
        self.dropdown = QPushButton(objectName="ThemeDropdown")
        self.dropdown.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dropdown.setMinimumHeight(40)
        self.dropdown.setMinimumWidth(220)
        self.dropdown.setStyleSheet("QPushButton#ThemeDropdown { text-align: left; padding: 6px 12px; "
                                    "border-radius: 10px; font-weight: 600; }")
        self.dropdown.clicked.connect(self.open_menu)
        side.addWidget(self.dropdown)
        self.hint = QLabel("Mehr Auswahl und eigene Themes gibt es unter „Themes“.", objectName="Muted")
        self.hint.setWordWrap(True)
        side.addWidget(self.hint)
        side.addStretch(1)
        row.addLayout(side, 1)
        ctx.theme.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        try:
            name = self.ctx.theme.current
            label = self.ctx.theme.all_themes().get(name, name)
        except RuntimeError:  # Fenster schon zu
            return
        self.card.name = name
        self.card.colors = swatch(name)
        self.card.setText(label)
        self.card.update()
        self.dropdown.setText(f"{label}   ▾")
        self.dropdown.setIcon(swatch_icon(name))

    def build_menu(self):
        from PySide6.QtWidgets import QMenu
        menu = self.menu_factory(self) if self.menu_factory else QMenu(self)
        current = self.ctx.theme.current
        for name, label in self.ctx.theme.all_themes().items():
            action = menu.addAction(swatch_icon(name), label)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(lambda _on=False, n=name: self.choose(n))
        return menu

    def open_menu(self) -> None:
        menu = self.build_menu()
        menu.setMinimumWidth(self.dropdown.width())
        menu.popup(self.dropdown.mapToGlobal(self.dropdown.rect().bottomLeft()))

    def choose(self, name: str) -> None:
        self.ctx.theme.set_theme(name)
        self.refresh()


class ColorButton(QPushButton):
    picked = Signal(str)

    def __init__(self, color: str, title: str):
        super().__init__(objectName="Secondary")
        self.title = title
        self.set_color(color)
        self.clicked.connect(self._choose)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setText(color)
        text = "#14171D" if theme_maker.is_light(color) else "#FFFFFF"
        self.setStyleSheet(f"QPushButton {{ background: {color}; color: {text}; border: 1px solid rgba(255,255,255,0.3); "
                           "border-radius: 10px; padding: 6px 14px; font-weight: 600; }}")

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self.color), self, self.title)
        if chosen.isValid():
            self.set_color(chosen.name().upper())
            self.picked.emit(self.color)


class ThemePreview(QFrame):
    """Echte PopWin-Bausteine mit dem Stylesheet des Theme-Entwurfs."""

    def __init__(self):
        super().__init__(objectName="Panel")
        self.setMinimumHeight(230)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        bar = QFrame(objectName="Bar")
        bar.setFixedHeight(36)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 0, 10, 0)
        row.addWidget(QLabel("PopWin", objectName="UserLabel"))
        row.addStretch(1)
        chip = QPushButton("1", objectName="WorkspaceChip")
        chip.setProperty("current", True)
        row.addWidget(chip)
        row.addWidget(QPushButton("2", objectName="WorkspaceChip"))
        row.addStretch(1)
        row.addWidget(QLabel("12:34", objectName="ClockTime"))
        box.addWidget(bar)
        box.addWidget(QLabel("Vorschau", objectName="PanelTitle"))
        box.addWidget(QLabel("So sehen Menüs, Knöpfe und Regler aus.", objectName="Muted"))
        buttons = QHBoxLayout()
        buttons.addWidget(QPushButton("Weiter", objectName="Primary"))
        buttons.addWidget(QPushButton("Abbrechen", objectName="Secondary"))
        buttons.addStretch(1)
        box.addLayout(buttons)
        controls = QHBoxLayout()
        check = QCheckBox("Eingeschaltet")
        check.setChecked(True)
        controls.addWidget(check)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setValue(60)
        controls.addWidget(slider, 1)
        box.addLayout(controls)
        progress = QProgressBar()
        progress.setValue(70)
        progress.setTextVisible(False)
        box.addWidget(progress)


class ThemePage(QObject):
    """Füllt die Seite `page`; `settings` ist das Einstellungsfenster (für ctx und Dateidialoge)."""

    def __init__(self, settings, page):
        super().__init__(page)
        self.settings = settings
        self.ctx = settings.ctx
        self.page = page
        self.editing = ""  # Kennung des eigenen Themes, das gerade im Editor ist
        self.cards: list[ThemeCard] = []
        self._build()
        self.ctx.theme.changed.connect(self._mark_current)

    # --- Galerie ------------------------------------------------------------
    def _build(self) -> None:
        page = self.page
        gallery = QGroupBox("Themes")
        self.grid = QGridLayout(gallery)
        self.grid.setSpacing(10)
        page.box.add_full(gallery)
        self.fill_gallery()

        form = page.group("Darstellung")
        mode = QComboBox()
        mode.addItem("Dunkel", "dark")
        mode.addItem("Hell", "light")
        mode.setCurrentIndex(mode.findData(self.ctx.theme.mode))
        mode.currentIndexChanged.connect(lambda _i: self.ctx.set_dark_mode(mode.currentData()))
        self.settings.mode_combo = mode
        form.addRow("Modus (auch für Windows)", mode)
        tiles = QComboBox()
        for value, label in TILE_MODES.items():
            tiles.addItem(label, value)
        tiles.setCurrentIndex(tiles.findData(self.ctx.theme.tile_mode))
        tiles.currentIndexChanged.connect(lambda _i: self.ctx.theme.set_tile_mode(tiles.currentData()))
        form.addRow("Kacheln um die Icons", tiles)
        shape = QComboBox()
        for value, label in TILE_SHAPES.items():
            shape.addItem(label, value)
        shape.setCurrentIndex(shape.findData(self.ctx.theme.tile_shape))
        shape.currentIndexChanged.connect(lambda _i: self.ctx.theme.set_tile_shape(shape.currentData()))
        form.addRow("Form der Kacheln", shape)
        page.note("Hell und Dunkel gelten für alle Themes, auch für deine eigenen.")

        self._build_editor()

        form = page.group("Für Profis")
        files = QPushButton("Theme-Dateien öffnen", objectName="Secondary")
        from core import win_api
        files.clicked.connect(lambda: win_api.shell_open(str(THEME_DIR)))
        form.addRow(files)
        page.note("Die .qss-Dateien sind reines CSS. Änderungen gelten beim Speichern sofort, ohne Neustart.")

    def fill_gallery(self) -> None:
        for card in self.cards:
            card.deleteLater()
        self.cards = []
        themes = self.ctx.theme.all_themes()
        for number, (name, label) in enumerate(themes.items()):
            card = ThemeCard(name, label)
            card.clicked.connect(lambda _c=False, n=name: self._choose(n))
            self.grid.addWidget(card, number // 4, number % 4)
            self.cards.append(card)
        self._mark_current()

    def _mark_current(self) -> None:
        for card in self.cards:
            card.setChecked(card.name == self.ctx.theme.current)

    def _choose(self, name: str) -> None:
        self.ctx.theme.set_theme(name)
        self._mark_current()
        if theme_maker.is_custom(name):
            self.load_into_editor(name)

    # --- Editor -------------------------------------------------------------
    def _build_editor(self) -> None:
        box = QGroupBox("Eigenes Theme erstellen")
        layout = QHBoxLayout(box)
        left = QFormLayout()
        left.setVerticalSpacing(8)
        self.spec = dict(theme_maker.DEFAULT_SPEC)
        self.name_field = QLineEdit(self.spec["name"])
        self.name_field.setMaxLength(40)
        self.name_field.textChanged.connect(lambda text: self._set("name", text))
        left.addRow("Name", self.name_field)
        self.colors: dict[str, ColorButton] = {}
        for key, label in (("accent", "Akzentfarbe"), ("accent2", "Zweite Akzentfarbe (Verlauf)"),
                           ("bg", "Hintergrund"), ("bg2", "Zweite Hintergrundfarbe")):
            button = ColorButton(self.spec[key], label)
            button.picked.connect(lambda color, k=key: self._set(k, color))
            self.colors[key] = button
            left.addRow(label, button)
        self.sliders: dict[str, QSlider] = {}
        for key, label in (("alpha", "Deckkraft"), ("corner", "Ecken-Rundung"), ("glow", "Leuchten")):
            low, high = theme_maker.LIMITS[key]
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(self.spec[key])
            slider.valueChanged.connect(lambda value, k=key: self._set(k, value))
            self.sliders[key] = slider
            left.addRow(label, slider)
        actions = QGridLayout()
        self.save_new = QPushButton("Als neues Theme speichern", objectName="Primary")
        self.save_new.clicked.connect(lambda: self._save(new=True))
        self.save_old = QPushButton("Änderungen speichern", objectName="Secondary")
        self.save_old.clicked.connect(lambda: self._save(new=False))
        self.remove = QPushButton("Löschen", objectName="Secondary")
        self.remove.clicked.connect(self._delete)
        share = QPushButton("Teilen (Datei)…", objectName="Secondary")
        share.clicked.connect(self._export)
        load = QPushButton("Theme-Datei laden…", objectName="Secondary")
        load.clicked.connect(self._import)
        start = QPushButton("Von Neon-Nacht starten", objectName="Secondary")
        start.clicked.connect(lambda: self._fill(dict(theme_maker.NEON_SPEC, name="Mein Neon")))
        for number, button in enumerate((self.save_new, self.save_old, self.remove, share, load, start)):
            actions.addWidget(button, number // 2, number % 2)
        left.addRow(actions)
        self.status = QLabel("", objectName="Muted", wordWrap=True)
        left.addRow(self.status)
        layout.addLayout(left, 1)
        self.preview = ThemePreview()
        layout.addWidget(self.preview, 1)
        self.page.box.add_full(box)
        self._update_buttons()
        self._refresh_preview()

    def _set(self, key: str, value) -> None:
        self.spec[key] = value
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        self.preview.setStyleSheet(self.ctx.theme.preview_sheet(self.spec))

    def _fill(self, spec: dict, editing: str = "") -> None:
        spec = theme_maker.clean_spec(spec)
        self.editing = editing
        for widget in (self.name_field, *self.colors.values(), *self.sliders.values()):
            widget.blockSignals(True)
        self.name_field.setText(spec["name"])
        for key, button in self.colors.items():
            button.set_color(spec[key])
        for key, slider in self.sliders.items():
            slider.setValue(spec[key])
        for widget in (self.name_field, *self.colors.values(), *self.sliders.values()):
            widget.blockSignals(False)
        self.spec = spec
        self._update_buttons()
        self._refresh_preview()

    def load_into_editor(self, name: str) -> None:
        spec = theme_maker.load_spec(name)
        if spec:
            self._fill(spec, name)

    def _update_buttons(self) -> None:
        self.save_old.setEnabled(bool(self.editing))
        self.remove.setEnabled(bool(self.editing))

    def _save(self, new: bool) -> None:
        name = theme_maker.save_spec(self.spec, "" if new else self.editing)
        self.editing = name
        self.ctx.theme.forget(name)
        self.fill_gallery()
        self.ctx.theme.set_theme(name)
        self._mark_current()
        self._update_buttons()
        self.status.setText(f"„{self.spec['name']}“ gespeichert und eingeschaltet.")

    def _delete(self) -> None:
        if not self.editing:
            return
        answer = QMessageBox.question(self.settings, "Theme löschen", f"„{self.spec['name']}“ wirklich löschen?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.ctx.theme.current == self.editing:
            self.ctx.theme.set_theme("neon_night")
        theme_maker.delete_spec(self.editing)
        self.ctx.theme.forget(self.editing)
        self.editing = ""
        self.fill_gallery()
        self._update_buttons()
        self.status.setText("Theme gelöscht.")

    def _export(self) -> None:
        from ui.file_dialog import pick_save
        target = pick_save(self.settings, "Theme teilen", f"PopWin-Theme (*{theme_maker.FILE_SUFFIX})")
        if not target:
            return
        path = Path(target)
        if path.suffix != theme_maker.FILE_SUFFIX:
            path = path.with_name(path.name + theme_maker.FILE_SUFFIX)
        try:
            theme_maker.export_spec(self.spec, path)
            self.status.setText(f"Gespeichert: {path.name}. Diese Datei kannst du weitergeben.")
        except OSError as exc:
            self.status.setText(f"Nicht gespeichert: {exc}")

    def _import(self) -> None:
        from ui.file_dialog import pick_files
        chosen = pick_files(self.settings, "Theme-Datei laden", f"PopWin-Theme (*{theme_maker.FILE_SUFFIX} *.json)")
        if not chosen:
            return
        try:
            spec = theme_maker.read_shared(Path(chosen[0]))
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._fill(spec)
        self.status.setText("Geladen. Mit „Als neues Theme speichern“ übernimmst du es.")
