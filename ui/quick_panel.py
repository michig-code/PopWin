"""Schnelleinstellungen neben der Uhr: kleine Schalter, Fokus-Timer, Abkürzungen."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton

from core import quick_settings as qs
from core import win_api
from ui.audio_outputs import AudioDevices
from ui.base import G, IconButton, PopupPanel, glyph_icon

FOCUS_MINUTES = (15, 25, 45, 60)
OUTPUT_ROWS = 4
TILE_NEEDS = {"bluetooth": "bluetooth", "wifi": "wifi"}


class QuickPanel(PopupPanel):
    def __init__(self, ctx, theme_name: str):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(330)
        header = QHBoxLayout()
        header.addWidget(QLabel("Schnelleinstellungen", objectName="PanelTitle"))
        header.addStretch(1)
        settings = IconButton(G.SETTINGS, "UI-Einstellungen")
        settings.clicked.connect(lambda: (self.close(), ctx.open_settings()))
        header.addWidget(settings)
        self.body.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(6)
        self.tiles: dict[str, tuple[QToolButton, object]] = {}
        definitions = (
            ("dark", G.DARK, "Dark Mode", lambda: ctx.theme.mode == "dark",
             lambda on: ctx.set_dark_mode("dark" if on else "light")),
            ("quiet", G.QUIET, "Nicht stören", qs.do_not_disturb, qs.set_do_not_disturb),
            ("toasts", G.BELL, "Mitteilungen", qs.notifications_enabled, qs.set_notifications_enabled),
            ("bluetooth", G.BLUETOOTH, "Bluetooth", lambda: bool(ctx.status.state["bluetooth"]),
             ctx.status.set_bluetooth),
            ("wifi", G.WIFI, "WLAN", lambda: bool(ctx.status.state["wifi"]), ctx.status.set_wifi),
            ("widgets", G.WIDGETS, "Widgets", lambda: ctx.config.get("widgets_visible"), ctx.widgets.set_visible),
            ("animations", G.ANIMATION, "Animationen", lambda: ctx.config.get("animations.enabled"),
             lambda on: ctx.config.set("animations.enabled", on)),
            ("nova", G.NOVA, "Nova", lambda: ctx.nova.running, lambda _on: ctx.nova.toggle()),
        )
        for index, (key, glyph, label, read, write) in enumerate(definitions):
            tile = QToolButton(self.panel, objectName="QuickTile")  # mit Eltern: nie kurz ein eigenes Fenster
            tile.setCheckable(True)
            tile.setText(label)
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setIconSize(QSize(20, 20))
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tile.clicked.connect(lambda checked, w=write: w(checked))
            self.tiles[key] = (tile, read, glyph)
        self.grid = grid
        self._place_tiles()
        self.body.addLayout(grid)

        self.outputs_title = QLabel("Audiogeräte", objectName="PanelTitle")
        self.outputs = AudioDevices(OUTPUT_ROWS)  # Leiste: Ausgabe | Eingabe
        self.outputs.failed.connect(self.outputs_title.setText)
        self.body.addWidget(self.outputs_title)
        self.body.addWidget(self.outputs)

        self.focus_title = QLabel("Fokus-Timer", objectName="PanelTitle")
        self.focus_hint = QLabel("Stellt Benachrichtigungen für die gewählte Zeit stumm.", objectName="Muted")
        self.focus_hint.setWordWrap(True)
        self.body.addWidget(self.focus_title)
        self.body.addWidget(self.focus_hint)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.focus_buttons = []
        for minutes in FOCUS_MINUTES:
            button = QPushButton(f"{minutes} Min", objectName="Secondary")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, m=minutes: ctx.focus.start(m))
            row.addWidget(button)
            self.focus_buttons.append(button)
        self.focus_stop = QPushButton("Beenden", objectName="Danger")
        self.focus_stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_stop.clicked.connect(ctx.focus.stop)
        row.addWidget(self.focus_stop)
        self.body.addLayout(row)

        links = QHBoxLayout()
        sleep = QPushButton("PC schlafen legen", objectName="Secondary")
        sleep.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sleep.clicked.connect(lambda: (self.close(), win_api.sleep_system()))
        widgets = QPushButton("Widget-Einstellungen", objectName="Secondary")
        widgets.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widgets.clicked.connect(lambda: (self.close(), ctx.open_widget_settings()))
        links.addWidget(sleep)
        links.addWidget(widgets)
        self.body.addLayout(links)

        ctx.focus.changed.connect(self.refresh_focus)
        ctx.status.updated.connect(lambda _s: self.isVisible() and self.refresh())

    def _place_tiles(self) -> None:
        """Ohne Lücken: Fehlt Bluetooth, rückt der Rest nach."""
        shown = [tile for key, (tile, _read, _glyph) in self.tiles.items()
                 if (key not in TILE_NEEDS or self.ctx.has_feature(TILE_NEEDS[key])) and (key != "nova" or self.ctx.nova_enabled())]
        for tile, _read, _glyph in self.tiles.values():
            self.grid.removeWidget(tile)
            if tile not in shown:
                tile.hide()
        for index, tile in enumerate(shown):
            self.grid.addWidget(tile, index // 3, index % 3)
            tile.show()  # erst einsortieren, dann zeigen

    def show_at(self, anchor) -> None:
        self.outputs_title.setText("Audiogeräte")
        self.outputs.refresh()
        super().show_at(anchor)

    def refresh(self) -> None:
        dark = self.ctx.theme.mode == "dark"
        color = "#FFFFFF" if dark else "#202020"
        # Aktive Kacheln haben je nach Stil einen hellen oder dunklen Hintergrund.
        active = self.meta()["quick_on"] if dark else "#FFFFFF"
        for tile, read, glyph in self.tiles.values():
            icon = glyph_icon(glyph, 20, color)
            icon.addPixmap(glyph_icon(glyph, 20, active).pixmap(QSize(20, 20)), QIcon.Mode.Normal, QIcon.State.On)
            tile.setIcon(icon)
            tile.setChecked(bool(read()))
        self._place_tiles()
        self.tiles["bluetooth"][0].setEnabled(self.ctx.status.state["bluetooth"] is not None)
        self.tiles["wifi"][0].setEnabled(self.ctx.status.state["wifi"] is not None)
        self.refresh_focus()

    def refresh_focus(self) -> None:
        focus = self.ctx.focus
        for button in self.focus_buttons:
            button.setVisible(not focus.active)
        self.focus_stop.setVisible(focus.active)
        self.focus_hint.setVisible(not focus.active)
        remaining = f": noch {qs.format_remaining(focus.remaining)}" if focus.active else ""
        self.focus_title.setText("Fokus-Timer" + remaining)
