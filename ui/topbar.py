"""Pop!_OS-Topbar: Benutzer und Workspaces, Mitte, Status-Icons und Uhr."""
from __future__ import annotations

import getpass
import os
import subprocess

from PySide6.QtCore import QDateTime, QEasingCurve, QEvent, QLocale, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (QAbstractButton, QApplication, QBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from core import hardware, system_status, win_api
from core.net_stats import NameLoader
from core.monitor_manager import native_point, native_rect
from core.notifications import visible_notices
from core.quick_settings import format_remaining
from core.replacements import launch
from ui.account_panel import AccountPanel
from ui.animation import duration_of
from ui.audio_outputs import AudioDevices, AudioOutputs
from ui.base import BarWindow, G, IconButton, NovaButton, PopupPanel, set_prop, themed_menu
from ui.quick_panel import QuickPanel
from ui.window_panel import WindowPanel


NOVA_ICON = 20


def _user_name() -> str:
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


def format_clock(now: QDateTime, seconds: bool = True) -> tuple[str, str]:
    return now.toString("HH:mm:ss" if seconds else "HH:mm"), now.toString("dd.MM.yyyy")


def format_center(now: QDateTime, seconds: bool, style: str = "both") -> str:
    time = "HH:mm:ss" if seconds else "HH:mm"
    return QLocale().toString(now, {"time": time, "date": "ddd, d. MMMM"}.get(style, "ddd, d. MMMM   " + time))


KEYBOARD_GLYPH = ""
SNIP_GLYPH = ""  # Schere


def language_label(short: str, layouts: int) -> str:
    """Text der Sprachanzeige; leer, wenn es nichts umzuschalten gibt oder Windows keinen Namen liefert."""
    return short if short and layouts > 1 else ""


# Symbole rechts in der Topbar: Kennung -> (Einstellung fürs Anzeigen, Name, Zeichen)
TOP_ITEMS = {
    "tray": ("show_tray", "Versteckte Symbole", G.CHEVRON_UP),
    "cloud": ("show_cloud", "Cloud-Synchronisierung", G.CLOUD),
    "mic": ("show_mic", "Mikrofon", G.MIC),
    "lang": ("show_language", "Tastatur-Sprache", KEYBOARD_GLYPH),
    "network": ("show_network", "Netzwerk", G.ETHERNET),
    "bluetooth": ("show_bluetooth", "Bluetooth", G.BLUETOOTH),
    "battery": ("show_battery", "Akku", G.BATTERY),
    "volume": ("show_volume", "Lautstärke", G.VOLUME),
    "project": ("show_project", "Bildschirm projizieren", G.DISPLAY),
    "snip": ("show_snip", "Ausschneiden (Screenshot)", SNIP_GLYPH),
    "quick": ("show_quick", "Schnelleinstellungen", G.QUICK),
    "nova": ("show_nova", "Nova", G.NOVA),
    "bell": ("show_notifications", "Benachrichtigungen", G.BELL),
}


# Diese Symbole gibt es nur, wenn der PC das Passende hat
TOP_NEEDS = {"mic": "microphone", "bluetooth": "bluetooth", "battery": "battery", "volume": "speakers"}
LOW_BATTERY = 20


def complete_order(order: list[str]) -> list[str]:
    """Gespeicherte Reihenfolge; Unbekanntes fällt weg, Neues kommt hinter seinen Vorgänger aus TOP_ITEMS."""
    known = [item for item in order if item in TOP_ITEMS]
    previous = None
    for item in TOP_ITEMS:
        if item not in known:
            known.insert(known.index(previous) + 1 if previous in known else len(known), item)
        previous = item
    return known


OUTPUT_ROWS = 7
TOP_GLIDE_MS = 170
# Rechtsklick öffnet bei diesen Symbolen dasselbe wie ein Linksklick (Bluetooth hat ein eigenes Rechtsklick-Ziel)
RIGHT_CLICK_OPENS = ("tray", "cloud", "mic", "network", "battery", "volume", "project", "quick", "nova", "bell")


class MicPopup(PopupPanel):
    """Wie das Lautstärke-Fenster, nur fürs Mikrofon: Pegel, stumm, Eingabegerät."""

    def __init__(self, ctx, theme_name: str):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(280)
        head = QHBoxLayout()
        self.label = QLabel("Mikrofon", objectName="PanelTitle")
        head.addWidget(self.label, 1)
        self.mute = IconButton(G.MIC, "Stumm schalten")
        self.mute.setCheckable(True)
        self.mute.clicked.connect(self._toggle_mute)
        head.addWidget(self.mute)
        self.body.addLayout(head)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self._changed)
        self.body.addWidget(self.slider)
        self.users = QLabel(objectName="Muted")
        self.users.setWordWrap(True)
        self.body.addWidget(self.users)
        self.inputs_title = QLabel("Eingabegerät", objectName="Muted")
        self.inputs = AudioOutputs(OUTPUT_ROWS, "in", hide_single=False)
        self.inputs.switched.connect(self.load)
        self.inputs.failed.connect(self.inputs_title.setText)
        self.body.addWidget(self.inputs_title)
        self.body.addWidget(self.inputs)
        privacy = QPushButton("Mikrofon-Einstellungen", objectName="Secondary")
        privacy.clicked.connect(lambda: (self.dismiss(), win_api.shell_open("ms-settings:sound")))
        self.body.addWidget(privacy)

    def load(self, users: list | None = None) -> None:
        if users is not None:
            self.users.setText("Gerade aktiv: " + ", ".join(users) if users else "Keine App nutzt das Mikrofon.")
        state = system_status.read_mic_volume()
        self.slider.setEnabled(state is not None)
        self.mute.setEnabled(state is not None)
        if state is None:
            self.label.setText("Kein Mikrofon gefunden")
            return
        value, muted = state
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.mute.setChecked(muted)
        self._title(value, muted)

    def _title(self, value: int, muted: bool) -> None:
        self.label.setText("Mikrofon stumm" if muted else f"Mikrofon {value} %")
        self.mute.setText(G.MIC_OFF if muted else G.MIC)
        self.mute.setToolTip("Stummschaltung aufheben" if muted else "Stumm schalten")

    def show_at(self, anchor) -> None:
        self.inputs_title.setText("Eingabegerät")
        self.inputs.refresh()
        self.inputs_title.setVisible(self.inputs.count() > 0)
        super().show_at(anchor)

    def _changed(self, value: int) -> None:
        try:
            system_status.write_mic_volume(value)
        except Exception as exc:  # Gerät eben abgesteckt
            self.label.setText(f"Mikrofon nicht erreichbar: {exc}")
            return
        self._title(value, self.mute.isChecked())

    def _toggle_mute(self, muted: bool) -> None:
        try:
            system_status.set_mic_muted(muted)
        except Exception as exc:
            self.label.setText(f"Mikrofon nicht erreichbar: {exc}")
            return
        self._title(self.slider.value(), muted)


