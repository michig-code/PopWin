"""Einstellungen: alle Anpassungen plus interaktive Setup-Seite. Änderungen gelten sofort."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from config import config_manager
from config.config_manager import MONITOR_DEFAULTS
from core import hardware, i18n, wallpapers, win_api, nova_brain  # [Antigravity]
from core.ai_actions import settings_catalog
from core.gemini_client import PlanWorker
from core.monitor_manager import native_rect, screen_key
from core.nova_controller import NOVA_KEY
from core.nova_voice import VOICES
from core.paths import DATA_DIR, app_shortcut
from core.process_trim import CANDIDATES
from core.replacements import REPLACEMENTS
from core.theme_manager import NOVA_CLASSICS
from ui.theme_page import ThemePage
from extensions import USER_FOLDER
from ui import desktop_groups
from ui.audio_mixer import AudioMixer
from ui.stat_pages import CachePanel, NetworkStats, PerformanceStats
from ui.audio_outputs import AudioOutputs
from ui.base import G, ScrollFields, glyph_icon, resettable
from core.dev import dev_menu_wanted, is_dev, open_dev_menu
from core.updater import NO_RELEASE
from core.version import REPO, VERSION
from ui.clock_place import PLACES, PlacePicker
from ui.dock_tools import STACK_ORDERS, TOOL_ITEMS, TOOL_MODES
from ui.nova_artifacts_view import ConnectionForm
from ui.column_layout import ColumnLayout
from ui.file_dialog import pick_files
from ui.monitor_map import MonitorEntry, MonitorMap, MonitorNumbers
from ui.pin_editor import PinEditor, WindowsPinEditor
from ui.widgets import WIDGET_LABELS
from ui.window_frame import NativeWindow, fade_in_page

ROOT = Path(__file__).resolve().parent.parent
DEV_HIT = -1  # Suchtreffer, der kein Tab ist, sondern das Dev-Menü
EDGE_LABELS = {1: "Stufe 1: nur ganz am Rand, mit Verzögerung", 2: "Stufe 2: am Rand, etwas verzögert", 3: "Stufe 3: normal",
               4: "Stufe 4: schon vor dem Rand, schnell", 5: "Stufe 5: weit vor dem Rand, sofort"}


class Page(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget(objectName="SettingsPage")
        self.box = ColumnLayout(inner)
        self.box.setContentsMargins(16, 14, 16, 14)
        self._last_form: QFormLayout | None = None
        self.setWidget(inner)
        inner.installEventFilter(self)

    def _fit_height(self) -> None:
        """Die Blöcke brauchen je nach Breite verschieden viel Höhe; das Scrollfeld fragt das von selbst nicht ab.
        Ohne diese Vorgabe würde beim Ändern der Fenstergröße Inhalt abgeschnitten."""
        self.widget().setMinimumHeight(self.box.totalHeightForWidth(self.viewport().width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:
            self._fit_height()
        return super().eventFilter(watched, event)

    def group(self, title: str, full: bool = False) -> QFormLayout:
        """Ein Block; mit full geht er über die ganze Breite statt in eine Spalte."""
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        if full:
            self.box.add_full(box)
        else:
            self.box.addWidget(box)
        self._last_form = form
        return form

    def note(self, value: str) -> None:
        """Hinweis zum letzten Block; er bleibt bei ihm, auch wenn die Blöcke in Spalten stehen."""
        if self._last_form is None:
            self.box.add_full(self.text(value))
        else:
            self._last_form.addRow(self.text(value))

    def text(self, value: str) -> QLabel:
        label = QLabel(value, objectName="Muted")
        label.setWordWrap(True)
        return label


PAGE_ROLE = Qt.ItemDataRole.UserRole + 1  # welche Seite ein Eintrag der Navigation öffnet; Überschriften haben keine

# Neues Einstellungsmenü (Beta): Seiten nach Themen gruppiert, mit Überschriften in der Navigation
NAV_GROUPS = (
    ("Start", ("Übersicht",)),
    ("Aussehen", ("Themes", "Hintergrund", "Uhr", "Widgets")),
    ("Leisten und Menüs", ("Topbar", "Taskbar", "Startmenü", "Rechtsklick-Menü")),
    ("System", ("Benachrichtigungen", "Audio", "Netzwerk", "Leistung", "Datenschutz")),
    ("KI und Suche", ("Nova", "Suche", "Suche mit KI")),
    ("PopWin", ("Allgemein", "Setup", "Teilen & Presets", "Erweiterungen", "Beta")),
)


def grouped_nav(labels: list[str], groups=NAV_GROUPS) -> list[tuple[str, bool]]:
    """Reihenfolge der Navigation: (Text, ist Überschrift). Seiten ohne Gruppe landen am Ende unter der letzten."""
    known = {label for _title, members in groups for label in members}
    rows: list[tuple[str, bool]] = []
    for number, (title, members) in enumerate(groups):
        present = [label for label in members if label in labels]
        if number == len(groups) - 1:
            present += [label for label in labels if label not in known]
        if present:
            rows.append((title, True))
            rows += [(label, False) for label in present]
    return rows


class PageStack(QStackedWidget):
    """Seiten mit Navigation an der Seite; kennt die Namen für Suche und direkte Sprünge."""

    def __init__(self, nav: QListWidget, window: "SettingsWindow"):
        super().__init__()
        self.nav = nav
        self.window_ref = window
        self._order: list[QListWidgetItem] = []  # Einträge in der klassischen Reihenfolge
        nav.currentRowChanged.connect(self._show_row)

    def addTab(self, page: QWidget, label: str, glyph: str = "") -> int:
        index = self.addWidget(page)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, glyph)
        item.setData(PAGE_ROLE, index)
        self.nav.addItem(item)
        self._order.append(item)
        return index

    def _item(self, index: int) -> QListWidgetItem | None:
        return next((item for item in self._order if item.data(PAGE_ROLE) == index), None)

    def _row(self, index: int) -> int:
        for row in range(self.nav.count()):
            if self.nav.item(row).data(PAGE_ROLE) == index:
                return row
        return -1

    def tabText(self, index: int) -> str:
        item = self._item(index)
        return item.text() if item else ""

    def setCurrentIndex(self, index: int) -> None:
        self.nav.setCurrentRow(self._row(index))

    def setCurrentWidget(self, page: QWidget) -> None:
        self.setCurrentIndex(self.indexOf(page))

    def set_visible_tab(self, index: int, visible: bool) -> None:
        item = self._item(index)
        if item is not None:
            item.setHidden(not visible)

    def arrange(self, grouped: bool) -> None:
        """Klassisch: alle Seiten untereinander. Neu: nach Themen mit Überschriften."""
        current = self.currentIndex()
        self.nav.blockSignals(True)
        while self.nav.count():
            self.nav.takeItem(0)
        if grouped:
            by_label = {item.text(): item for item in self._order}
            for text, header in grouped_nav(list(by_label)):
                if header:
                    head = QListWidgetItem(text.upper())
                    head.setFlags(Qt.ItemFlag.NoItemFlags)
                    font = head.font()
                    font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
                    font.setBold(True)
                    head.setFont(font)
                    self.nav.addItem(head)
                else:
                    self.nav.addItem(by_label[text])
        else:
            for item in self._order:
                self.nav.addItem(item)
        self.nav.blockSignals(False)
        self.setCurrentIndex(current)

    def _show_row(self, row: int) -> None:
        item = self.nav.item(row) if row >= 0 else None
        index = item.data(PAGE_ROLE) if item is not None else None
        if index is not None:
            super().setCurrentIndex(index)
            fade_in_page(self.widget(index).viewport(), self.window_ref.page_fade())


class SettingsWindow(NativeWindow):
    def __init__(self, ctx):
        super().__init__(ctx, "PopWin Einstellungen", "apps/settings")
        self.resize(940, 680)
        self.search = QLineEdit(objectName="TitleSearch")
        self.search.setPlaceholderText("Einstellung suchen oder ? für KI")
        self.search.setMaximumWidth(420)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._search_accept)
        self.titlebar.middle.addStretch(1)
        self.titlebar.middle.addWidget(self.search, 3)
        self.titlebar.middle.addStretch(1)

        row = QHBoxLayout(self.content)
        row.setContentsMargins(10, 4, 0, 0)
        row.setSpacing(6)
        nav = QListWidget(objectName="SettingsNav")
        nav.setFixedWidth(190)
        nav.setIconSize(QSize(18, 18))
        nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs = PageStack(nav, self)
        row.addWidget(nav)
        row.addWidget(self.tabs, 1)
        self._taskbar_monitor = ""
        self._topbar_monitor = ""
        self._clock_monitor = ""
        self.overview_tab = self.tabs.addTab(self._overview_page(), "Übersicht", G.WINDOWS)
        self.tabs.addTab(self._themes_page(), "Themes", G.PERSONALIZE)
        self.tabs.addTab(self._design_page(), "Allgemein", G.SETTINGS)
        self.tabs.addTab(self._topbar_page(), "Topbar", G.DISPLAY)
        self.tabs.addTab(self._bars_page(), "Taskbar", G.APP)
        self.tabs.addTab(self._clock_page(), "Uhr", G.CLOCK)
        self.tabs.addTab(self._notifications_page(), "Benachrichtigungen", G.BELL)
        self.tabs.addTab(self._audio_page(), "Audio", G.VOLUME)
        self.tabs.addTab(self._wallpaper_page(), "Hintergrund", G.PERSONALIZE)
        self.tabs.addTab(self._menu_page(), "Rechtsklick-Menü", G.LIST)
        self.tabs.addTab(self._launcher_page(), "Suche", G.SEARCH)
        self.tabs.addTab(self._ai_page(), "Suche mit KI", G.QUICK)
        self.tabs.addTab(self._start_page(), "Startmenü", G.START)
        self.widget_page = Page()
        self.tabs.addTab(self.widget_page, "Widgets", G.WIDGETS)
        self.tabs.addTab(self._nova_page(), "Nova", G.NOVA)
        self.tabs.addTab(self._network_page(), "Netzwerk", G.ETHERNET)
        self.tabs.addTab(self._performance_page(), "Leistung", G.QUICK)
        self.tabs.addTab(self._privacy_page(), "Datenschutz", G.LOCK)
        self.tabs.addTab(self._beta_page(), "Beta", G.QUICK)
        self.extensions_page = Page()
        self.tabs.addTab(self.extensions_page, "Erweiterungen", G.ADD)
        self._fill_extensions()
        self.tabs.addTab(self._share_page(), "Teilen & Presets", G.SEND)
        self.setup_tab = self.tabs.addTab(self._setup_page(), "Setup", G.SETTINGS)
        self._apply_look()
        ctx.config.changed.connect(lambda key: key == "settings_v2" and self._apply_look())

        self.hits = QListWidget(self, objectName="SearchHits")
        self.hits.setFixedWidth(420)
        self.hits.hide()
        self.hits.itemClicked.connect(self._open_hit)
        self.search.installEventFilter(self)
        self._worker = None
        ScrollFields.install()
        self._monitor_key = ""
        self._monitor_numbers = MonitorNumbers()
        self._fill_monitors()
        self._fill_widgets()
        ctx.monitors.screens_changed.connect(self._fill_monitors)

    def mouse_step(self, step: int) -> None:
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())

    def page_fade(self) -> int:
        return 140 if self.ctx.config.get("animations.enabled") else 0

    def open_page(self, setup: bool = False) -> None:
        if setup:
            self.tabs.setCurrentIndex(self.setup_tab)
        self._fill_monitors()
        self._fill_widgets()
        self._refresh_setup()
        self.apply_theme()
        self.showNormal() if self.isMinimized() else self.show()
        win_api.bring_to_front(int(self.winId()))
        self.raise_()
        self.activateWindow()

    def open_named(self, label: str) -> None:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) in (label, i18n.translate(label)):
                self.tabs.setCurrentIndex(index)
        self.open_page()

    def apply_theme(self) -> None:
        super().apply_theme()
        color = "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"
        for row in range(self.tabs.nav.count()):
            item = self.tabs.nav.item(row)
            if item.data(Qt.ItemDataRole.UserRole):  # Überschriften haben kein Symbol
                item.setIcon(glyph_icon(item.data(Qt.ItemDataRole.UserRole), 18, color))

    # --- Suche in den Einstellungen ------------------------------------------------
    def eventFilter(self, watched, event) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.search.clear()
            return True
        return super().eventFilter(watched, event)

    def _matches(self, text: str) -> list[tuple[str, int, QWidget | None]]:
        needle = text.lower()
        found: list[tuple[str, int, QWidget | None]] = []
        dev = dev_menu_wanted(text)
        if dev:  # nur im Dev-Build; geöffnet wird erst nach der PIN, wie über den Knopf im Setup
            found.append(("Dev-Menü öffnen", DEV_HIT, None))
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            for widget in page.findChildren(QLabel) + page.findChildren(QCheckBox) + page.findChildren(QGroupBox):
                label = widget.title() if isinstance(widget, QGroupBox) else widget.text()
                tools = dev and isinstance(widget, QGroupBox) and "Symbolleiste über der Taskbar" in label
                if (tools or needle in label.lower()) and len(label) < 60 and "<" not in label:
                    found.append((f"{self.tabs.tabText(index)} › {label.rstrip(':')}", index, widget))
        return found[:9]

    def _search_changed(self, text: str) -> None:
        query = text.strip()
        self.hits.clear()
        if len(query) < 2 or query.startswith("?"):
            self.hits.hide()
            return
        for label, index, widget in self._matches(query):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (index, widget))
            self.hits.addItem(item)
        if not self.hits.count():
            self.hits.hide()
            return
        self.hits.setCurrentRow(0)
        corner = self.search.mapTo(self, QPoint(0, self.search.height() + 4))
        self.hits.setFixedHeight(min(self.hits.count(), 9) * 32 + 8)
        self.hits.move(corner)
        self.hits.show()
        self.hits.raise_()

    def _open_hit(self, item: QListWidgetItem) -> None:
        index, widget = item.data(Qt.ItemDataRole.UserRole)
        if index == DEV_HIT:
            self.search.clear()
            self._open_dev_menu()
            return
        self.tabs.setCurrentIndex(index)
        self.tabs.widget(index).ensureWidgetVisible(widget)
        self.search.clear()

    def _search_accept(self) -> None:
        query = self.search.text().strip()
        if self.hits.isVisible() and self.hits.currentItem():
            self._open_hit(self.hits.currentItem())
        elif query:
            self._ask_ai(query.lstrip("?").strip())

    def _ask_ai(self, prompt: str) -> None:
        key = self.ctx.config.api_key()
        if not key or not prompt:
            self.search.clear()
            self.search.setPlaceholderText("Für die KI braucht es einen API-Key (Setup)")
            return
        self.search.clear()
        self.search.setPlaceholderText("Denke nach …")
        request = f"Ändere PopWin-Einstellungen, nur mit kind=setting: {prompt}"
        self._worker = PlanWorker(0, key, self.ctx.config.models("search"), request, settings_catalog(self.ctx), self)
        self._worker.planned.connect(self._on_plan)
        self._worker.failed.connect(lambda _rid, message: self.search.setPlaceholderText(message))
        self._worker.start()

    def _on_plan(self, _request: int, plan: dict, _model: str) -> None:
        self.search.setPlaceholderText(plan["answer"][:80] or "Einstellung suchen oder ? für KI")
        if plan["tasks"]:
            self.ctx.tasks.propose(plan["tasks"])

    # --- Bausteine, die direkt an die Konfiguration gebunden sind --------------
    def _check(self, path: str, label: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setChecked(bool(self.ctx.config.get(path)))
        box.toggled.connect(lambda value: self.ctx.config.set(path, value))
        return box

    _resettable = staticmethod(resettable)

    def _spin(self, path: str, low: int, high: int, suffix: str = "") -> QWidget:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setValue(self.ctx.config.get(path))
        spin.valueChanged.connect(lambda value: self.ctx.config.set(path, value))
        return self._resettable(spin, lambda: spin.setValue(self.ctx.config.default_of(path)))

    def _combo(self, path: str, options: dict[str, str]) -> QWidget:
        combo = QComboBox()
        for value, label in options.items():
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(self.ctx.config.get(path))))
        combo.currentIndexChanged.connect(lambda _i: self.ctx.config.set(path, combo.currentData()))
        return self._resettable(combo, lambda: combo.setCurrentIndex(
            max(0, combo.findData(self.ctx.config.default_of(path)))))

    def _line(self, path: str) -> QWidget:
        line = QLineEdit(self.ctx.config.get(path))
        line.editingFinished.connect(lambda: self.ctx.config.set(path, line.text().strip()))

        def reset() -> None:
            line.setText(self.ctx.config.default_of(path))
            self.ctx.config.set(path, line.text())

        return self._resettable(line, reset)

    def _opacity(self, path: str) -> QWidget:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(40, 100)
        slider.setValue(round(self.ctx.config.get(path) * 100))
        slider.valueChanged.connect(lambda value: self.ctx.config.set(path, value / 100))
        return self._resettable(slider, lambda: slider.setValue(round(self.ctx.config.default_of(path) * 100)))

    # --- Seiten -------------------------------------------------------------
    def _design_page(self) -> Page:
        page = Page()
        form = page.group("Sprache und Region")
        form.addRow("Sprache", self._combo("language", {i18n.AUTO: "Automatisch (wie Windows)", **i18n.LANGUAGES}))
        page.note("Gilt nach dem Neustart von PopWin.")
        form = page.group("Animationen")
        form.addRow(self._check("animations.enabled", "Weiche Animationen beim Starten, Öffnen und Schließen"))
        form.addRow("Dauer", self._spin("animations.duration", 80, 600, " ms"))
        return page

    def _themes_page(self) -> Page:
        """Eigene Kategorie: Galerie, Hell/Dunkel, Kacheln und der Editor für eigene Themes."""
        page = Page()
        self.theme_page = ThemePage(self, page)
        return page

    def _fill_monitors(self) -> None:
        """Eine eigene Monitor-Seite gibt es nicht mehr: Topbar und Taskbar haben je ihre Bildschirm-Karte."""
        self._fill_topbar_monitors()
        self._fill_taskbar_monitors()
        self._fill_clock_monitors()

    def _screen_group(self, layout: ColumnLayout, selected: MonitorEntry, bar: str) -> None:
        """UI-Stil, Position und auf welchen Bildschirmen die Leiste steht - früher auf der Seite „Monitore“."""
        form = self._option_group(layout, selected, "Bildschirm")
        _check, combo, _spin = self._monitor_controls(form, selected.key)
        combo("theme", "Theme", {"": "Wie das Haupt-Theme", **self.ctx.theme.all_themes()})
        if bar == "topbar":
            combo("topbar_position", "Topbar-Position", {"top": "Oben", "bottom": "Unten"})
        if not self.ctx.config.synced:
            copy = QPushButton("Auf alle Bildschirme übertragen", objectName="Secondary")
            copy.clicked.connect(lambda: self._copy_to_all(selected.key))
            form.addRow(copy)
        box = QGroupBox(("Topbar" if bar == "topbar" else "Taskbar") + " anzeigen auf")
        where = QFormLayout(box)
        layout.addWidget(box)
        self._bar_choice(where, bar)

    def _monitor_entries(self) -> list[MonitorEntry]:
        numbers = win_api.monitor_numbers()
        entries = []
        for index, screen in enumerate(self.ctx.monitors.screens()):
            rect = native_rect(screen)
            entries.append(MonitorEntry(screen_key(screen), numbers.get(rect, index + 1), screen.name(), rect,
                                        self.ctx.monitors.is_primary(screen)))
        return sorted(entries, key=lambda entry: entry.number)

    def _fill_taskbar_monitors(self) -> None:
        """Karte wie auf der Monitor-Seite; darunter die angehefteten Apps des gewählten Bildschirms."""
        layout = self.taskbar_monitors.layout()
        selected = self._monitor_picker(layout, self._taskbar_monitor, self._pick_taskbar_monitor)
        if selected is None:
            return
        self._taskbar_monitor = selected.key
        self._screen_group(layout, selected, "taskbar")
        self._designer_group(layout, selected, "Knöpfe der Taskbar", self._dock_designer(selected.key))
        synced = self.ctx.config.get("taskbar.sync_icons")
        form = self._option_group(layout, selected, "Angeheftet" + (" auf allen Taskbars" if synced else ""))
        form.addRow(PinEditor(self.ctx, "taskbar.pinned") if selected.primary or synced
                    else PinEditor(self.ctx, "taskbar.screen_pins", selected.key))
        self._taskbar_options(layout, selected)

    def _monitor_picker(self, layout: ColumnLayout, current: str, picked) -> MonitorEntry | None:
        """Leert das Layout und legt Karte und Identifizieren hinein; gibt den gewählten Bildschirm zurück."""
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        entries = self._monitor_entries()
        if not entries:
            return None
        selected = next((entry for entry in entries if entry.key == current), None)
        selected = selected or next((entry for entry in entries if entry.primary), entries[0])
        monitor_map = MonitorMap(entries, "" if self.ctx.config.synced else selected.key)
        monitor_map.picked.connect(picked)
        identify = QPushButton("Bildschirme identifizieren", objectName="Secondary")
        identify.clicked.connect(lambda: self._identify_monitors(self._monitor_entries()))
        layout.add_full(monitor_map)
        layout.add_full(identify)
        layout.add_full(self._sync_switch())
        return selected

    def _sync_switch(self) -> QCheckBox:
        box = QCheckBox("Alle Bildschirme gleichzeitig einstellen")
        box.setChecked(self.ctx.config.synced)
        box.toggled.connect(lambda on: QTimer.singleShot(0, lambda: self._switch_mode("sync" if on else "separate")))
        return box

    def _refresh_monitor_pages(self) -> None:
        """Baut die drei Seiten neu auf; die Scroll-Position bleibt, nur die Einstellungen darunter wechseln."""
        for page, fill in ((self.taskbar_page, self._fill_taskbar_monitors),
                           (self.topbar_page, self._fill_topbar_monitors), (self.clock_page, self._fill_clock_monitors)):
            bar = page.verticalScrollBar()
            position = bar.value()
            fill()
            QTimer.singleShot(0, lambda b=bar, p=position: b.setValue(p))

    def _use_monitor(self, field: str, key: str) -> None:
        setattr(self, field, key)
        if self.ctx.config.synced:  # ein einzelner Bildschirm lässt sich nur einzeln einstellen
            self.ctx.config.set_monitor_mode("separate")
        # Verzögert: Die angeklickte Karte wird beim Neuaufbau ersetzt.
        QTimer.singleShot(0, self._refresh_monitor_pages)

    def _designer_group(self, layout: ColumnLayout, selected: MonitorEntry, title: str, designer: QWidget) -> None:
        """Leisten-Designer über die ganze Breite, wie der Uhr-Platz-Wähler."""
        scope = "alle Bildschirme" if self.ctx.config.synced else f"Bildschirm {selected.number}"
        box = QGroupBox(f"{title} · {scope}")
        inner = QVBoxLayout(box)
        inner.addWidget(designer)
        layout.add_full(box)

    def _top_designer(self, key: str) -> QWidget:
        from ui.bar_designer import BarDesigner, Item
        from ui.topbar import TOP_ITEMS, TOP_NEEDS, complete_order
        state = self.ctx.config.monitor(key)
        order = complete_order(state["top_order"])
        items = [Item(name, TOP_ITEMS[name][1], TOP_ITEMS[name][2], bool(state[TOP_ITEMS[name][0]])) for name in order
                 if name not in TOP_NEEDS or self.ctx.has_feature(TOP_NEEDS[name])]
        designer = BarDesigner(items, self.ctx.theme.mode == "dark")
        designer.toggled.connect(lambda name, on: self.ctx.config.set_monitor(key, TOP_ITEMS[name][0], on))

        def store(keys: list[str]) -> None:
            old = complete_order(self.ctx.config.monitor(key)["top_order"])
            self.ctx.config.set_monitor(key, "top_order", keys + [name for name in old if name not in keys])
        designer.reordered.connect(store)
        self.top_designer = designer
        return designer

    def _dock_designer(self, key: str) -> QWidget:
        from ui.bar_designer import BarDesigner, Item
        from ui.taskbar import dock_order_of
        state = self.ctx.config.monitor(key)
        fields = {"start": ("show_start", "Home-Knopf", G.START), "search": ("show_search", "Suche", G.SEARCH),
                  "media": ("show_media", "Player", G.MUSIC), "nova": ("show_nova_button", "Nova", G.NOVA),
                  "trash": ("show_recycle", "Papierkorb", "\uE74D"), "desktop": ("show_desktop", "Desktop anzeigen", G.DISPLAY)}
        items = []
        for name in dock_order_of(state.get("dock_order")):
            if name == "apps":
                items.append(Item("apps", "Deine Apps", "", True, fixed=True, wide=3))
            elif name != "nova" or self.ctx.nova_enabled():
                field, label, glyph = fields[name]
                items.append(Item(name, label, glyph, bool(state.get(field, True))))
        designer = BarDesigner(items, self.ctx.theme.mode == "dark")
        designer.toggled.connect(lambda name, on: self.ctx.config.set_monitor(key, fields[name][0], on))

        def store(keys: list[str]) -> None:
            old = dock_order_of(self.ctx.config.monitor(key).get("dock_order"))
            self.ctx.config.set_monitor(key, "dock_order", dock_order_of(keys + [name for name in old if name not in keys]))
        designer.reordered.connect(store)
        self.dock_designer = designer
        return designer

    def _option_group(self, layout: ColumnLayout, selected: MonitorEntry, title: str):
        scope = "alle Bildschirme" if self.ctx.config.synced else f"Bildschirm {selected.number}"
        box = QGroupBox(f"{title} · {scope}")
        form = QFormLayout(box)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        layout.addWidget(box)
        return form

    def _topbar_page(self) -> Page:
        page = self.topbar_page = Page()
        self._hardware_group(page)
        self.topbar_monitors = QWidget()
        ColumnLayout(self.topbar_monitors)
        page.box.add_full(self.topbar_monitors)
        self._fill_topbar_monitors()
        return page

    def _fill_topbar_monitors(self) -> None:
        layout = self.topbar_monitors.layout()
        selected = self._monitor_picker(layout, self._topbar_monitor, self._pick_topbar_monitor)
        if selected is None:
            return
        self._topbar_monitor = selected.key
        self._screen_group(layout, selected, "topbar")
        self._designer_group(layout, selected, "Symbole rechts", self._top_designer(selected.key))
        middle = self._option_group(layout, selected, "Links und Mitte")
        check, combo, spin = self._monitor_controls(middle, selected.key)
        check("show_user", "Benutzername anzeigen")
        check("show_windows", "Menü am Benutzernamen")
        combo("user_click", "Klick auf den Benutzernamen", {
            "account": "Benutzer-Bereich (Konten, Sperren, Abmelden)", "windows": "Offene Fenster"})
        check("show_workspaces", "Arbeitsflächen anzeigen (ab zwei Desktops)")
        check("topbar_auto_hide", "Nur zeigen, wenn die Maus an den Rand kommt")
        combo("edge_level", "Empfindlichkeit am Rand", EDGE_LABELS)
        combo("center", "Mitte", {"title": "Aktiver Fenstertitel", "none": "Leer"})
        middle.addRow(QLabel("Steht die Uhr oben in der Mitte, hat sie dort Vorrang. Den Platz der Uhr wählst du im Tab „Uhr“.",
                           objectName="Muted", wordWrap=True))
        spin("topbar_height", "Höhe", 0, 72, "Wie im Theme")

    def _pick_topbar_monitor(self, key: str) -> None:
        self._use_monitor("_topbar_monitor", key)

    def _dock_tools_group(self, layout: ColumnLayout, selected: MonitorEntry) -> None:
        """Beta: Apps unten, Knöpfe in einer eigenen Leiste darüber."""
        form = self._option_group(layout, selected, "Beta · Symbolleiste über der Taskbar")
        _check, combo, _spin = self._monitor_controls(form, selected.key)
        combo("dock_tools", "Leiste über der Taskbar", TOOL_MODES)
        combo("dock_stack_order", "Mit der Uhr über der Mitte", STACK_ORDERS)
        config = self.ctx.config
        chosen = list(config.monitor(selected.key)["dock_tools_items"])
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        for item, label in TOOL_ITEMS.items():
            tick = QCheckBox(label)
            tick.setChecked(item in chosen)

            def toggled(on: bool, item=item) -> None:
                items = [i for i in config.monitor(selected.key)["dock_tools_items"] if i != item] + ([item] if on else [])
                config.set_monitor(selected.key, "dock_tools_items", items)

            tick.toggled.connect(toggled)
            row.addWidget(tick)
        row.addStretch(1)
        form.addRow("Darin", box)
        form.addRow(QLabel("Unten bleiben die Apps. Was hier abgehakt ist, steht in der Leiste darüber.", objectName="Muted",
                           wordWrap=True))

    def _search_updates(self) -> None:
        self.update_state.setText("Suche …")
        updates = self.ctx.updates
        try:
            updates.finished.disconnect(self._show_update_state)
        except (RuntimeError, TypeError):
            pass
        updates.finished.connect(self._show_update_state)
        updates.check(manual=True)

    def _show_update_state(self, result: str) -> None:
        self.update_state.setText({"neu": "Eine neue Version ist da.", "aktuell": "PopWin ist aktuell.",
                                   NO_RELEASE: "Kein Update gefunden."}.get(result, result))

    def _open_dev_menu(self) -> None:
        open_dev_menu(self.ctx)

    def _clock_page(self) -> Page:
        page = self.clock_page = Page()
        self.clock_monitors = QWidget()
        ColumnLayout(self.clock_monitors)
        page.box.add_full(self.clock_monitors)
        self._fill_clock_monitors()
        return page

    def _fill_clock_monitors(self) -> None:
        """Ein Platz pro Bildschirm: Die Uhrzeit soll man nie zweimal lesen."""
        layout = self.clock_monitors.layout()
        selected = self._monitor_picker(layout, self._clock_monitor, lambda key: self._use_monitor("_clock_monitor", key))
        if selected is None:
            return
        self._clock_monitor = selected.key
        config = self.ctx.config
        form = self._option_group(layout, selected, "Wo steht die Uhr?")
        profile = config.monitor(selected.key)
        picker = PlacePicker(profile["clock_place"], stack_order=profile["dock_stack_order"],
                             show_stack=profile["dock_tools"] != "off")
        form.addRow(picker)
        place = QComboBox()
        for value, text in PLACES.items():
            place.addItem(text, value)
        place.setCurrentIndex(max(0, place.findData(picker.current)))
        order = QComboBox()
        for value, text in STACK_ORDERS.items():
            order.addItem(text, value)
        order.setCurrentIndex(max(0, order.findData(picker.stack_order)))

        def choose(value: str) -> None:
            picker.current = value
            picker.update()
            place.blockSignals(True)
            place.setCurrentIndex(max(0, place.findData(value)))
            place.blockSignals(False)
            order.setEnabled(value == "dock_center")
            config.set_monitor(selected.key, "clock_place", value)

        def stack(value: str) -> None:
            picker.stack_order = value
            picker.update()
            order.blockSignals(True)
            order.setCurrentIndex(max(0, order.findData(value)))
            order.blockSignals(False)
            config.set_monitor(selected.key, "dock_stack_order", value)

        picker.picked.connect(choose)
        picker.stack_changed.connect(stack)
        place.currentIndexChanged.connect(lambda _i: choose(place.currentData()))
        order.currentIndexChanged.connect(lambda _i: stack(order.currentData()))
        order.setEnabled(picker.current == "dock_center")
        form.addRow("Platz", place)
        form.addRow("Mit der Symbolleiste", order)
        form.addRow(QLabel("Klick auf einen Platz im Bild. Uhrzeit und Datum stehen dann nur noch dort.", objectName="Muted",
                           wordWrap=True))
        check, combo, _spin = self._monitor_controls(self._option_group(layout, selected, "Aussehen"), selected.key)
        combo("clock_style", "Inhalt", {"both": "Uhrzeit und Datum", "time": "Nur Uhrzeit", "date": "Nur Datum"})
        combo("clock_lines", "Anordnung", {"two": "Zwei Zeilen", "one": "Eine Zeile"})
        check("clock_seconds", "Sekunden anzeigen")

    def _taskbar_options(self, layout: ColumnLayout, selected: MonitorEntry) -> None:
        def group(title: str):
            return self._monitor_controls(self._option_group(layout, selected, title), selected.key)

        self._dock_tools_group(layout, selected)
        if is_dev():


            # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
            dev_form = self._option_group(layout, selected, "Dev · Claude-Knopf")
            dev_check, _combo, _spin = self._monitor_controls(dev_form, selected.key)
            dev_check("dev_claude_button", "Claude-Knopf neben dem Home-Knopf")
            # --- [Antigravity Ende: Sidebar Support] ---

        check, combo, spin = group("Elemente")
        if is_dev():  # [Antigravity: Claude-Knopf umschaltbar wie andere Elemente]
            check("dev_claude_button", "Claude-Knopf anzeigen")
        check("show_start", "Home-Knopf anzeigen")
        combo("start_style", "Symbol des Home-Knopfs", {
            "home": "Haus", "windows": "Windows-Logo", "logo": "PopWin-Logo"})
        check("show_search", "Suche anzeigen")
        combo("search_style", "Aussehen der Suche", {
            "icon": "Nur Lupe", "pill": "Kleine Pille", "field": "Suchfeld", "wide": "Breites Suchfeld"})
        check("show_media", "Player anzeigen")
        combo("media_style", "Aussehen des Players", {
            "full": "Cover, Titel und Knöpfe", "compact": "Titel und Knöpfe",
            "controls": "Nur Knöpfe", "cover": "Cover und Play"})
        spin("media_width", "Breite des Players", 140, 480)
        check("show_recycle", "Papierkorb anzeigen")
        check("show_nova_button", "Nova-Knopf anzeigen")
        check("show_desktop", "Knopf „Desktop anzeigen“")

        check, combo, spin = group("Apps")
        spin("icon_size", "Icon-Größe", 16, 64)
        spin("icon_spacing", "Abstand zwischen den Icons", 0, 40)
        combo("task_indicator", "Anzeige für offene Apps", {
            "bar": "Strich", "dot": "Punkt", "none": "Keine"})
        check("task_labels", "App-Namen neben den Icons")
        check("task_counts", "Anzahl offener Fenster am Icon")
        check("task_badges", "Rote Zahl für neue Mitteilungen am Icon")

        check, combo, spin = group("Position, Größe und Verhalten")
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        from ui import sidebar
        combo("taskbar_position", "Taskbar-Position", sidebar.taskbar_positions())
        # --- [Antigravity Ende: Sidebar Support] ---
        combo("dock_style", "Breite", {"full": "Ganze Bildschirmbreite", "fit": "Nur so breit wie die Icons"})
        check("dock_floating", "Schwebendes Dock (runde Pille mit Abstand zum Rand)")
        spin("dock_gap", "Abstand des schwebenden Docks zum Rand", 0, 40)
        combo("dock_align", "Ausrichtung", {"left": "Links", "center": "Mitte", "right": "Rechts"})
        spin("dock_height", "Höhe", 0, 96, "Wie im Theme")
        check("auto_hide", "Nur zeigen, wenn die Maus an den Rand kommt")
        combo("edge_level", "Empfindlichkeit am Rand", EDGE_LABELS)
        check("reserve_space", "Platz freihalten (sonst gehen Fenster bis zum Rand)")

    def _pick_taskbar_monitor(self, key: str) -> None:
        self._use_monitor("_taskbar_monitor", key)

    def _pick_monitor(self, key: str) -> None:
        self._use_monitor("_monitor_key", key)

    def _identify_monitors(self, entries: list[MonitorEntry]) -> None:
        by_key = {screen_key(screen): screen for screen in self.ctx.monitors.screens()}
        self._monitor_numbers.show([(entry.number, by_key[entry.key].geometry())
                                    for entry in entries if entry.key in by_key])

    def _bar_group(self, page: Page, kind: str, label: str) -> None:
        self._bar_choice(page.group(f"{label} anzeigen auf"), kind)

    def _bar_choice(self, form: QFormLayout, kind: str) -> None:
        """Wählt, auf welchen Monitoren eine Leiste erscheint."""
        config = self.ctx.config
        choice = config.data["bar_screens"][kind]
        where = QComboBox()
        for value, text in (("all", "Allen Monitoren"), ("primary", "Nur dem Hauptmonitor"),
                            ("custom", "Ausgewählten Monitoren")):
            where.addItem(text, value)
        where.setCurrentIndex(where.findData(choice["mode"]))

        def on_mode(_index: int) -> None:
            mode = where.currentData()
            # Beim ersten Wechsel auf "Ausgewählt" startet die Auswahl mit dem Hauptmonitor.
            primary = [screen_key(s) for s in self.ctx.monitors.screens() if self.ctx.monitors.is_primary(s)]
            config.set_bar_screens(kind, mode, config.data["bar_screens"][kind]["screens"] or primary)
            QTimer.singleShot(0, self._fill_monitors)

        where.currentIndexChanged.connect(on_mode)
        form.addRow(where)
        if choice["mode"] != "custom":
            return
        for screen in self.ctx.monitors.screens():
            key = screen_key(screen)
            box = QCheckBox(screen.name() + (" (Hauptmonitor)" if self.ctx.monitors.is_primary(screen) else ""))
            box.setChecked(key in choice["screens"])

            def on_toggle(checked: bool, key: str = key) -> None:
                current = [k for k in config.data["bar_screens"][kind]["screens"] if k != key]
                config.set_bar_screens(kind, "custom", current + [key] if checked else current)

            box.toggled.connect(on_toggle)
            form.addRow(box)

    def _switch_mode(self, mode: str) -> None:
        self.ctx.config.set_monitor_mode(mode)
        self._refresh_monitor_pages()

    def _copy_to_all(self, key: str) -> None:
        keys = [screen_key(s) for s in self.ctx.monitors.screens()]
        self.ctx.config.copy_profile_to_all(key, keys)
        self._fill_monitors()

    def _monitor_controls(self, form: QFormLayout, key: str | None):
        """Eingaben für ein Monitor-Profil: (Haken, Auswahl, Zahl)."""
        config = self.ctx.config
        state = config.monitor(key)

        def check(field: str, label: str) -> None:
            box = QCheckBox(label)
            box.setChecked(state[field])
            box.toggled.connect(lambda value: config.set_monitor(key, field, value))
            form.addRow(box)

        def combo(field: str, label: str, options: dict[str, str]) -> None:
            box = QComboBox()
            for value, text in options.items():
                box.addItem(text, value)
            box.setCurrentIndex(max(0, box.findData(state[field])))
            box.currentIndexChanged.connect(lambda _i: config.set_monitor(key, field, box.currentData()))
            form.addRow(label, self._resettable(box, lambda: box.setCurrentIndex(
                max(0, box.findData(MONITOR_DEFAULTS[field])))))

        def spin(field: str, label: str, low: int, high: int, zero_text: str = "") -> None:
            box = QSpinBox()
            box.setRange(low, high)
            box.setSuffix(" px")
            box.setSpecialValueText(zero_text)
            box.setValue(state[field])
            box.valueChanged.connect(lambda value: config.set_monitor(key, field, value))
            form.addRow(label, self._resettable(box, lambda: box.setValue(MONITOR_DEFAULTS[field])))

        return check, combo, spin

    def _profile_group(self, page: Page, title: str, key: str | None) -> None:
        """Einstellungen eines Monitors; key=None bearbeitet das gemeinsame Profil. Die Taskbar hat ihre eigene Seite."""
        form = page.group(title)
        _check, combo, _spin = self._monitor_controls(form, key)
        positions = {"top": "Oben", "bottom": "Unten"}
        combo("theme", "Theme", {"": "Wie das Haupt-Theme", **self.ctx.theme.all_themes()})
        combo("topbar_position", "Topbar-Position", positions)
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        from ui import sidebar
        combo("taskbar_position", "Taskbar-Position", sidebar.taskbar_positions())
        # --- [Antigravity Ende: Sidebar Support] ---
        if key is not None:
            copy = QPushButton("Auf alle Monitore übertragen", objectName="Secondary")
            copy.clicked.connect(lambda: self._copy_to_all(key))
            form.addRow(copy)

    def _audio_page(self) -> Page:
        page = Page()
        form = page.group("Ausgabegerät", full=True)
        self.audio_outputs = AudioOutputs(6)
        form.addRow(self.audio_outputs)
        self.audio_outputs.refresh()
        form = page.group("Lautstärke der Apps", full=True)
        form.addRow(AudioMixer(self.ctx))
        page.note("Windows merkt sich die Lautstärke jeder App. Der Balken zeigt, was die App gerade abspielt.")
        return page

    def _wallpaper_page(self) -> Page:
        page = Page()
        form = page.group("Wallpaper Engine", full=True)
        self.engine_status = page.text("Wird erkannt …")
        form.addRow(self.engine_status)
        self.ctx.status.updated.connect(self._show_engine)
        form.addRow("Verwenden", self._combo("wallpaper.source", {
            wallpapers.AUTO: "Automatisch erkennen", wallpapers.ENGINE: "Wallpaper Engine nutzen",
            wallpapers.OWN: "Hintergrund von PopWin zeigen"}))
        form = page.group("Animierter Hintergrund von PopWin", full=True)
        form.addRow(self._check("wallpaper.enabled", "Animierten Hintergrund abspielen"))
        form.addRow("Bildschirme", self._combo("wallpaper.screens", {"all": "Alle Bildschirme", "primary": "Nur dem Hauptmonitor"}))
        form.addRow(self._check("wallpaper.pause_covered", "Pausieren, wenn Fenster den Desktop verdecken"))
        form.addRow(self._check("wallpaper.pause_on_battery", "Im Akkubetrieb pausieren"))
        self.wall_list = QListWidget()
        self.wall_list.setFixedHeight(170)
        self.wall_list.itemClicked.connect(self._pick_wallpaper)
        form.addRow(self.wall_list)
        row = QHBoxLayout()
        add = QPushButton("Datei hinzufügen", objectName="Secondary")
        add.clicked.connect(self._add_wallpaper)
        remove = QPushButton("Löschen", objectName="Secondary")
        remove.clicked.connect(self._remove_wallpaper)
        row.addWidget(add)
        row.addWidget(remove)
        form.addRow(row)
        form = page.group("Hintergründe laden", full=True)
        self.wall_url = QLineEdit(placeholderText="Direkter Link zu einer MP4-, WebM- oder GIF-Datei")
        fetch = QPushButton("Laden", objectName="Primary")
        fetch.clicked.connect(self._download_wallpaper)
        row = QHBoxLayout()
        row.addWidget(self.wall_url, 1)
        row.addWidget(fetch)
        form.addRow(row)
        self.wall_note = page.text("")
        form.addRow(self.wall_note)
        row = QHBoxLayout()
        for name, url in wallpapers.FREE_SOURCES.items():
            source = QPushButton(f"Freie Videos auf {name}", objectName="Secondary")
            source.clicked.connect(lambda _=False, u=url: win_api.shell_open(u))
            row.addWidget(source)
        form.addRow(row)
        page.note("Geladene Videos fügst du über „Datei hinzufügen“ ein. Achte bei fremden Videos auf die Nutzungsrechte. "
                  "Läuft Wallpaper Engine, pausiert PopWin seinen Hintergrund, bei Vollbild-Spielen ebenso.")
        self._fill_wallpapers()
        return page

    def _show_engine(self, state: dict) -> None:
        self.engine_status.setText({wallpapers.RUNNING: "Wallpaper Engine läuft gerade. Der Hintergrund von PopWin bleibt solange aus.",
                                    wallpapers.INSTALLED: "Wallpaper Engine ist installiert, läuft aber nicht.",
                                    wallpapers.MISSING: "Wallpaper Engine wurde nicht gefunden. PopWin prüft das laufend, du musst nichts neu starten."}
                                   .get(state["wallpaper_engine"], "Wird erkannt …"))

    def _fill_wallpapers(self) -> None:
        self.wall_list.clear()
        current = self.ctx.config.get("wallpaper.file")
        entries = [(wallpapers.PRESET_PREFIX + key, name) for key, name in wallpapers.PRESETS.items()]
        for choice, name in entries + [(str(path), path.name) for path in wallpapers.library()]:
            item = QListWidgetItem(("✓  " if choice == current else "     ") + i18n.translate(name))
            item.setData(Qt.ItemDataRole.UserRole, choice)
            self.wall_list.addItem(item)

    def _pick_wallpaper(self, item: QListWidgetItem) -> None:
        """Eine Wahl soll etwas bewirken: Sie schaltet den Hintergrund ein. Wallpaper Engine beendet nur, wer zustimmt."""
        config = self.ctx.config
        config.set("wallpaper.file", item.data(Qt.ItemDataRole.UserRole))
        config.set("wallpaper.enabled", True)
        self._fill_wallpapers()
        if (self.ctx.status.state.get("wallpaper_engine") == wallpapers.RUNNING and config.get("wallpaper.source") == wallpapers.AUTO
                and QMessageBox.question(self, "PopWin", "Wallpaper Engine läuft gerade und liegt über diesem Hintergrund. Wallpaper Engine beenden und den Hintergrund von PopWin zeigen?") == QMessageBox.StandardButton.Yes):
            config.set("wallpaper.source", wallpapers.OWN)
            self.ctx.wallpaper.close_engine()

    def _add_wallpaper(self) -> None:
        for file in pick_files(self, "Hintergrund wählen", "Videos und GIFs (*.mp4 *.webm *.mkv *.mov *.m4v *.gif)")[:1]:
            try:
                self.ctx.config.set("wallpaper.file", str(wallpapers.add_file(file)))
            except (OSError, ValueError) as exc:
                self.wall_note.setText(str(exc))
            self._fill_wallpapers()

    def _remove_wallpaper(self) -> None:
        item = self.wall_list.currentItem()
        choice = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if choice and not choice.startswith(wallpapers.PRESET_PREFIX):  # Presets gehören zum Programm
            Path(choice).unlink(missing_ok=True)
            if self.ctx.config.get("wallpaper.file") == choice:
                self.ctx.config.set("wallpaper.file", "")
            self._fill_wallpapers()

    def _download_wallpaper(self) -> None:
        url = self.wall_url.text().strip()
        if not url or getattr(self, "wall_job", None) is not None:
            return
        self.wall_job = wallpapers.WallpaperDownload(url, self)
        self.wall_job.progress.connect(lambda value: self.wall_note.setText(f"Lädt: {value} %"))
        self.wall_job.finished_with.connect(self._wallpaper_loaded)
        self.wall_note.setText("Lädt …")
        self.wall_job.start()

    def _wallpaper_loaded(self, file: str, error: str) -> None:
        self.wall_job = None
        self.wall_note.setText(f"Das hat nicht geklappt: {error}" if error else "Fertig. Der Hintergrund steht in der Liste.")
        if file:
            self.ctx.config.set("wallpaper.file", file)
            self.wall_url.clear()
            self._fill_wallpapers()

    def _network_page(self) -> Page:
        page = Page()
        form = page.group("Speedtest", full=True)
        form.addRow(NetworkStats(self.ctx))
        page.note("Der Test lädt bis zu 50 MB von Cloudflare und sendet 8 MB dorthin. Die Ergebnisse bleiben auf diesem PC.")
        return page

    def _performance_page(self) -> Page:
        page = Page()
        form = page.group("Leistung", full=True)
        form.addRow("Stufe", self._combo("performance.level", {
            "saver": "Sparsam: keine Animationen, seltener nachsehen",
            "balanced": "Ausgewogen (empfohlen)",
            "beautiful": "Schön: alle Effekte"}))
        form.addRow(self._check("performance.game_mode", "Spielmodus: Leisten ruhen, solange ein Vollbild-Spiel läuft"))
        page.note("Den Unschärfe-Effekt (Blur) rechnet schon jetzt die Grafikkarte. Den Rest zeichnet Windows immer mit dem "
                  "Prozessor, darum spart „Sparsam“ am meisten.")
        form = page.group("Windows entlasten", full=True)
        for candidate in CANDIDATES:
            box = self._check(f"windows_trim.{candidate.key}", candidate.label)
            box.setToolTip(candidate.hint)
            form.addRow(box)
            form.addRow(QLabel(candidate.hint, objectName="Muted", wordWrap=True))
        page.note("Solange PopWin läuft, beendet es die angehakten Windows-Programme, sobald sie auftauchen. "
                  "Ab Werk ist nichts angehakt. Wichtige Teile von Windows fasst PopWin nie an.")
        form = page.group("Arbeitsspeicher von PopWin", full=True)
        form.addRow(PerformanceStats(self.ctx))
        page.note("Das Diagramm zeigt die letzte Stunde, seit PopWin läuft. Dazu zählen alle offenen PopWin-Apps.")
        form = page.group("Cache leeren", full=True)
        form.addRow(CachePanel())
        page.note("Datei-Cache: temporäre Dateien, die älter als ein Tag sind. RAM-Cache: der Standby-Speicher von Windows, "
                  "dafür fragt Windows nach Administratorrechten. Windows gibt ihn ohnehin frei, sobald ein Programm mehr braucht, "
                  "und füllt ihn danach wieder. Schneller wird der PC dadurch meist nicht.")
        return page

    def _privacy_page(self) -> Page:
        page = Page()
        form = page.group("Was PopWin mit deinen Daten macht", full=True)
        form.addRow(page.text("PopWin hat kein Konto, keine Werbung und keine Nutzungsstatistik. Einstellungen, Notizen, Chat und "
                              "Novas Gedächtnis liegen nur auf diesem PC."))
        form.addRow(page.text("Ins Internet geht nur das hier: Nutzt du die KI, also Nova, KI-Fragen in der Suche, Spracheingabe oder "
                              "Vorlesen, gehen dein Text, die Aufnahme und Bildschirmfotos, die Nova ansehen darf, an Google Gemini. "
                              "Ohne API-Key und ohne KI-Nutzung passiert das nie. Dazu prüft PopWin mit einer kurzen Verbindung zu "
                              "1.1.1.1, ob Internet da ist; dabei werden keine Daten von dir übertragen. Hintergründe lädt PopWin nur von Links, "
                              "die du selbst einträgst. Der Speedtest spricht nur mit Cloudflare und nur, wenn du ihn startest "
                              "oder das stündliche Messen einschaltest. Fragst du im Explorer die KI, gehen Dateinamen aus dem offenen Ordner an Google."))
        form.addRow(page.text("Erweiterungen sind fremder Code und dürfen alles, was PopWin darf. Füge nur welche hinzu, denen du vertraust."))
        form = page.group("Zwischenablage")
        form.addRow(self._check("privacy.clipboard_history", "Verlauf der Zwischenablage merken (in der Suche mit „v “)"))
        form.addRow(page.text("Die letzten 25 kopierten Texte bleiben nur im Arbeitsspeicher und sind nach dem Beenden von PopWin weg. "
                              "Lass das aus, wenn du Passwörter kopierst."))
        form = page.group("Nova")
        form.addRow(self._check("nova.allow_memory", "Nova darf sich Dinge über dich merken"))
        form.addRow(self._check("nova.allow_screen", "Den Bildschirm ansehen"))
        form.addRow(self._check("nova.allow_input", "Maus und Tastatur benutzen, nach deiner Erlaubnis"))
        forget = QPushButton("Novas Gedächtnis löschen", objectName="Danger")
        forget.clicked.connect(self._forget_memory)
        chat = QPushButton("Chat-Verlauf löschen", objectName="Secondary")
        chat.clicked.connect(self._forget_chat)
        form.addRow(forget)
        form.addRow(chat)
        form = page.group("Verbindung")
        form.addRow(self._check("privacy.online_check", "Prüfen, ob Internet da ist"))
        form = page.group("Deine Daten")
        folder = QPushButton("Datenordner öffnen", objectName="Secondary")
        folder.clicked.connect(lambda: win_api.shell_open(str(DATA_DIR)))
        form.addRow(folder)
        return page

    def _forget_memory(self) -> None:
        if QMessageBox.question(self, "PopWin", "Alles löschen, was Nova sich gemerkt hat?") == QMessageBox.StandardButton.Yes:
            self.ctx.nova.memory.clear()

    def _forget_chat(self) -> None:
        if QMessageBox.question(self, "PopWin", "Den Chat-Verlauf mit Nova löschen?") == QMessageBox.StandardButton.Yes:
            self.ctx.nova.new_chat()

    def _beta_page(self) -> Page:
        page = Page()
        form = page.group("Desktop-System")
        form.addRow("Desktop-Gruppen", self._combo("desktop_groups.style", desktop_groups.STYLES))
        page.note("Der Desktop wie am Handy: Zieh ein Icon auf ein anderes, dann entsteht eine Gruppe. Zu zeigt sie kleine Icons, "
                  "nach einem Klick die ganzen Namen. Die Dateien bleiben, wo sie sind. Solange es läuft, blendet PopWin die Windows-Symbole aus.")
        form = page.group("Experimentell")
        form.addRow(self._check("popwin_file_dialog", "Öffnen und Speichern im PopWin-Stil statt im Windows-Fenster"))
        form.addRow(self._check("keep_icons_off_widgets", "Desktop-Symbole von Widgets fernhalten"))
        form.addRow(self._check("launcher.override_win_s", "Win + S übernehmen (ersetzt die Windows-Suche)"))
        form.addRow(self._check("network.hourly_test", "Jede Stunde automatisch einen Speedtest machen"))
        form.addRow(self._check("taskbar.magnify", "Taskbar-Icons wachsen unter der Maus (wie am Mac)"))
        form.addRow(self._check("settings_v2", "Einstellungen mit Übersicht und Kategorien (aus: klassische Liste)"))
        tools = QPushButton("Symbolleiste über der Taskbar einstellen", objectName="Secondary")
        tools.setToolTip("Home, Suche, Nova und mehr schweben in einer eigenen Leiste über der Taskbar. Pro Monitor im Tab Taskbar.")
        tools.clicked.connect(lambda: self.open_named("Taskbar"))
        form.addRow(tools)
        fast = QPushButton("Schnellstart einrichten", objectName="Secondary")
        fast.setToolTip("Legt eine Windows-Aufgabe beim Anmelden an. Windows fragt dafür einmal nach Administratorrechten.")
        fast.clicked.connect(lambda: win_api.run_elevated("schtasks", win_api.logon_task_arguments(*app_shortcut("main"))))
        form.addRow(fast)
        form = page.group("PopWin-Apps statt Windows-Apps")
        for replacement in REPLACEMENTS:
            form.addRow(self._check(f"replacements.{replacement.key}", replacement.label))
        page.note("Gilt für alles, was du in PopWin startest: Startmenü, Taskbar und Suche. "
                  "Ausgeschaltet startet wieder die Windows-App.")
        form = page.group("Mit Windows verbinden")
        with_editor = QCheckBox("PopWin Editor bei „Öffnen mit“ in Windows anbieten")
        with_editor.setChecked(win_api.open_with_enabled())
        with_editor.toggled.connect(lambda on: win_api.set_open_with(
            on, *app_shortcut("editor"), str(ROOT / "assets" / "apps" / "editor.ico")))
        form.addRow(with_editor)
        defaults = QPushButton("Standard-Apps in Windows wählen", objectName="Secondary")
        defaults.clicked.connect(lambda: win_api.shell_open("ms-settings:defaultapps"))
        form.addRow(defaults)
        form = page.group("Windows-Explorer")
        classic = QCheckBox("Klassisches Menü: alle Einträge sofort auf einer Ebene")
        classic.setChecked(win_api.classic_menu_enabled())
        classic.toggled.connect(win_api.set_classic_menu)
        form.addRow(classic)
        verbs = QCheckBox("Eintrag „Im PopWin Explorer öffnen“ bei Ordnern")
        verbs.setChecked(win_api.explorer_verbs_enabled())
        verbs.toggled.connect(lambda on: win_api.set_explorer_verbs(
            on, *app_shortcut("explorer"), str(ROOT / "assets" / "popwin.ico")))
        form.addRow(verbs)
        restart = QPushButton("Windows-Explorer neu starten", objectName="Secondary")
        restart.clicked.connect(self._restart_explorer)
        form.addRow(restart)
        page.note("Das klassische Menü wirkt erst nach einem Neustart des Explorers. "
                                     "Beide Einstellungen lassen sich hier jederzeit rückgängig machen.")
        return page

    def _hardware_group(self, page: Page) -> None:
        form = page.group("Dieser PC")
        form.addRow(self._check("hide_missing_hardware", "Nur anzeigen, was dieser PC kann"))
        self.hardware_list = page.text("")
        form.addRow(self.hardware_list)
        self.ctx.status.updated.connect(self._show_hardware)

    def _show_hardware(self, state: dict) -> None:
        lines = [f"{label}: vorhanden" if state["hardware"].get(key) else f"{label}: nicht vorhanden"
                 for key, label in hardware.FEATURES.items() if key in state["hardware"]]
        self.hardware_list.setText("\n".join(lines) or "Wird erkannt …")

    def _bars_page(self) -> Page:
        page = self.taskbar_page = Page()
        self.taskbar_monitors = QWidget()
        ColumnLayout(self.taskbar_monitors)
        page.box.add_full(self.taskbar_monitors)
        self._fill_taskbar_monitors()
        form = page.group("Für alle Bildschirme")
        sync = self._check("taskbar.sync_icons", "Icons auf allen Taskbars synchronisieren")
        sync.toggled.connect(lambda _on: QTimer.singleShot(0, self._refresh_monitor_pages))
        form.addRow(sync)
        form.addRow("Angezeigte Fenster", self._combo("taskbar.window_filter", {
            "smart": "Hauptmonitor alle, andere Monitore nur ihre eigenen",
            "own": "Jeder Monitor nur seine eigenen",
            "all": "Jeder Monitor alle"}))
        form.addRow("Angeheftete Apps", self._combo("taskbar.pin_scope", {
            "all": "Hauptmonitor-Apps überall, dazu die eigenen jedes Monitors",
            "primary": "Jeder Monitor zeigt nur seine eigenen Apps"}))
        form.addRow(self._check("taskbar.preview", "Vorschau beim Draufbleiben auf einem Task"))
        form.addRow(self._check("taskbar.drag_switch", "Beim Ziehen über einem Task hinhalten wechselt zur App"))
        take_over = QPushButton("Apps der Windows-Taskleiste übernehmen", objectName="Secondary")
        take_over.clicked.connect(self.ctx.import_windows_pins)
        form.addRow(take_over)
        page.note(
            "Rechtsklick und Drag and Drop heften an die Taskbar des jeweiligen Monitors an. "
            "Tasks verschiebst du einfach mit der Maus. "
            "Theme und Bildschirme stellst du oben auf dieser Seite ein.")
        form = page.group("Echte Windows-Taskleiste")
        form.addRow(WindowsPinEditor())
        page.note(
            "Das ändert die Windows-Taskleiste selbst, nicht die PopWin-Taskbar. Anheften erlaubt Windows nur "
            "dem Explorer, deshalb öffnet „In Windows anheften“ den App-Ordner, dort geht es per Rechtsklick.")
        return page

    def _notifications_page(self) -> Page:
        page = Page()
        form = page.group("Benachrichtigungen")
        form.addRow("Seite", self._combo("notifications.side", {"right": "Rechts", "left": "Links"}))
        form.addRow("Breite", self._spin("notifications.width", 280, 700, " px"))
        form.addRow(self._check("notifications.group", "Nach App gruppieren"))
        form.addRow(self._check("notifications.show_count", "Anzahl neben der Glocke anzeigen"))
        restore = QPushButton("Gelöschte wieder anzeigen", objectName="Secondary")
        restore.clicked.connect(lambda: self.ctx.config.set("notifications.hidden", []))
        form.addRow(restore)
        windows = QPushButton("Benachrichtigungen in Windows einstellen", objectName="Secondary")
        windows.clicked.connect(lambda: win_api.shell_open("ms-settings:notifications"))
        form.addRow(windows)
        page.note(
            "Die Glocke oben rechts öffnet die Liste. Löschen entfernt Einträge nur in PopWin, "
            "in der Mitteilungszentrale von Windows bleiben sie stehen. Die Glocke schaltest du pro Bildschirm unter "
            "Topbar ein oder aus.")
        return page

    def _fill_extensions(self) -> None:
        page = self.extensions_page
        while page.box.count():
            item = page.box.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        page._last_form = None
        manager = self.ctx.extensions
        for key, extension in manager.items.items():
            form = page.group(extension.title)
            form.addRow(page.text(extension.description))
            active = QCheckBox("Erweiterung aktiv")
            active.setChecked(manager.enabled(key))
            active.toggled.connect(lambda on, k=key: manager.set_enabled(k, on))
            form.addRow(active)
            extension.settings(form, self)
        form = page.group("Eigene Erweiterungen")
        add = QPushButton("Erweiterung hinzufügen", objectName="Secondary")
        add.clicked.connect(self._add_extension)
        folder = QPushButton("Ordner öffnen", objectName="Secondary")
        folder.clicked.connect(self._open_extension_folder)
        form.addRow(add)
        form.addRow(folder)
        for name, error in manager.errors.items():
            form.addRow(page.text(f"{name} ließ sich nicht laden: {error}"))
        page.note("Eine Erweiterung ist eine Python-Datei und darf alles, was PopWin darf. "
                  "Füge nur Dateien hinzu, denen du vertraust.")

    def _add_extension(self) -> None:
        path = QFileDialog.getOpenFileName(self, "Erweiterung wählen", "", "Python-Datei (*.py)")[0]
        if path:
            self.ctx.extensions.add_file(path)
            self._fill_extensions()

    def _open_extension_folder(self) -> None:
        USER_FOLDER.mkdir(parents=True, exist_ok=True)
        win_api.shell_open(str(USER_FOLDER))

    def _menu_page(self) -> Page:
        page = Page()
        form = page.group("Rechtsklickmenü")
        form.addRow(self._check("context_menu.enabled", "Eigenes Menü auf dem Desktop verwenden"))
        form.addRow("Menügröße", self._combo("context_menu.size", {
            "kompakt": "Kompakt", "normal": "Normal", "gross": "Groß"}))
        form.addRow("Schriftgröße", self._spin("context_menu.font_size", 10, 20, " px"))
        form.addRow("Icon-Größe", self._spin("context_menu.icon_size", 12, 28, " px"))
        form.addRow("Deckkraft", self._opacity("context_menu.opacity"))
        form.addRow("Theme", self._combo("context_menu.theme", {"": "Wie das Haupt-Theme", **self.ctx.theme.all_themes()}))
        form.addRow(page.text("Größe, Schrift, Deckkraft und Stil gelten für alle Rechtsklickmenüs von PopWin: "
                              "Desktop, Taskbar, Topbar, Explorer und die anderen Apps."))
        page.note("Auf einer Datei oder einem Ordner bleibt das Windows-Menü aktiv, "
                                     "weil nur Windows die Einträge der jeweiligen Programme kennt.")
        return page

    def _restart_explorer(self) -> None:
        answer = QMessageBox.question(self, "PopWin", "Windows-Explorer jetzt neu starten? Offene Ordnerfenster schließen sich.")
        if answer == QMessageBox.StandardButton.Yes:
            win_api.restart_explorer()

    def _launcher_page(self) -> Page:
        page = Page()
        form = page.group("Suchleiste")
        form.addRow("Tastenkürzel", self._line("launcher.hotkey"))
        self.hotkey_status = QLabel(objectName="Muted")
        self.hotkey_status.setWordWrap(True)
        form.addRow(self.hotkey_status)
        form.addRow(self._check("launcher.live_actions", "Aktionen schon beim Tippen vorschlagen"))
        form.addRow(self._check("launcher.situation_actions", "Vorschläge zur Situation beim Öffnen"))
        form.addRow(self._check("launcher.quick_answers", "Rechnen, Umrechnen und „timer 10“ direkt in der Suche"))
        form.addRow(self._check("launcher.file_search", "Dateien nach Namen finden (Desktop, Dokumente, Downloads, Bilder)"))
        form.addRow("Im Web suchen mit „w …“", self._combo("launcher.web_search", {
            "duckduckgo": "DuckDuckGo", "google": "Google", "bing": "Bing", "startpage": "Startpage"}))
        page.note(
            "Beispiele: <b>darkmode aktivieren</b>, <b>regedit</b>, <b>shell:startup</b>, "
            "<b>? Wie hoch ist der Eiffelturm</b>. Der KI-Teil läuft nur mit Enter, nie beim Tippen.")

        form = page.group("Aussehen der Suchanzeige")
        form.addRow("Breite", self._spin("launcher.width", 360, 1400, " px"))
        form.addRow("Position", self._combo("launcher.position", {
            "top": "Oben", "upper": "Oberes Drittel", "center": "Mitte"}))
        form.addRow("Erscheint auf", self._combo("launcher.screen", {
            "cursor": "Monitor mit der Maus", "primary": "Hauptmonitor"}))
        form.addRow("Sichtbare Ergebnisse", self._spin("launcher.max_results", 3, 15))
        form.addRow("Schrift Eingabe", self._spin("launcher.input_font", 12, 32, " px"))
        form.addRow("Schrift Ergebnisse", self._spin("launcher.result_font", 10, 22, " px"))
        form.addRow("Deckkraft", self._opacity("launcher.opacity"))
        form.addRow("Theme", self._combo("launcher.theme", {"": "Wie das Haupt-Theme", **self.ctx.theme.all_themes()}))
        form.addRow("Platzhaltertext", self._line("launcher.placeholder"))
        form.addRow(self._check("launcher.show_icons", "Icons anzeigen"))
        form.addRow(self._check("launcher.show_commands", "Systembefehle wie bei Win + R anzeigen"))
        preview = QPushButton("Vorschau öffnen", objectName="Secondary")
        preview.clicked.connect(self.ctx.show_launcher)
        form.addRow(preview)
        return page

    def _ai_page(self) -> Page:
        page = Page()
        form = page.group("KI in der Suche")
        form.addRow(self._check("launcher.ai_fallback", "Gemini fragen, wenn nichts Lokales gefunden wird"))
        form.addRow(self._check("launcher.ai_actions", "KI darf Aktionen vorschlagen, immer mit Bestätigung"))
        form.addRow("Aufgabenleiste", self._combo("launcher.task_side", {"right": "Rechts", "left": "Links"}))
        form.addRow("Sprachaufnahme höchstens", self._spin("launcher.voice_seconds", 3, 60, " s"))
        form = page.group("Modelle")
        form.addRow("Suche", self._line("launcher.search_models"))
        form.addRow("Reparatur", self._line("launcher.repair_models"))
        page.note(
            "Komma-getrennte Reihenfolge. Ist ein Modell ausgeschöpft oder nicht erreichbar, übernimmt das nächste. "
            "Die Spracheingabe schickt die Aufnahme an Google, das Mikrofon läuft nur während der Aufnahme.")
        return page

    def _nova_page(self) -> Page:
        page = Page()
        form = page.group("Nova")
        enabled = self._check("nova.enabled", "Nova einschalten (Knöpfe, Tastenkürzel und Fragen aus der Suche)")
        form.addRow(enabled)
        self.nova_off_note = QLabel("Nova ist ausgeschaltet. Mit dem Haken oben kommt sie zurück.", objectName="Muted", wordWrap=True)
        form.addRow(self.nova_off_note)
        self._nova_groups_start = page.box.count()
        form = page.group("Chat")
        opener = QPushButton("Nova öffnen", objectName="Primary")
        opener.clicked.connect(self.ctx.nova_panel.reveal)
        form.addRow(opener)
        form.addRow("Tastenkürzel", self._line("nova.hotkey"))
        form.addRow("Aussehen", self._combo("nova.look", {
            "popwin": "PopWin-Theme", **{name: f"Classic {name.capitalize()}" for name in NOVA_CLASSICS}}))
        form.addRow("Seite", self._combo("nova.side", {"right": "Rechts", "left": "Links"}))
        screens = {screen_key(screen): f"Bildschirm {win_api.monitor_numbers().get(native_rect(screen), 0)}"
                   for screen in QGuiApplication.screens()}
        form.addRow("Erscheint auf", self._combo("nova.screen", {
            "cursor": "Dem Bildschirm mit der Maus", "primary": "Dem Hauptbildschirm", **screens}))
        form.addRow(self._check("nova.reserve_space", "Platz am Rand freihalten, damit Nova keine Fenster verdeckt"))
        form.addRow("Breite", self._spin("nova.width", 320, 800, " px"))
        form.addRow(self._check("nova.keep_open", "Bleibt offen, wenn man woanders klickt"))
        form.addRow(self._check("nova.from_search", "Fragen aus der Suche gehen an Nova"))
        form = page.group("Was Nova darf")
        form.addRow(self._check("nova.allow_screen", "Den Bildschirm ansehen"))
        form.addRow(self._check("nova.allow_input", "Maus und Tastatur benutzen, nach deiner Erlaubnis"))
        # --- [Antigravity Start: Novas Persönlichkeit anpassen und zurücksetzen (Rest: Claude Code)] ---
        form = page.group("Persönlichkeit & Charakter")
        self.nova_persona_edit = QPlainTextEdit()
        self.nova_persona_edit.setPlainText(nova_brain.load_persona())
        self.nova_persona_edit.setFixedHeight(130)
        self.nova_persona_edit.setPlaceholderText("Beschreibe Novas Persönlichkeit, Charakter und Verhalten …")
        form.addRow(self.nova_persona_edit)

        btn_row = QHBoxLayout()
        save_persona_btn = QPushButton("Persönlichkeit speichern", objectName="Primary")
        save_persona_btn.clicked.connect(self._save_nova_persona)
        reset_persona_btn = QPushButton("Auf Standard zurücksetzen", objectName="Secondary")
        reset_persona_btn.clicked.connect(self._reset_nova_persona)
        btn_row.addWidget(save_persona_btn)
        btn_row.addWidget(reset_persona_btn)
        btn_row.addStretch(1)
        form.addRow(btn_row)

        self.nova_persona_status = QLabel(objectName="Muted")
        form.addRow(self.nova_persona_status)
        page.note("Hier kannst du festlegen, wie Nova sich verhält, spricht und reagiert. Ein Klick auf "
                  "„Auf Standard zurücksetzen“ stellt den Originalzustand wieder her.")
        self.nova_page = page
        enabled.toggled.connect(lambda _on: QTimer.singleShot(0, self, self._show_nova_groups))
        QTimer.singleShot(0, self, self._show_nova_groups)
        # --- [Antigravity Ende] ---
        form = page.group("Stimme")
        form.addRow(self._check("nova.speak", "Nova antwortet mit Stimme"))
        form.addRow("Wie", self._combo("nova.speak_engine", {
            "live": "Gemini Live: hört und spricht live, wie früher", "tts": "Normales Modell, Antwort wird vorgelesen"}))
        form.addRow("Stimme", self._combo("nova.voice", {name: f"{name} · {trait}" for name, trait in VOICES.items()}))
        form.addRow("Sprechweise", self._line("nova.voice_style"))
        form.addRow("Live-Modell", self._line("nova.live_model"))
        page.note("Die Sprechweise in eigenen Worten, etwa „ruhig und etwas langsamer“. Du kannst Nova auch einfach sagen, "
                  "wie sie klingen soll. Mit Stimme läuft Nova über Gemini Live, getippt wie gesprochen; ohne Stimme über die "
                  "Modelle unten.")
        form = page.group("Sprechen")
        form.addRow(self._check("nova.listen_auto_stop", "Hört auf, sobald du eine Pause machst"))
        silence = QDoubleSpinBox()
        silence.setRange(0.4, 5.0)
        silence.setSingleStep(0.1)
        silence.setSuffix(" s")
        silence.setValue(float(self.ctx.config.get("nova.listen_silence")))
        silence.valueChanged.connect(lambda value: self.ctx.config.set("nova.listen_silence", round(value, 1)))
        form.addRow("Pause bis zum Stopp", self._resettable(silence, lambda: silence.setValue(self.ctx.config.default_of("nova.listen_silence"))))
        form.addRow(self._check("nova.listen_live", "Live zeigen, was Nova verstanden hat"))
        form.addRow(self._check("nova.listen_followup", "Fragt Nova zurück, hört sie gleich wieder zu"))
        page.note("Das Mikrofon ist nur offen, solange du sprichst. Ein Klick auf das Mikrofon heißt: fertig gesprochen.")
        form = page.group("Artefakte")
        form.addRow(self._check("nova.artifacts_tab", "Tab „Artefakte“ im Nova-Panel"))
        form.addRow(self._check("nova.artifacts_notify", "Hinweis zeigen, wenn eine Überwachung etwas Neues findet"))
        artifacts = QPushButton("Artefakte öffnen", objectName="Secondary")
        artifacts.clicked.connect(lambda: self.ctx.nova_panel.show_artifact(""))
        form.addRow(artifacts)
        page.note("Artefakte sind Aufgaben, Notizen, Überwachungen und kleine Werkzeuge, die Nova über längere Zeit verfolgt. "
                  "Du kannst sie auch selbst anlegen. Sie liegen nur auf diesem PC.")
        self.connections_group = page.group("Verbindungen")
        self._fill_connections()
        page.note("Verbindungen zu Diensten wie OVH legt Nova mit dir an. Die Keys trägst du hier ein; sie bleiben im "
                  "Windows-Anmeldespeicher, Nova sieht sie nie.")
        form = page.group("KI")
        form.addRow("Verbrauch", self._combo("nova.economy", {"sparsam": "Sparsam", "qualitaet": "Beste Qualität"}))
        form.addRow("Modelle", self._line("nova.models"))
        form = page.group("Eigener API-Key für Nova")
        self.nova_key = QLineEdit()
        self.nova_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.nova_key.setPlaceholderText("API-Key einfügen")
        save = QPushButton("Speichern", objectName="Primary")
        save.clicked.connect(self._save_nova_key)
        self.nova_key_status = QLabel(objectName="Muted")
        row = QHBoxLayout()
        row.addWidget(self.nova_key, 1)
        row.addWidget(save)
        form.addRow(row)
        form.addRow(self.nova_key_status)
        page.note("Ohne eigenen Key benutzt Nova den Key aus dem Setup. Befehle und Dateiänderungen fragt sie immer vorher. "
                  "Was du schreibst und was sie ansieht, geht an Google. Ihr Gedächtnis und der Chat bleiben auf diesem PC.")
        self._refresh_nova()
        return page

    def _fill_connections(self) -> None:
        form = self.connections_group
        while form.rowCount():
            form.removeRow(0)
        names = self.ctx.nova.connections.names()
        for name in names:
            form.addRow(ConnectionForm(self.ctx.nova.connections, name))
        if not names:
            form.addRow(QLabel("Noch keine. Sag Nova zum Beispiel: „Verbinde dich mit meinem OVH-Konto.“", objectName="Muted"))

    def _refresh_nova(self) -> None:
        saved = bool(self.ctx.config.api_key(NOVA_KEY))
        self.nova_key_status.setText("Ein eigener Key ist gespeichert." if saved else "Noch kein eigener Key gespeichert.")
        # --- [Antigravity Start: Status der Persönlichkeit aktualisieren] ---
        if hasattr(self, "nova_persona_status"):
            is_custom = nova_brain.is_custom_persona()
            self.nova_persona_status.setText("Individuelle Persönlichkeit aktiv." if is_custom else "Standard-Persönlichkeit aktiv.")
        # --- [Antigravity Ende] ---

    # --- [Antigravity Start: Persönlichkeit speichern / zurücksetzen] ---
    def _save_nova_persona(self) -> None:
        nova_brain.save_custom_persona(self.nova_persona_edit.toPlainText())
        self._refresh_nova()

    def _reset_nova_persona(self) -> None:
        nova_brain.reset_persona()
        self.nova_persona_edit.setPlainText(nova_brain.default_persona())
        self._refresh_nova()
    # --- [Antigravity Ende] ---

    def _save_nova_key(self) -> None:
        self.ctx.config.set_api_key(self.nova_key.text().strip(), NOVA_KEY)
        self.nova_key.clear()
        self._refresh_nova()

    def show_active_hotkey(self, active: str) -> None:
        wanted = self.ctx.config.get("launcher.hotkey")
        if active == wanted:
            self.hotkey_status.setText(f"Aktiv: {active}")
        elif active:
            self.hotkey_status.setText(f"{wanted} ist von einem anderen Programm belegt. Aktiv ist: {active}")
        else:
            self.hotkey_status.setText(f"{wanted} ist belegt. Der Launcher ist über die Lupe in der Taskbar erreichbar.")

    def _start_page(self) -> Page:
        page = Page()
        form = page.group("Home-Menü")
        form.addRow("Windows-Taste öffnet", self._combo("start_menu.win_key", {
            "home": "PopWin Home-Menü", "launcher": "PopWin Suche", "windows": "Windows-Startmenü"}))
        form.addRow("Home-Menü öffnet auf", self._combo("start_menu.win_key_screen", {
            "cursor": "Dem Bildschirm mit der Maus", "primary": "Dem Hauptbildschirm"}))
        form.addRow(self._start_variants())
        form.addRow("Spalten (Raster)", self._spin("start_menu.columns", 1, 8))
        form.addRow("Icon-Größe", self._spin("start_menu.icon_size", 20, 64, " px"))
        form.addRow("Breite", self._spin("start_menu.width", 280, 900, " px"))
        form.addRow("Angepinnt", PinEditor(self.ctx, "start_menu.pinned"))
        return page

    def _start_variants(self) -> QWidget:
        """Aussehen des Home-Menüs als anklickbare Vorschaukarten; „Ausprobieren“ öffnet es gleich so."""
        from ui.start_menu import VARIANTS, VariantCard
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.start_cards = []
        for key in VARIANTS:
            card = VariantCard(key)
            card.clicked.connect(lambda _c=False, k=key: self._pick_start_variant(k))
            row.addWidget(card)
            self.start_cards.append(card)
        row.addStretch(1)
        box.addLayout(row)
        line = QHBoxLayout()
        self.start_hint = QLabel("", objectName="Muted")
        line.addWidget(self.start_hint, 1)
        test = QPushButton("Ausprobieren", objectName="Secondary")
        test.clicked.connect(lambda: self.ctx.toggle_start_menu(None))
        line.addWidget(test)
        box.addLayout(line)
        self._mark_start_variant()
        return holder

    def _pick_start_variant(self, key: str) -> None:
        self.ctx.config.set("start_menu.layout", key)
        self._mark_start_variant()

    def _mark_start_variant(self) -> None:
        from ui.start_menu import VARIANTS, variant_of
        current = variant_of(self.ctx.config.get("start_menu"))
        for card in self.start_cards:
            card.setChecked(card.key == current)
        self.start_hint.setText(VARIANTS[current][1])

    def _fill_widgets(self) -> None:
        page = self.widget_page
        while page.box.count():
            item = page.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        form = page.group("Widget hinzufügen")
        gallery = QPushButton("Widget-Galerie öffnen", objectName="Primary")
        gallery.clicked.connect(self.ctx.widgets.open_gallery)
        form.addRow(gallery)
        form = page.group("Aktive Widgets")
        specs = self.ctx.config.get("widgets")
        for spec in specs:
            button = QPushButton("Entfernen", objectName="Secondary")
            button.clicked.connect(lambda _=False, i=spec["id"]: (self.ctx.widgets.remove(i), self._fill_widgets()))
            form.addRow(WIDGET_LABELS.get(spec["type"], spec["type"]), button)
        page.note("Rechtsklick auf ein Widget öffnet die Galerie zum Hinzufügen und Anpassen. "
                                     "Mit der linken Maustaste lässt es sich verschieben.")

    def _show_nova_groups(self) -> None:
        """Ohne Nova bleibt auf ihrer Seite nur der Schalter zum Wiedereinschalten."""
        on = self.ctx.nova_enabled()
        self.nova_off_note.setVisible(not on)
        box = self.nova_page.box
        for index in range(self._nova_groups_start, box.count()):
            widget = box.itemAt(index).widget()
            if widget is not None:
                widget.setVisible(on)

    # --- Neues Einstellungsmenü (Beta) -------------------------------------------------
    def new_look(self) -> bool:
        return bool(self.ctx.config.get("settings_v2"))

    def _apply_look(self) -> None:
        """Neu: Übersicht als Startseite und Navigation nach Themen. Klassisch: die bekannte Liste ohne Übersicht."""
        new = self.new_look()
        self.tabs.arrange(new)
        self.tabs.set_visible_tab(self.overview_tab, new)  # erst danach: Das Umsortieren vergisst, was versteckt war
        self.tabs.nav.setFixedWidth(210 if new else 190)
        if new or self.tabs.currentIndex() == self.overview_tab:
            self.tabs.setCurrentIndex(self.overview_tab if new else self.overview_tab + 1)
        self.apply_theme()

    def _overview_page(self) -> Page:
        """Startseite des neuen Menüs: das Wichtigste zum Antippen, dazu Sprünge zu den großen Seiten."""
        page = Page()
        from ui.base import themed_menu
        from ui.theme_page import ThemeQuickPick
        form = page.group("Dein Design", full=True)
        self.theme_pick = ThemeQuickPick(self.ctx, lambda parent: themed_menu(self.ctx, parent))
        form.addRow(self.theme_pick)
        form = page.group("Schnell umschalten", full=True)
        grid = QGridLayout()
        grid.setSpacing(8)
        config = self.ctx.config
        switches = [
            ("Dunkler Modus", lambda: config.get("mode") == "dark", lambda on: self.ctx.theme.set_mode("dark" if on else "light")),
            ("Animationen", lambda: config.get("animations.enabled"), lambda on: config.set("animations.enabled", on)),
            ("Rote Zahlen an Icons", lambda: config.monitor()["task_badges"],
             lambda on: config.set_monitor(None, "task_badges", on)),
            ("Schwebendes Dock", lambda: config.monitor()["dock_floating"],
             lambda on: config.set_monitor(None, "dock_floating", on)),
            ("Taskbar ausblenden", lambda: config.monitor()["auto_hide"], lambda on: config.set_monitor(None, "auto_hide", on)),
            ("Topbar ausblenden", lambda: config.monitor()["topbar_auto_hide"],
             lambda on: config.set_monitor(None, "topbar_auto_hide", on)),
        ]
        self.overview_tiles = []
        for number, (label, read, write) in enumerate(switches):
            tile = QPushButton(label, objectName="QuickTile")
            tile.setCheckable(True)
            tile.setChecked(bool(read()))
            tile.setMinimumHeight(54)
            tile.toggled.connect(write)
            grid.addWidget(tile, number // 3, number % 3)
            self.overview_tiles.append(tile)
        form.addRow(grid)
        form = page.group("Häufig gebraucht", full=True)
        jumps = QGridLayout()
        jumps.setSpacing(8)
        for number, label in enumerate(("Themes", "Topbar", "Taskbar", "Hintergrund", "Teilen & Presets", "Nova")):
            button = QPushButton(label, objectName="Secondary")
            button.setMinimumHeight(40)
            button.clicked.connect(lambda _=False, name=label: self.open_named(name))
            jumps.addWidget(button, number // 3, number % 3)
        form.addRow(jumps)
        page.note("Das ist das neue Einstellungsmenü (Beta). Zurück zum alten geht es unter Beta.")
        return page

    def _share_page(self) -> Page:
        """Eigene Einstellungen als Preset merken oder als Datei an Freunde weitergeben."""
        page = Page()
        form = page.group("Presets")
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("Name, z. B. „Gaming“ oder „Arbeit“")
        save = QPushButton("Aktuelle Einstellungen als Preset speichern", objectName="Primary")
        save.clicked.connect(self._save_preset)
        form.addRow("Name", self.preset_name)
        form.addRow(save)
        self.preset_list = QListWidget()
        self.preset_list.setFixedHeight(150)
        self.preset_list.itemDoubleClicked.connect(lambda _item: self._load_preset())
        form.addRow(self.preset_list)
        row = QHBoxLayout()
        for label, handler in (("Laden", self._load_preset), ("Exportieren …", self._export_preset),
                               ("Löschen", self._delete_preset)):
            button = QPushButton(label, objectName="Secondary")
            button.clicked.connect(handler)
            row.addWidget(button)
        form.addRow(row)
        self._fill_presets()

        form = page.group("Mit anderen teilen")
        export = QPushButton("Einstellungen exportieren …", objectName="Secondary")
        export.clicked.connect(self._export_settings)
        load = QPushButton("Einstellungen importieren …", objectName="Secondary")
        load.clicked.connect(self._import_settings)
        form.addRow(export)
        form.addRow(load)
        page.note("Die Datei enthält nur das Aussehen und die Einstellungen von PopWin. Nicht dabei sind Schlüssel, "
                  "Nova-Verbindungen, Notizen und Widgets, dein Hintergrundbild und was nur zu deinen Bildschirmen passt.")
        return page

    def _fill_presets(self) -> None:
        self.preset_list.clear()
        for name in config_manager.list_presets():
            self.preset_list.addItem(name)

    def _picked_preset(self) -> str:
        item = self.preset_list.currentItem()
        if item is None:
            QMessageBox.information(self, "PopWin", "Bitte wähle zuerst ein Preset aus der Liste.")
            return ""
        return item.text()

    def _save_preset(self) -> None:
        name = self.preset_name.text()
        try:
            path = config_manager.preset_file(name)
        except ValueError as exc:
            QMessageBox.information(self, "PopWin", str(exc))
            return
        if path.exists() and QMessageBox.question(
                self, "PopWin", f"Das Preset „{path.stem}“ gibt es schon. Überschreiben?") != QMessageBox.StandardButton.Yes:
            return
        config_manager.save_preset(self.ctx.config, name)
        self.preset_name.clear()
        self._fill_presets()

    def _apply_file(self, path: Path, what: str) -> None:
        if QMessageBox.question(self, "PopWin", f"{what} übernehmen? Deine jetzigen Einstellungen werden dabei ersetzt. "
                                "Tipp: Speichere sie vorher als Preset.") != QMessageBox.StandardButton.Yes:
            return
        try:
            config_manager.import_settings(self.ctx.config, path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "PopWin", f"Konnte nicht übernommen werden: {exc}")
            return
        self.ctx.theme.changed.emit()  # Stil, Hell/Dunkel und Kacheln liest der Theme-Manager direkt aus den Einstellungen
        self._refresh_monitor_pages()
        if QMessageBox.question(self, "PopWin", "Übernommen. PopWin jetzt neu starten, damit wirklich alles passt?")                 == QMessageBox.StandardButton.Yes:
            self.ctx.restart_ui()

    def _load_preset(self) -> None:
        name = self._picked_preset()
        if name:
            self._apply_file(config_manager.preset_file(name), f"Preset „{name}“")

    def _delete_preset(self) -> None:
        name = self._picked_preset()
        if name and QMessageBox.question(self, "PopWin", f"Preset „{name}“ löschen?") == QMessageBox.StandardButton.Yes:
            config_manager.delete_preset(name)
            self._fill_presets()

    def _export_preset(self) -> None:
        name = self._picked_preset()
        if not name:
            return
        target, _ = QFileDialog.getSaveFileName(self, "Preset exportieren", str(Path.home() / f"PopWin {name}.json"),
                                                "PopWin-Einstellungen (*.json)")
        if target:
            Path(target).write_bytes(config_manager.preset_file(name).read_bytes())

    def _export_settings(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Einstellungen exportieren", str(Path.home() / "PopWin Einstellungen.json"),
                                                "PopWin-Einstellungen (*.json)")
        if target:
            config_manager.export_settings(self.ctx.config, Path(target), "Einstellungen")

    def _import_settings(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Einstellungen importieren", str(Path.home()),
                                                "PopWin-Einstellungen (*.json)")
        if source:
            self._apply_file(Path(source), "Diese Einstellungen")

    def _setup_page(self) -> Page:
        page = Page()
        page.note("In vier Schritten ist PopWin eingerichtet. Jeder Schritt lässt sich "
                                     "jederzeit rückgängig machen.")

        form = page.group("1. Autostart einrichten")
        form.addRow(page.text("PopWin startet dann automatisch mit Windows. Dafür wird eine Verknüpfung "
                              "im Ordner shell:startup angelegt."))
        self.autostart = QCheckBox("Mit Windows starten")
        self.autostart.toggled.connect(lambda v: win_api.set_autostart(v, *app_shortcut("main")))
        form.addRow(self.autostart)

        form = page.group("2. Wallpaper Engine")
        form.addRow(page.text(
            "PopWin fasst den Hintergrund nicht an, Wallpaper Engine läuft darunter unverändert weiter. "
            "Für beste Ergebnisse: In Wallpaper Engine unter Einstellungen → Leistung bei "
            "<b>Andere Anwendung im Vordergrund</b> auf <b>Weiter ausführen</b> stellen, damit das "
            "Hintergrundbild neben den Leisten nicht anhält. Unter Einstellungen → Allgemein "
            "<b>Mit Windows starten</b> aktivieren."))

        form = page.group("3. Windows-Taskleiste ausblenden")
        form.addRow(page.text(
            "Die normale Taskleiste ist ausgeblendet, solange PopWin läuft und eine eigene Taskbar aktiv ist. "
            "Beim Beenden kommt sie automatisch zurück, auch nach einem Absturz. Notfall: "
            "Strg + Alt + Umschalt + K beendet PopWin sofort, und der Startmenü-Eintrag "
            "<b>Windows-Taskleiste wiederherstellen</b> holt sie von Hand zurück."))
        self.hide_taskbar = QCheckBox("Windows-Taskleiste ausblenden")
        self.hide_taskbar.toggled.connect(self.ctx.set_native_taskbar_hidden)
        form.addRow(self.hide_taskbar)

        form = page.group("Einrichtung")
        again = QPushButton("Einrichtung erneut starten", objectName="Primary")
        again.clicked.connect(lambda: self.ctx.show_setup())
        news = QPushButton("Was ist neu?", objectName="Secondary")
        news.clicked.connect(lambda: self.ctx.show_whats_new(force=True))
        form.addRow(again)
        form.addRow(news)
        page.note("Theme, Leisten, Nova und Leistung in einem schönen Durchgang. Nichts geht dabei verloren.")

        form = page.group("Updates")
        form.addRow(page.text(f"Installiert: PopWin {VERSION}" + ("" if REPO else " · Für diese Ausgabe gibt es noch keine Update-Quelle.")))
        form.addRow(self._check("updates.auto_check", "Beim Start nach Updates suchen"))
        search = QPushButton("Jetzt nach Updates suchen", objectName="Secondary")
        self.update_state = page.text("")
        search.clicked.connect(self._search_updates)
        form.addRow(search)
        form.addRow(self.update_state)
        if is_dev():
            dev = QPushButton("Dev-Menü öffnen", objectName="Primary")
            dev.clicked.connect(self._open_dev_menu)
            form.addRow(dev)

        form = page.group("4. Gemini API-Key (optional)")
        form.addRow(page.text(
            "Für die KI im Launcher. Den Schlüssel erstellst du im Google AI Studio über „Create API key“. "
            "Er wird im Windows-Anmeldeinformationsspeicher abgelegt und nicht in settings.json."))
        studio = QPushButton("Google AI Studio öffnen", objectName="Secondary")
        studio.clicked.connect(lambda: win_api.shell_open("https://aistudio.google.com/apikey"))
        form.addRow(studio)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("API-Key einfügen")
        save = QPushButton("Speichern", objectName="Primary")
        save.clicked.connect(self._save_key)
        self.key_status = QLabel(objectName="Muted")
        row = QHBoxLayout()
        row.addWidget(self.key_input, 1)
        row.addWidget(save)
        form.addRow(row)
        form.addRow(self.key_status)

        form = page.group("Beenden")
        restart = QPushButton("PopWin neu starten", objectName="Secondary")
        restart.clicked.connect(self.ctx.restart_ui)
        form.addRow(restart)
        stop = QPushButton("PopWin deaktivieren", objectName="Danger")
        stop.clicked.connect(self.ctx.disable_ui)
        form.addRow(stop)
        self._refresh_setup()
        return page

    def _refresh_setup(self) -> None:
        for box, value in ((self.autostart, win_api.autostart_enabled()),
                           (self.hide_taskbar, self.ctx.config.get("shell.hide_native_taskbar"))):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.key_status.setText("Ein Key ist gespeichert." if self.ctx.config.api_key() else "Noch kein Key gespeichert.")

    def _save_key(self) -> None:
        key = self.key_input.text().strip()
        ok = self.ctx.config.set_api_key(key)
        self.key_input.clear()
        if ok:
            self.key_status.setText("Key gespeichert." if key else "Key gelöscht.")
        else:
            self.key_status.setText("Der Key konnte nicht gespeichert werden.")
