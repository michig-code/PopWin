"""Gemeinsame Bausteine: Theme-Anbindung, AppBar-Leisten, Popups, Symbole."""
from __future__ import annotations

import ctypes
import os
import shutil
import time
from ctypes import wintypes

import shiboken6
from PySide6.QtCore import QPointF, QSize, QEvent, QFileInfo, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QHBoxLayout, QMenu, QPushButton, QScrollArea, QScrollBar,
    QVBoxLayout, QWidget,
)

from core import win_api
from core.monitor_manager import native_rect, screen_key
from ui.animation import WindowAnimator, animate_menu

ICON_FONT = ("Segoe Fluent Icons", "Segoe MDL2 Assets")


class G:
    """Zeichen der Windows-Symbolschrift."""
    SIGN_OUT = ""
    CHEVRON_UP = ""
    CLOUD = ""
    MIC = ""
    MIC_OFF = ""
    ETHERNET = ""
    WIFI = ""
    NO_NET = ""
    BATTERY = "\uE83F"
    BLUETOOTH = ""
    VOLUME = ""
    MUTE = ""
    START = ""
    SEARCH = ""
    SETTINGS = ""
    POWER = ""
    RESTART = ""
    LOCK = ""
    SLEEP = ""
    PLAY = ""
    PAUSE = ""
    NEXT = ""
    PREV = ""
    TRASH = ""
    REFRESH = ""
    TERMINAL = ""
    NEW = ""
    FOLDER = ""
    DOC = ""
    DISPLAY = ""
    PERSONALIZE = ""
    APP = ""
    WINDOWS = ""
    CLOSE = ""
    NOTE = ""
    CLOCK = ""
    CPU = ""
    MUSIC = ""
    PIN = ""
    NOVA = "\uECCB"  # Orbit mit Planet in der Mitte; als Schriftzeichen folgt es hell und dunkel von selbst
    RESET = ""
    MEMORY = ""
    GPU = ""
    NETWORK = ""
    DISK = ""
    LIST = ""
    CALENDAR = ""
    STOPWATCH = ""
    UPTIME = ""
    ADD = ""
    QUICK = ""
    DARK = ""
    QUIET = ""
    BELL = ""
    WIDGETS = ""
    SEND = "\ue724"
    STOP = "\ue71a"
    ANIMATION = ""


def resettable(editor: QWidget, reset) -> QWidget:
    """Feld mit Knopf daneben, der es auf den Standardwert zurücksetzt."""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(editor, 1)
    button = IconButton(G.RESET, "Auf Standard zurücksetzen")
    button.clicked.connect(lambda: reset())
    row.addWidget(button)
    return host