class VolumePopup(PopupPanel):
    def __init__(self, ctx, theme_name: str):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(280)
        self.label = QLabel("Lautstärke", objectName="PanelTitle")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.valueChanged.connect(self._changed)
        self.body.addWidget(self.label)
        self.body.addWidget(self.slider)
        self.outputs_title = QLabel("Audiogeräte", objectName="Muted")
        self.outputs = AudioDevices(OUTPUT_ROWS)
        self.outputs.switched.connect(self._switched)
        self.outputs.failed.connect(self.outputs_title.setText)
        self.body.addWidget(self.outputs_title)
        self.body.addWidget(self.outputs)
        mixer = QPushButton("Lautstärke der Apps", objectName="Secondary")
        mixer.clicked.connect(lambda: (self.dismiss(), ctx.open_settings_at("Audio")))
        self.body.addWidget(mixer)

    def show_at(self, anchor) -> None:
        self.outputs_title.setText("Audiogeräte")
        self.outputs.refresh()
        super().show_at(anchor)

    def _switched(self) -> None:
        volume = system_status.read_volume()  # jedes Gerät hat seine eigene Lautstärke
        if volume:
            self.set_value(volume[0])

    def set_value(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.label.setText(f"Lautstärke {value} %")

    def _changed(self, value: int) -> None:
        system_status.write_volume(value)
        self.label.setText(f"Lautstärke {value} %")


class NetworkPopup(PopupPanel):
    def __init__(self, ctx, theme_name: str):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(280)
        self.title = QLabel(objectName="PanelTitle")
        self.name = QLabel(objectName="MediaTitle")
        self.detail = QLabel(objectName="Muted")
        self.detail.setWordWrap(True)
        self.wifi = QPushButton(objectName="Secondary")
        self.wifi.clicked.connect(self._toggle_wifi)
        test = QPushButton("Speedtest und Statistik", objectName="Secondary")
        test.clicked.connect(lambda: (self.close(), ctx.open_settings_at("Netzwerk")))
        button = QPushButton("Netzwerkeinstellungen", objectName="Secondary")
        button.clicked.connect(lambda: (self.close(), win_api.shell_open("ms-settings:network")))
        for widget in (self.title, self.name, self.detail, self.wifi, test, button):
            self.body.addWidget(widget)
        self._names: NameLoader | None = None
        self._shown_name = ""

    def _toggle_wifi(self) -> None:
        self.ctx.status.set_wifi(not self.ctx.status.state["wifi"])

    def show_at(self, anchor, bar_gap: int = 0) -> None:
        if self._names is None:  # dauert eine Sekunde, das Fenster soll trotzdem sofort da sein
            self._names = NameLoader(self)
            self._names.loaded.connect(self._name_loaded)
            self._names.start()
        super().show_at(anchor, bar_gap)

    def _name_loaded(self, name: str) -> None:
        self._names = None
        self._shown_name = name
        self.name.setText(name)
        self.name.setVisible(bool(name))

    def refresh(self, state: dict) -> None:
        kind = state["net_kind"]
        self.title.setText({"wifi": "WLAN", "ethernet": "Ethernet"}.get(kind, "Nicht verbunden"))
        lines = [state["net_name"]] if state["net_name"] else []
        if kind == "wifi":
            lines.append(_wifi_ssid())
        wifi = state["wifi"]  # am Kabel braucht es keinen WLAN-Schalter
        self.wifi.setVisible(wifi is not None and kind != "ethernet" and self.ctx.has_feature("wifi"))
        self.wifi.setText("WLAN ausschalten" if wifi else "WLAN einschalten")
        if kind != "none":
            lines.append("Internet erreichbar" if state["online"] else "Kein Internetzugang")
        self.detail.setText("\n".join(line for line in lines if line))


def _wifi_ssid() -> str:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=3,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode("cp850", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "SSID":
            return value.strip()
    return ""


class VolumeButton(IconButton):
    """Mausrad regelt die Lautstärke direkt, ohne das Popup zu öffnen."""

    def __init__(self, on_wheel):
        super().__init__(G.VOLUME, "Lautstärke")
        self._on_wheel = on_wheel

    def wheelEvent(self, event) -> None:
        self._on_wheel(2 if event.angleDelta().y() > 0 else -2)
        event.accept()


class BluetoothButton(IconButton):
    def contextMenuEvent(self, event) -> None:
        win_api.shell_open("ms-settings:bluetooth")


class TopBar(BarWindow):
    SIZE_KEY = "bar"
    AUTO_HIDE_FIELD = "topbar_auto_hide"

    def __init__(self, ctx, screen, edge: str, theme_name: str = ""):
        super().__init__(ctx, screen, edge, theme_name)
        self._volume_popup: VolumePopup | None = None
        self._network_popup: NetworkPopup | None = None
        self._quick_popup: QuickPanel | None = None
        self._window_popup: WindowPanel | None = None
        self._account_popup: AccountPanel | None = None
        self._mic_popup: MicPopup | None = None
        self._top_press: tuple[str, QPoint] | None = None
        self._top_drag = ""
        self._top_grab = QPoint()
        self._top_glides: dict = {}
        self._state: dict = {}
        self._workspace_signature = None

        row = QHBoxLayout(self.root)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(2)

        self.user = QPushButton(_user_name(), objectName="UserLabel")
        self.user.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.user.setToolTip("Benutzer")
        self.user.clicked.connect(self._user_clicked)
        self.workspaces = QHBoxLayout()
        self.workspaces.setSpacing(2)
        row.addWidget(self.user)
        row.addLayout(self.workspaces)
        row.addStretch(1)

        # Liegt über der Leiste statt in der Zeile: Sonst schieben die Symbole rechts den Text aus der Mitte.
        self.center = QLabel(self.root, objectName="CenterLabel")
        self.center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.chevron = IconButton(G.CHEVRON_UP, "Versteckte Symbole")
        self._tray_was_open = False
        self.chevron.pressed.connect(self._remember_tray)
        self.chevron.clicked.connect(self._toggle_tray)
        self.cloud = IconButton(G.CLOUD, "Cloud-Synchronisierung")
        self.cloud.clicked.connect(self._open_cloud)
        self.mic = IconButton(G.MIC, "Mikrofon wird nicht verwendet")
        self.mic.clicked.connect(self._show_mic)
        self.network = IconButton(G.ETHERNET, "Netzwerk")
        self.network.clicked.connect(self._show_network)
        self.bluetooth = BluetoothButton(G.BLUETOOTH, "Bluetooth")
        self.bluetooth.clicked.connect(self._toggle_bluetooth)
        self.battery = IconButton(G.BATTERY, "Akku")
        self.battery.clicked.connect(lambda: win_api.shell_open("ms-settings:batterysaver"))
        self.battery_level = QLabel(objectName="ClockDate")
        self.volume = VolumeButton(self._change_volume)
        self.volume.clicked.connect(self._show_volume)
        # Sprachanzeige wie „DEU“: zeigt die Tastatur des Fensters im Vordergrund, ein Klick schaltet weiter
        self.lang = QPushButton("", objectName="UserLabel")
        self.lang.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lang.setToolTip("Tastatur-Sprache wechseln")
        self.lang.clicked.connect(self._next_language)
        self._lang_window = 0
        self.project = IconButton(G.DISPLAY, "Bildschirm projizieren")
        self.project.clicked.connect(win_api.open_projection)
        self.snip = IconButton(SNIP_GLYPH, "Ausschneiden (Screenshot)")
        self.snip.clicked.connect(win_api.open_snipping)

        self.focus_time = QLabel(objectName="ClockDate")
        self.focus_time.hide()
        self.quick = IconButton(G.QUICK, "Schnelleinstellungen")
        self.quick.clicked.connect(self._show_quick)
        self.nova = NovaButton(ctx, NOVA_ICON)
        self.nova.clicked.connect(ctx.nova.toggle)
        self.bell = IconButton(G.BELL, "Benachrichtigungen")
        self.bell.clicked.connect(lambda: ctx.notification_panel.open_on(self.bar_screen))
        self.bell_count = QLabel(objectName="ClockDate")
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(2)
        row.addLayout(self.status_row)
        self._items = {"tray": [self.chevron], "cloud": [self.cloud], "mic": [self.mic], "lang": [self.lang], "network": [self.network],
                       "bluetooth": [self.bluetooth], "battery": [self.battery, self.battery_level], "volume": [self.volume],
                       "project": [self.project], "snip": [self.snip],
                       "quick": [self.focus_time, self.quick], "nova": [self.nova], "bell": [self.bell, self.bell_count]}
        self._arranged: list[str] = []
        self._layout = (self.bar_size(), self.auto_hides())
        # Ohne Eltern wäre jedes Icon kurz ein eigenes kleines Fenster, sobald es vor dem Einsortieren sichtbar wird
        for widgets in self._items.values():
            for widget in widgets:
                if widget.parent() is None:
                    widget.setParent(self.root)
                    widget.hide()

        self.clock = QWidget()
        self.clock_box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.clock)
        self.clock_box.setContentsMargins(8, 0, 2, 0)
        self.clock_box.setSpacing(0)
        self.time_label = QLabel(objectName="ClockTime")
        self.date_label = QLabel(objectName="ClockDate")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.clock_box.addWidget(self.time_label)
        self.clock_box.addWidget(self.date_label)
        row.addWidget(self.clock)

        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        ctx.status.updated.connect(self.update_status)
        ctx.focus.changed.connect(self._update_focus)
        ctx.config.changed.connect(self._on_config)
        ctx.notifications.updated.connect(self._update_bell)
        self._on_config("profile")
        self._update_focus()
        self.update_status(ctx.status.state)
        self._tick()

    def set_theme_name(self, name: str) -> None:
        self._volume_popup = self._network_popup = self._quick_popup = self._window_popup = None  # entstehen neu
        self._account_popup = self._mic_popup = None
        super().set_theme_name(name)

    def on_meta_changed(self, meta: dict) -> None:
        self._arrange_clock()
        self._show_cloud()

    def bar_size(self) -> int:
        return self.option("topbar_height") or super().bar_size()

    def contextMenuEvent(self, event) -> None:
        child = self.root.childAt(self.root.mapFromGlobal(event.globalPos()))
        key = self._item_key(child)
        if key in RIGHT_CLICK_OPENS and child.isEnabled():
            child.click()  # Rechtsklick auf ein Symbol öffnet sein Menü, nicht das der Leiste
            return
        menu = themed_menu(self.ctx, self, self.theme_name)
        action = menu.addAction("Automatisch ausblenden")
        action.setCheckable(True)
        action.setChecked(self.auto_hides())
        action.triggered.connect(lambda on: self.ctx.config.set_monitor(self.screen_id, "topbar_auto_hide", on))
        menu.addAction("Topbar-Einstellungen", lambda: self.ctx.open_settings_at("Topbar"))
        menu.popup(event.globalPos())
        win_api.force_foreground(int(menu.winId()))

    def _arrange_clock(self) -> None:
        one_line = self.meta()["compact"] == "1" or self.option("clock_lines") == "one"
        self.clock_box.setDirection(QBoxLayout.Direction.LeftToRight if one_line else QBoxLayout.Direction.TopToBottom)
        self.clock_box.setSpacing(6 if one_line else 0)
        self.clock.setVisible(self.option("clock_place") == "top_right")
        self.time_label.setVisible(self.option("clock_style") != "date")
        self.date_label.setVisible(self.option("clock_style") != "time")

    def _arrange_items(self, order: list[str] | None = None, animate: bool = False) -> None:
        order = complete_order(order if order is not None else self.option("top_order"))
        if order == self._arranged:
            return
        before = {widget: widget.pos() for widgets in self._items.values() for widget in widgets if widget.isVisible()}
        self._arranged = order
        for item in order:
            for widget in self._items[item]:
                self.status_row.removeWidget(widget)
                self.status_row.addWidget(widget)
                if isinstance(widget, QAbstractButton) and widget.property("topDrag") is None:
                    widget.setProperty("topDrag", True)
                    widget.installEventFilter(self)  # mit der Maus verschiebbar, wie die Knöpfe der Taskbar
        if animate and duration_of(self.ctx.config):
            self.root.layout().activate()
            held = self._items[self._top_drag][0] if self._top_drag else None
            for widget, old in before.items():
                if widget is not held:
                    self._top_glide(widget, old)

    # --- Symbole oben umstellen ---------------------------------------------------------------------
    def _item_key(self, widget) -> str:
        if not isinstance(widget, QAbstractButton):
            return ""
        return next((key for key, widgets in self._items.items() if widget in widgets), "")

    def _top_glide(self, widget: QWidget, start: QPoint) -> None:
        end = widget.pos()
        if start == end:
            return
        animation = self._top_glides.get(widget)
        if animation is None:
            animation = self._top_glides[widget] = QPropertyAnimation(widget, b"pos", self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setDuration(TOP_GLIDE_MS)
        animation.stop()
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.start()

    def eventFilter(self, watched, event) -> bool:
        key = self._item_key(watched)
        if not key:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._top_press = (key, event.globalPosition().toPoint())
        elif kind == QEvent.Type.MouseMove and self._top_press and self._top_press[0] == key:
            point = event.globalPosition().toPoint()
            if not self._top_drag and (point - self._top_press[1]).manhattanLength() >= QApplication.startDragDistance():
                self._top_drag = key
                self._top_grab = point - watched.mapToGlobal(QPoint(0, 0))
                watched.raise_()
                watched.setDown(False)
            if self._top_drag:
                self._drag_top(watched, point)
                return True
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            dragged = self._top_drag
            self._top_press = None
            if dragged:
                self._finish_top(watched)
                return True  # kein Klick nach dem Ziehen
        return super().eventFilter(watched, event)

    def _drag_top(self, widget: QWidget, point: QPoint) -> None:
        local = widget.parentWidget().mapFromGlobal(point - self._top_grab)
        widget.move(local.x(), widget.y())
        mine = widget.mapToGlobal(widget.rect().center()).x()
        centers = []
        for key in self._arranged:
            if key == self._top_drag:
                continue
            shown = [w for w in self._items[key] if w.isVisible()]
            if shown:
                centers.append((key, shown[0].mapToGlobal(shown[0].rect().center()).x()))
        place = sum(mine > x for _key, x in centers)
        anchor = centers[place - 1][0] if place > 0 else None
        order = [key for key in self._arranged if key != self._top_drag]
        order.insert(order.index(anchor) + 1 if anchor else 0, self._top_drag)
        if order != self._arranged:
            held = widget.pos()
            self._arrange_items(order, animate=True)
            widget.move(held)  # bleibt unter der Maus, die anderen machen Platz

    def _finish_top(self, widget: QWidget) -> None:
        start = widget.pos()
        self._top_drag = ""
        self.root.layout().activate()
        self._top_glide(widget, start)
        if self._arranged != complete_order(self.option("top_order")):
            self.ctx.config.set_monitor(self.screen_id, "top_order", list(self._arranged))

    def _show_cloud(self) -> None:
        running = bool(self._state.get("cloud"))
        self.cloud.setVisible(running and self.option("show_cloud") and self.meta()["compact"] != "1")

    def item_on(self, item: str) -> bool:
        return self.option(TOP_ITEMS[item][0]) and (item not in TOP_NEEDS or self.ctx.has_feature(TOP_NEEDS[item]))

    def _apply_items(self) -> None:
        self.mic.setVisible(self.item_on("mic"))
        self.volume.setVisible(self.item_on("volume"))
        self.bluetooth.setVisible(self.item_on("bluetooth") and self._state.get("bluetooth") is not None)
        battery = self._state.get("battery")
        self.battery.setVisible(self.item_on("battery") and battery is not None)
        self.battery_level.setVisible(self.battery.isVisibleTo(self))
        if battery:
            percent, charging = battery
            self.battery.setText(hardware.battery_glyph(percent, charging))
            self.battery_level.setText(f"{percent}%")
            set_prop(self.battery, "alert", percent < LOW_BATTERY and not charging)
            self.battery.setToolTip(f"Akku {percent} %, lädt" if charging else f"Akku {percent} %")

    def _on_config(self, key: str) -> None:
        if key == "nova.enabled":
            key = "profile"
        if key == "hide_missing_hardware":
            self._apply_items()
        if key == "profile":
            self.user.setVisible(self.option("show_user"))
            self.quick.setVisible(self.option("show_quick"))
            self.nova.setVisible(self.option("show_nova") and self.ctx.nova_enabled())
            self.chevron.setVisible(self.option("show_tray"))
            self.network.setVisible(self.option("show_network"))
            self.project.setVisible(self.option("show_project"))
            self.snip.setVisible(self.option("show_snip"))
            self._update_language()
            self._apply_items()
            self._show_cloud()
            self._arrange_items()
            self._arrange_clock()
            layout = (self.bar_size(), self.auto_hides())
            if self._registered and layout != self._layout:
                self._layout = layout
                self.reposition()
                self._sync_auto_hide()
            self.user.setEnabled(self.option("show_windows"))
            self.user.setToolTip("Benutzer" if self.option("user_click") == "account" else "Aktive Fenster")
            self._workspace_signature = None
            self._tick()
            self._update_workspaces(self.ctx.status.state["desktops"])
        if key == "profile" or key.startswith("notifications."):
            self._update_bell()

    def _update_bell(self) -> None:
        shown = self.option("show_notifications")
        count = len(visible_notices(self.ctx.notifications.notices, self.ctx.config.get("notifications.hidden")))
        self.bell.setVisible(shown)
        self.bell.setToolTip(f"Benachrichtigungen: {count}")
        self.bell_count.setText(str(count))
        self.bell_count.setVisible(shown and count > 0 and self.ctx.config.get("notifications.show_count"))

    def _update_language(self) -> None:
        hwnd = win_api.foreign_foreground()
        if hwnd:
            self._lang_window = hwnd  # beim Klick auf die Topbar zählt das Fenster davor
        text = ""
        if self.option("show_language") and self._lang_window:
            short = win_api.language_short(win_api.keyboard_language(self._lang_window))
            text = language_label(short, win_api.keyboard_language_count())
        if self.lang.text() != text:
            self.lang.setText(text)
        self.lang.setVisible(bool(text))

    def _next_language(self) -> None:
        if win_api.next_keyboard_language(self._lang_window):
            QTimer.singleShot(300, self._update_language)

    def peek(self) -> None:
        super().peek()
        self._tick()

    def _tick(self) -> None:
        if self.behind_game():
            return  # im Spiel nicht jede Sekunde Uhr, Titel und Sprache abfragen
        self._update_language()
        now, date = format_clock(QDateTime.currentDateTime(), self.option("clock_seconds"))
        self.time_label.setText(now)
        self.date_label.setText(date)
        mode = self.option("center")
        if self.option("clock_place") == "top_center":
            self.center.setText(format_center(QDateTime.currentDateTime(), self.option("clock_seconds"), self.option("clock_style")))
        elif mode == "title":
            # Nur dort, wo das Fenster liegt: Der Titel gehört nicht auf die anderen Bildschirme.
            here = win_api.foreground_on(native_rect(self.bar_screen))
            title = win_api.foreground_title() if here else ""
            self.center.setText(self.center.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, 520))
        else:
            self.center.clear()
        self._place_center()

    def _place_center(self) -> None:
        width = self.center.sizeHint().width()
        self.center.setGeometry((self.root.width() - width) // 2, 0, width, self.root.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_center()

    def update_status(self, state: dict) -> None:
        self._state = state
        users = state["mic"]
        set_prop(self.mic, "active", bool(users))
        set_prop(self.mic, "off", not users)
        self.mic.setToolTip("Mikrofon aktiv: " + ", ".join(users) if users else "Mikrofon wird nicht verwendet")

        cloud = state["cloud"]
        self._show_cloud()
        self.cloud.setToolTip("Läuft: " + ", ".join(cloud))

        kind = state["net_kind"]
        self.network.setText({"wifi": G.WIFI, "ethernet": G.ETHERNET}.get(kind, G.NO_NET))
        offline = kind == "none" or state["online"] is False
        set_prop(self.network, "alert", offline)
        self.network.setToolTip("Keine Internetverbindung" if offline else f"Verbunden: {state['net_name']}")

        bluetooth = state["bluetooth"]
        self._apply_items()
        set_prop(self.bluetooth, "off", not bluetooth)
        self.bluetooth.setToolTip("Bluetooth an" if bluetooth else "Bluetooth aus")

        volume = state["volume"]
        muted = state["muted"] or volume == 0
        self.volume.setText(hardware.volume_glyph(volume, muted))
        self.volume.setToolTip("Stumm" if state["muted"] else f"Lautstärke {volume} %")
        if self._volume_popup and self._volume_popup.isVisible() and not self._volume_popup.slider.isSliderDown():
            self._volume_popup.set_value(volume or 0)
        self._update_workspaces(state["desktops"])

    def _update_workspaces(self, desktops: tuple[int, int]) -> None:
        visible = self.option("show_workspaces")
        signature = (desktops, visible)
        if signature == self._workspace_signature:
            return
        self._workspace_signature = signature
        while self.workspaces.count():
            self.workspaces.takeAt(0).widget().deleteLater()
        count, current = desktops
        if not visible or count < 2:  # bei nur einem Desktop gibt es nichts zu wechseln
            return
        for index in range(count):
            chip = QPushButton(str(index + 1), objectName="WorkspaceChip")
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.setProperty("current", index == current)
            chip.setToolTip(f"Arbeitsfläche {index + 1}")
            chip.clicked.connect(lambda _=False, i=index: win_api.switch_virtual_desktop(i - current))
            self.workspaces.addWidget(chip)

    def _user_clicked(self) -> None:
        """Oben links: Benutzer-Bereich (Standard) oder wie früher gleich die offenen Fenster."""
        if self.option("user_click") == "windows":
            self._show_window_menu()
            return
        if self._account_popup is None:
            self._account_popup = AccountPanel(self.ctx, self.theme_name, self._show_window_menu)
        self._account_popup.open_at(self.user)

    def _show_window_menu(self) -> None:
        if self._window_popup is None:
            self._window_popup = WindowPanel(self.ctx, self.theme_name)
        self._window_popup.open_at(self.user)

    def _show_quick(self) -> None:
        if self._quick_popup is None:
            self._quick_popup = QuickPanel(self.ctx, self.theme_name)
        self._quick_popup.toggle_at(self.quick, self._quick_popup.refresh)

    def _update_focus(self) -> None:
        focus = self.ctx.focus
        set_prop(self.quick, "active", focus.active)
        self.focus_time.setVisible(focus.active)
        self.focus_time.setText(format_remaining(focus.remaining))
        self.quick.setToolTip(f"Fokus: noch {format_remaining(focus.remaining)}" if focus.active
                              else "Schnelleinstellungen")

    def _remember_tray(self) -> None:
        # Muss beim Drücken feststehen: Windows schließt das Fenster oft schon vor dem Loslassen.
        self._tray_was_open = bool(win_api.tray_overflow_window())

    def _toggle_tray(self) -> None:
        if self._tray_was_open:
            # Nie erneut öffnen: Enter würde sonst das markierte Tray-Symbol auslösen.
            if win_api.tray_overflow_window():
                win_api.close_tray_overflow()
            return
        QTimer.singleShot(0, win_api.open_tray_overflow)
        self._place_tray(0)

    def _place_tray(self, tick: int) -> None:
        """Windows öffnet den Überlauf an der ausgeblendeten Taskleiste - wir holen ihn unter den Pfeil.

        Mehrfach, weil Explorer die Position während der Einblend-Animation noch einmal setzt.
        """
        hwnd = win_api.tray_overflow_window()
        if hwnd:
            win_api.move_window(hwnd, *self._tray_position(hwnd))
        if tick < 40:
            QTimer.singleShot(20, lambda: self._place_tray(tick + 1))

    def _tray_position(self, hwnd: int) -> tuple[int, int]:
        left, top, right, bottom = win_api.window_rect(hwnd)
        width, height = right - left, bottom - top
        corner = self.chevron.mapToGlobal(self.chevron.rect().bottomRight())
        screen = self.bar_screen
        anchor_x, anchor_y = native_point(screen, corner.x(), corner.y())
        monitor = native_rect(screen)
        x = max(monitor[0] + 4, min(anchor_x - width, monitor[2] - width - 4))
        if self.edge == "top":
            return x, anchor_y + 8
        chevron_top = self.chevron.mapToGlobal(self.chevron.rect().topLeft())
        return x, native_point(screen, chevron_top.x(), chevron_top.y())[1] - height - 8

    def _open_cloud(self) -> None:
        folder = os.environ.get("OneDrive")
        if folder and os.path.isdir(folder):
            launch(self.ctx, folder)

    def _toggle_bluetooth(self) -> None:
        self.ctx.status.set_bluetooth(not self._state.get("bluetooth"))

    def _change_volume(self, delta: int) -> None:
        value = (self._state.get("volume") or 0) + delta
        value = max(0, min(100, value))
        self._state["volume"] = value
        system_status.write_volume(value)
        self.volume.setToolTip(f"Lautstärke {value} %")

    def _show_volume(self) -> None:
        if self._volume_popup is None:
            self._volume_popup = VolumePopup(self.ctx, self.theme_name)
        self._volume_popup.set_value(self._state.get("volume") or 0)
        self._volume_popup.toggle_at(self.volume)

    def _show_mic(self) -> None:
        if self._mic_popup is None:
            self._mic_popup = MicPopup(self.ctx, self.theme_name)
        self._mic_popup.load(list(self._state.get("mic") or []))
        self._mic_popup.toggle_at(self.mic)

    def _show_network(self) -> None:
        if self._network_popup is None:
            self._network_popup = NetworkPopup(self.ctx, self.theme_name)
        self._network_popup.refresh(self._state)
        self._network_popup.toggle_at(self.network)