def set_prop(widget: QWidget, name: str, value) -> None:
    """Setzt eine QSS-Eigenschaft und zeichnet das Widget nur bei echter Änderung neu."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def _paint_orbit(painter: QPainter, size: int, color: str) -> None:
    """Novas Zeichen: Planet in der Mitte, Ring außen, ein kleiner Mond auf dem Ring. Einfarbig, wie die übrigen Symbole."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    middle = QPointF(size / 2, size / 2)
    ring = size * 0.40
    moon = middle + QPointF(ring * 0.707, -ring * 0.707)
    painter.setPen(QPen(QColor(color), max(1.2, size * 0.075)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(middle, ring, ring)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(middle, size * 0.19, size * 0.19)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)  # Lücke im Ring, damit der Mond frei steht
    painter.drawEllipse(moon, size * 0.17, size * 0.17)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.drawEllipse(moon, size * 0.105, size * 0.105)


def glyph_icon(glyph: str, size: int, color: str) -> QIcon:
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    if glyph == G.NOVA:
        _paint_orbit(painter, size, color)
    else:
        font = QFont(ICON_FONT[0])
        font.setFamilies(list(ICON_FONT))
        font.setPixelSize(size)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return QIcon(pixmap)


_icon_cache: dict[str, QIcon] = {}


def icon_for_target(target: str) -> QIcon:
    """Icon einer .exe, einer Verknüpfung oder eines Befehls; leer, wenn es keine Datei gibt."""
    if target in _icon_cache:
        return _icon_cache[target]
    # --- [Antigravity Start] Icons für interne PopWin-Pins (z. B. popwin:dev) ---
    if target.startswith("popwin:"):
        from core.replacements import app_icon
        app_name = target.removeprefix("popwin:")
        icon_file = app_icon(app_name)
        if os.path.exists(icon_file):
            icon = QIcon(icon_file)
            _icon_cache[target] = icon
            return icon
    # --- [Antigravity Ende] ---
    packaged = win_api.packaged_app_target(target)
    if packaged or target.lower().startswith("shell:appsfolder"):
        icon = _take_icon(win_api.shell_item_hicon(packaged or target))
    else:
        path = target if os.path.exists(target) else shutil.which(target)
        if path and path.lower().endswith(".lnk"):
            path = win_api.shortcut_icon_file(path) or path
        if path and path.lower().endswith(".ico"):
            icon = QIcon(path)
            if not _has_pixels(icon):
                icon = _take_icon(win_api.file_hicon(target))  # Installer-Icons kann Qt nicht immer lesen, Windows schon
        else:
            icon = _take_icon(win_api.file_hicon(path)) if path else QIcon()
    _icon_cache[target] = icon
    return icon


def _has_pixels(icon: QIcon) -> bool:
    image = icon.pixmap(32, 32).toImage()
    return any(image.pixelColor(x, y).alpha() for x in range(0, image.width(), 3) for y in range(0, image.height(), 3))


def _take_icon(handle: int) -> QIcon:
    image = QImage.fromHICON(handle) if handle else QImage()
    if handle:
        win_api.destroy_icon(handle)
    return QIcon(QPixmap.fromImage(image)) if not image.isNull() else QIcon()


class ThemedWindow(QWidget):
    """Top-Level-Fenster, das Theme, Modus und Blur automatisch nachzieht."""
    THEMED_CHILDREN = ("Bar", "Panel", "Desklet")

    def __init__(self, ctx, flags, theme_name: str = "", translucent: bool = True):
        super().__init__(None, flags)
        self.ctx = ctx
        self.theme_name = theme_name
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, translucent)
        ctx.theme.changed.connect(self.apply_theme)

    def apply_theme(self) -> None:
        theme = self.ctx.theme
        # Listen, Dialoge und Tooltips malen mit den Grundfarben von Qt, und die folgen sonst Windows statt PopWin.
        scheme = Qt.ColorScheme.Light if theme.mode == "light" else Qt.ColorScheme.Dark
        hints = QGuiApplication.styleHints()
        if hints.colorScheme() != scheme:
            hints.setColorScheme(scheme)
        self.setProperty("popMode", theme.mode)
        for child in self.findChildren(QWidget):
            if child.objectName() in self.THEMED_CHILDREN:
                child.setProperty("popMode", theme.mode)
        self.setStyleSheet(theme.stylesheet(self.theme_name))
        meta = theme.meta(self.theme_name)
        hwnd = int(self.winId())
        win_api.set_blur(hwnd, meta["blur"], theme.tint(self.theme_name))
        win_api.set_rounded(hwnd, meta["rounded"] == "1")

    def meta(self) -> dict:
        return self.ctx.theme.meta(self.theme_name)


class IconButton(QPushButton):
    def __init__(self, glyph: str, tooltip: str = "", parent: QWidget | None = None, name: str = "IconButton"):
        super().__init__(glyph, parent)
        self.setObjectName(name)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class NovaButton(IconButton):
    """Ein gezeichnetes Symbol statt eines Schriftzeichens; die Farbe zieht es deshalb selbst nach."""

    def __init__(self, ctx, size: int, name: str = "IconButton"):
        super().__init__("", "Nova", name=name)
        self.ctx = ctx
        self.setIconSize(QSize(size, size))
        ctx.theme.changed.connect(self._tint)
        self._tint()

    def _tint(self) -> None:
        size = self.iconSize().width()
        self.setIcon(glyph_icon(G.NOVA, size, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"))


# Stufe -> (Pixel vom Rand, Sekunden, die die Maus dort bleiben muss)
PEEK_SECONDS = 1.5  # so lange bleibt die Leiste im Vollbild mindestens vorn
EDGE_LEVELS = {1: (1, 0.45), 2: (2, 0.3), 3: (6, 0.15), 4: (16, 0.06), 5: (40, 0.0)}


def edge_reveals(hot: bool, since: float | None, now: float, level: int) -> tuple[bool, float | None]:
    """Soll die Leiste erscheinen? Liefert das und ab wann die Maus am Rand steht."""
    if not hot:
        return False, None
    start = now if since is None else since
    return now - start >= EDGE_LEVELS.get(level, EDGE_LEVELS[3])[1], start


def in_hot_zone(x: int, y: int, area: tuple[int, int, int, int], edge: str, depth: int) -> bool:
    """Liegt der Mauszeiger am Rand, an dem die Leiste aufklappt?"""
    left, top, right, bottom = area
    # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
    if edge in ("left", "right"):
        if not top <= y < bottom:
            return False
        return left <= x < left + depth if edge == "left" else right - depth <= x < right
    # --- [Antigravity Ende: Sidebar Support] ---
    if not left <= x < right:
        return False
    return top <= y < top + depth if edge == "top" else bottom - depth <= y < bottom


class ScrollFields(QObject):
    """Das Mausrad scrollt die Seite. Es verstellt keine Felder, über denen der Zeiger zufällig gerade steht."""
    _installed: "ScrollFields | None" = None

    @classmethod
    def install(cls) -> None:
        if cls._installed is None:
            cls._installed = cls(QApplication.instance())
            QApplication.instance().installEventFilter(cls._installed)

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.Wheel or isinstance(watched, QScrollBar) or not isinstance(watched, QWidget):
            return False
        # Bei Zahlenfeldern trifft das Rad zuerst das Textfeld im Inneren.
        fields = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        if not isinstance(watched, fields) and not isinstance(watched.parentWidget(), fields):
            return False
        parent = watched.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            return False
        QApplication.sendEvent(parent.verticalScrollBar(), event)
        return True


class BarWindow(ThemedWindow):
    """Leiste, die sich per SHAppBarMessage ihren Platz reserviert.

    Der Desktop selbst wird nie angefasst - Wallpaper Engine läuft darunter unverändert weiter.
    """
    SIZE_KEY = "bar"
    AUTO_HIDE_FIELD = "auto_hide"
    _APPBAR_MESSAGE = 0x8000 + 0x50
    _TASKBAR_CREATED = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")

    def __init__(self, ctx, screen, edge: str, theme_name: str = ""):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(ctx, flags, theme_name)
        self._screen = screen
        self.screen_id = screen_key(screen)
        self.edge = edge
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._registered = False
        self._rect = None
        self._area = None
        self._placed = None
        self.slide_offset = 0  # so weit steht die Leiste gerade neben ihrem Platz, in echten Pixeln
        self._revealed = True
        self._hide_at = 0.0
        self._hot_since: float | None = None
        self._edge_timer = QTimer(self, interval=70)
        self._edge_timer.timeout.connect(self._watch_edge)
        self.animator = WindowAnimator(ctx, self, self._shift)
        self._reposition_timer = QTimer(self, singleShot=True, interval=120)
        self._reposition_timer.timeout.connect(self.reposition)
        self._fullscreen = False
        self._peek_until = 0.0
        self._on_top = True
        self._fullscreen_timer = QTimer(self, interval=700)
        self._fullscreen_timer.timeout.connect(self._check_fullscreen)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.root = QWidget(self, objectName="Bar")
        self.root.setProperty("popEdge", edge)
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(self.root)

    @property
    def bar_screen(self):
        """Windows ersetzt Bildschirme beim Umschalten von Monitoren, das alte Objekt ist dann tot.
        Ohne diesen Ersatz schlägt jede Aktualisierung fehl und die Leiste bleibt in falscher Breite stehen."""
        if not shiboken6.isValid(self._screen):
            self._screen = self.ctx.monitors.by_key(self.screen_id) or QGuiApplication.primaryScreen()
        return self._screen

    def option(self, field: str):
        """Wert aus dem Profil dieses Monitors - im Sync-Modus also dem gemeinsamen."""
        return self.ctx.config.monitor(self.screen_id)[field]

    def _shift(self, offset: int) -> None:
        self.slide_offset = offset
        if self._placed:
            x, y, width, height = self._placed
            # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
            if self.edge in ("left", "right"):
                win_api.place_window(int(self.winId()), x + offset, y, width, height)
                return
            # --- [Antigravity Ende: Sidebar Support] ---
            win_api.place_window(int(self.winId()), x, y + offset, width, height)

    def _fade_in(self) -> None:
        self.animator.enter(1.0, slide=-12 if self.edge in ("top", "left") else 12)  # [Antigravity: Sidebar Support]

    # --- Auto-Hide ---------------------------------------------------------------
    def auto_hides(self) -> bool:
        return self.option(self.AUTO_HIDE_FIELD)

    def reveal(self) -> None:
        self._sync_auto_hide(first=True)

    def _sync_auto_hide(self, first: bool = False) -> None:
        if self.auto_hides():
            self._edge_timer.start()
            self._revealed = False
            if first:
                self.hide()
            else:
                self.animator.leave()
        else:
            self._edge_timer.stop()
            self._revealed = True
            # [Antigravity: Auch mitten im Ausblenden (closing) wieder einblenden]
            if not self.isVisible() or self.animator.closing or first:
                self.show()
                self._fade_in()

    def show_then(self, action) -> None:
        """Blendet die Leiste ein und ruft `action` sofort auf: Menüs rechnen ihre Verschiebung selbst heraus."""
        if not self.auto_hides() or (self._revealed and self.isVisible()):
            action()
            return
        self._hide_at = time.monotonic() + 1.0
        self._revealed = True
        self.show()
        win_api.set_zorder(int(self.winId()), win_api.HWND_TOPMOST)
        self._fade_in()
        action()

    def _keeps_open(self) -> bool:
        panel = PopupPanel._open_panel
        return QApplication.activePopupWidget() is not None or (panel is not None and panel.isVisible())

    def _watch_edge(self) -> None:
        """Zeigt die Leiste, solange die Maus am Rand oder auf ihr ist."""
        if self._fullscreen and not self.peeking():
            return
        cursor = QCursor.pos()
        screen = self.bar_screen.geometry()
        ratio = self.bar_screen.devicePixelRatio()
        x = screen.x() + round((cursor.x() - screen.x()) * ratio)
        y = screen.y() + round((cursor.y() - screen.y()) * ratio)
        level = self.option("edge_level")
        hot = in_hot_zone(x, y, native_rect(self.bar_screen), self.edge, round(EDGE_LEVELS.get(level, EDGE_LEVELS[3])[0] * ratio))
        busy = self.frameGeometry().contains(cursor) or self._keeps_open()
        now = time.monotonic()
        hot, self._hot_since = edge_reveals(hot, self._hot_since, now, level)
        if hot or (self._revealed and busy):
            self._hide_at = now + 0.5
            if not self._revealed:
                self._revealed = True
                self.show()
                win_api.set_zorder(int(self.winId()), win_api.HWND_TOPMOST)
                self._fade_in()
        elif self._revealed and now >= self._hide_at:
            self._revealed = False
            self.animator.leave()

    def dock(self) -> None:
        self.setScreen(self.bar_screen)
        self.setWindowOpacity(0)  # unsichtbar andocken; reveal() blendet alle Leisten gemeinsam ein
        self.show()
        self.apply_theme()
        win_api.appbar_register(int(self.winId()), self._APPBAR_MESSAGE)
        self._registered = True
        self.reposition()
        self._fullscreen_timer.start()

    def _check_fullscreen(self) -> None:
        # Eigene Prüfung statt ABN_FULLSCREENAPP: Das Signal unterscheidet Monitore nicht zuverlässig,
        # und ein Spiel auf Monitor 1 soll die Leisten auf Monitor 2 nicht wegnehmen.
        self._enforce_placement()
        self._fullscreen = win_api.fullscreen_on(native_rect(self.bar_screen))
        self._place_in_z()

    def peek(self) -> None:
        """Windows-Taste im Vollbild: Die Leiste kommt nach vorn, solange das Home-Menü offen ist oder die Maus auf ihr liegt."""
        self._peek_until = time.monotonic() + PEEK_SECONDS
        if self._fullscreen:
            self.show()
            self.setWindowOpacity(1)
        self._place_in_z()

    def peeking(self) -> bool:
        if time.monotonic() < self._peek_until:
            return True
        if self._peek_until and (self._keeps_open() or self.frameGeometry().contains(QCursor.pos())):
            return True
        self._peek_until = 0.0
        return False

    def _place_in_z(self) -> None:
        on_top = not self._fullscreen or self.peeking()
        if on_top != self._on_top:
            self._on_top = on_top
            win_api.set_zorder(int(self.winId()), win_api.HWND_TOPMOST if on_top else win_api.HWND_BOTTOM)

    def undock(self) -> None:
        self._fullscreen_timer.stop()
        if self._registered:
            self._registered = False
            win_api.appbar_remove(int(self.winId()))
        self.hide()

    def reposition(self) -> None:
        if not self._registered:
            return
        monitor = native_rect(self.bar_screen)
        ratio = self.bar_screen.devicePixelRatio()
        size = round(self.bar_size() * ratio)
        margin = round(self.bar_margin() * ratio)
        hwnd = int(self.winId())
        # Der Abstand gilt auf beiden Seiten: zum Bildschirmrand und zu den Fenstern.
        # Ohne Reservierung gehen Fenster bis zum Rand und die Leiste schwebt darüber.
        thickness = size + 2 * margin if self.reserves_space() else 0
        rect = win_api.appbar_set_pos(hwnd, self.edge, monitor, thickness, self._rect)
        self._rect = rect
        left, top, right, bottom = rect if thickness else monitor
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.edge in ("left", "right"):
            x = left + margin if self.edge == "left" else right - size - margin
            self._area = (top + margin, bottom - margin, x, size)
            self.apply_geometry()
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        y = top + margin if self.edge == "top" else bottom - size - margin
        self._area = (left + margin, right - margin, y, size)
        self.apply_geometry()

    def reserves_space(self) -> bool:
        return not self.auto_hides()

    def bar_size(self) -> int:
        return int(self.meta()[self.SIZE_KEY])

    def bar_margin(self) -> int:
        return int(self.meta()["margin"])

    def apply_geometry(self) -> None:
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        if self.edge in ("left", "right"):
            top, bottom, x, size = self._area
            self._place(x, top, size, bottom - top)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        left, right, y, size = self._area
        self._place(left, y, right - left, size)

    def _place(self, x: int, y: int, width: int, height: int) -> None:
        if (x, y, width, height) != self._placed:
            self._placed = (x, y, width, height)
            win_api.place_window(int(self.winId()), x, y, width, height)
            # Qt erfährt die Größe sonst erst später und stellt beim nächsten Layout den alten Wert her.
            ratio = self.bar_screen.devicePixelRatio()
            self.resize(round(width / ratio), round(height / ratio))

    def _enforce_placement(self) -> None:
        """Holt die Leiste zurück, falls Qt oder ein anderes Programm sie verändert hat."""
        if not self._placed or self.animator.running or not self.isVisible():
            return
        x, y, width, height = self._placed
        if win_api.window_rect(int(self.winId())) != (x, y, x + width, y + height):
            win_api.place_window(int(self.winId()), x, y, width, height)
        # --- [Antigravity Start] Qt-Fenstergröße mit nativer Platzierung synchron halten ---
        ratio = self.bar_screen.devicePixelRatio()
        wanted = QSize(round(width / ratio), round(height / ratio))
        if self.size() != wanted:
            self.resize(wanted)
        # --- [Antigravity Ende] ---

    def set_theme_name(self, name: str) -> None:
        self.theme_name = name
        self.apply_theme()

    def apply_theme(self) -> None:
        super().apply_theme()
        self.on_meta_changed(self.meta())
        self.reposition()

    def on_meta_changed(self, meta: dict) -> None:
        pass

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == self._APPBAR_MESSAGE:
            if msg.wParam == win_api.ABN_POSCHANGED:
                self._reposition_timer.start()
        elif msg.message == self._TASKBAR_CREATED and self._registered:
            win_api.appbar_register(int(self.winId()), self._APPBAR_MESSAGE)
            self._rect = None
            self._reposition_timer.start()
        return False, 0

    def contextMenuEvent(self, event) -> None:
        self.ctx.show_context_menu(event.globalPos(), desktop=False)


MENU_SIZES = {"kompakt": (3, 8), "normal": (6, 10), "gross": (9, 14)}


def style_menu(ctx, menu: QMenu, theme_name: str = "") -> None:
    """Das Aussehen aus den Einstellungen unter Kontextmenü, für jedes Rechtsklickmenü von PopWin gleich."""
    config = ctx.config.get("context_menu")
    theme = ctx.theme
    name = config["theme"] or theme_name or theme.current
    vertical, horizontal = MENU_SIZES.get(config["size"], MENU_SIZES["normal"])
    font = config["font_size"]
    menu.setProperty("popMode", theme.mode)
    menu.setStyleSheet(theme.stylesheet(name) + (
        f"\nQMenu {{ font-size: {font}px; }}"
        f"\nQMenu::item {{ padding: {vertical}px {horizontal + 8}px {vertical}px {horizontal}px; font-size: {font}px; }}"
        f"\nQMenu::icon {{ padding-left: {horizontal}px; }}"))
    menu.setWindowOpacity(config["opacity"])


def dress_menu(ctx, menu: QMenu, theme_name: str = "") -> None:
    """Der Hintergrund kommt von Windows und geht erst, wenn das Fenster existiert."""
    name = ctx.config.get("context_menu.theme") or theme_name
    meta = ctx.theme.meta(name)
    hwnd = int(menu.winId())
    win_api.set_blur(hwnd, meta["blur"], ctx.theme.tint(name))
    win_api.set_rounded(hwnd, meta["rounded"] == "1")


def themed_menu(ctx, parent: QWidget, theme_name: str = "") -> QMenu:
    """Rechtsklickmenü im eingestellten Stil, das weich erscheint und verschwindet."""
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    style_menu(ctx, menu, theme_name)
    menu.aboutToShow.connect(lambda: dress_menu(ctx, menu, theme_name))
    animate_menu(ctx, menu)
    return menu


class PopupPanel(ThemedWindow):
    """Aufklappendes Fenster, das sich beim Klick daneben schließt, weich rein und raus.

    Kein Qt-Popup: Das schließt sich sofort und ohne Animation, und es klappt zu, wenn es per
    Hotkey aus einer anderen App den Fokus holt.
    """

    WINDOW_KIND = Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
    CENTERED = False  # waagerecht in der Bildschirmmitte statt am Knopf
    _open_panel: "PopupPanel | None" = None  # die Leisten nehmen keinen Fokus, Klicks dort schließen sonst nichts

    def __init__(self, ctx, theme_name: str = "", shift=None):
        flags = self.WINDOW_KIND | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        super().__init__(ctx, flags, theme_name)
        self.animator = WindowAnimator(ctx, self, shift)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QWidget(self, objectName="Panel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(self.panel)
        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(8)

    def dismiss(self) -> None:
        self.animator.leave()

    def closeEvent(self, event) -> None:
        # close() von außen: weich ausblenden, das Fenster bleibt für das nächste Öffnen bestehen.
        # Beim Beenden muss es sich schließen lassen, sonst blockiert es das Ende von PopWin.
        if self.ctx.is_exiting():
            event.accept()
            return
        event.ignore()
        self.dismiss()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate:
            QTimer.singleShot(0, self.dismiss)
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)

    def toggle_at(self, anchor: QWidget, prepare=None) -> None:
        if self.isVisible() and not self.animator.closing:
            self.dismiss()
            return
        if prepare:
            prepare()
        self.show_at(anchor)

    def _take_over(self) -> None:
        current = PopupPanel._open_panel
        if current is not None and current is not self:
            current.dismiss()
        PopupPanel._open_panel = self

    def show_at(self, anchor: QWidget, bar_gap: int = 0) -> None:
        """bar_gap: Abstand zur ganzen Leiste. Ohne ihn hängt das Fenster direkt am Knopf, die schwebende Taskbar ragt darüber."""
        self._take_over()
        self.apply_theme()
        self.adjustSize()
        screen = anchor.screen().availableGeometry()
        # Beim Ein- und Ausblenden steht die Leiste kurz daneben; das Menü richtet sich nach ihrem festen Platz.
        offset = round(getattr(anchor.window(), "slide_offset", 0) / anchor.devicePixelRatio())
        # --- [Antigravity Start: Sidebar Support (links/rechts)] ---
        bar_edge = getattr(anchor.window(), "edge", "bottom")
        if bar_edge in ("left", "right"):
            top_left = anchor.mapToGlobal(QPoint(0, 0)) - QPoint(offset, 0)
            bar_x = anchor.window().mapToGlobal(QPoint(0, 0)).x() - offset
            bar_w = anchor.window().width()
            gap = bar_gap or 8
            x = (bar_x + bar_w + gap) if bar_edge == "left" else (bar_x - self.width() - gap)
            x = min(max(x, screen.left() + 4), screen.right() - self.width() - 4)
            y = screen.center().y() - self.height() // 2 if self.CENTERED else top_left.y()
            y = min(max(y, screen.top() + 4), screen.bottom() - self.height() - 4)
            self.move(x, y)
            self.show()
            win_api.force_foreground(int(self.winId()))
            self.activateWindow()
            self.setFocus()
            self.animator.enter(1.0, slide=-8 if bar_edge == "left" else 8)
            return
        # --- [Antigravity Ende: Sidebar Support] ---
        top_left = anchor.mapToGlobal(QPoint(0, 0)) - QPoint(0, offset)
        x = screen.center().x() - self.width() // 2 if self.CENTERED else top_left.x()
        x = min(max(x, screen.left() + 4), screen.right() - self.width() - 4)
        below = top_left.y() + anchor.height() + 6
        opens_below = below + self.height() <= screen.bottom()
        y = below if opens_below else top_left.y() - self.height() - 6
        if bar_gap and not opens_below:
            bar_top = anchor.window().mapToGlobal(QPoint(0, 0)).y() - offset
            y = bar_top - self.height() - bar_gap
        self.move(x, y)
        self.show()
        win_api.force_foreground(int(self.winId()))
        self.activateWindow()
        self.setFocus()
        self.animator.enter(1.0, slide=-8 if opens_below else 8)
