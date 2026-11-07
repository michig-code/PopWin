"""Basisklasse: verschiebbar, anpassbar, Desktop-Ebene oder Always-on-Top."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget

from core import win_api
from ui.animation import WindowAnimator
from ui.base import G, IconButton, ThemedWindow


@dataclass(frozen=True)
class Option:
    """Eine Einstellung eines Widgets; daraus baut die Galerie automatisch das passende Feld."""
    key: str
    label: str
    kind: str  # bool, int, choice oder color
    default: object = None
    low: int = 0
    high: int = 0
    choices: dict = field(default_factory=dict)


MIN_WIDTH, MAX_WIDTH = 120, 640
MIN_HEIGHT, MAX_HEIGHT = 80, 900
COMMON_OPTIONS = (
    Option("show_title", "Titel anzeigen", "bool", True),
    Option("accent", "Akzentfarbe", "color", ""),
    Option("width", "Breite", "int", None, MIN_WIDTH, MAX_WIDTH),
    Option("height", "Höhe (0 = automatisch)", "int", 0, 0, MAX_HEIGHT),
)


class WidthGrip(QWidget):
    """Griff unten rechts: Zieht Breite und Höhe, die linke obere Ecke und damit der Text bleiben stehen."""
    SIZE = 16

    def __init__(self, desklet: "DeskletWindow"):
        super().__init__(desklet.frame)
        self.desklet = desklet
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.hide()
        self._start: tuple[QPoint, int, int] | None = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(128, 128, 128, 220), 2))
        for inset in (4, 9):
            painter.drawLine(self.SIZE - 2, inset, inset, self.SIZE - 2)

    # --- [Antigravity Start: Diagonaler Eckgriff vergrößert & verkleinert Breite und Höhe (Rest: Claude Code)] ---
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = (event.globalPosition().toPoint(), self.desklet.width(), self.desklet.height())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._start:
            start, width, height = self._start
            moved = event.globalPosition().toPoint() - start
            self.desklet.preview_size(width + moved.x(), height + moved.y())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._start:
            self._start = None
            w, h = self.desklet.width(), self.desklet.height()
            self.desklet.set_options({"width": w, "height": h})
            event.accept()
    # --- [Antigravity Ende: Diagonaler Eckgriff vergrößert & verkleinert Breite und Höhe] ---


class DeskletWindow(ThemedWindow):
    TITLE = "Widget"
    GLYPH = G.APP
    DESCRIPTION = ""
    DEFAULT_WIDTH = 240
    OPTIONS: tuple[Option, ...] = ()

    def __init__(self, ctx, spec: dict):
        super().__init__(ctx, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.spec = spec
        self.animator = WindowAnimator(ctx, self)
        self._drag_offset: QPoint | None = None
        self._desktop_state: str | None = None
        self.title_label: QLabel | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QWidget(self, objectName="Desklet")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(self.frame)
        self.body = QVBoxLayout(self.frame)
        self.body.setContentsMargins(12, 10, 12, 10)
        self.body.setSpacing(6)
        self.build()
        self.close_button = IconButton(G.CLOSE, "Widget entfernen", self.frame)
        self.close_button.clicked.connect(self.ask_remove)
        self.close_button.hide()
        self.grip = WidthGrip(self)
        self._zorder_timer = QTimer(self, interval=500)
        self._zorder_timer.timeout.connect(self._keep_on_desktop)
        self.apply_options()

    def build(self) -> None:
        raise NotImplementedError

    def add_title(self) -> None:
        self.title_label = QLabel(self.TITLE, objectName="PanelTitle")
        self.body.addWidget(self.title_label)

    # --- Optionen -----------------------------------------------------------
    def all_options(self) -> tuple[Option, ...]:
        common = COMMON_OPTIONS if self.title_label else COMMON_OPTIONS[1:]
        return (*common, *self.OPTIONS)

    def default_of(self, option: Option):
        return self.DEFAULT_WIDTH if option.key == "width" else option.default

    def option(self, key: str):
        chosen = self.spec.get("options", {})
        if key in chosen:
            return chosen[key]
        return next(self.default_of(o) for o in self.all_options() if o.key == key)

    # --- [Antigravity Start: Atomares Setzen mehrerer Optionen (Rest: Claude Code)] ---
    def set_options(self, updates: dict) -> None:
        options = {**self.spec.get("options", {}), **updates}
        self.spec["options"] = options
        self.ctx.widgets.update_spec(self.spec["id"], options=options)
        self.apply_options()

    def set_option(self, key: str, value) -> None:
        self.set_options({key: value})
    # --- [Antigravity Ende: Atomares Setzen mehrerer Optionen] ---

    def accent(self) -> QColor:
        chosen = self.option("accent")
        if chosen:
            return QColor(chosen)
        return QColor(self.ctx.theme.meta()["ind_light" if self.ctx.theme.mode == "light" else "ind"])

    def apply_options(self) -> None:
        self.setFixedWidth(self.option("width"))
        self._fit_height(self.option("height"))
        if self.title_label:
            self.title_label.setVisible(self.option("show_title"))
        self.options_changed()
        self.adjustSize()
        self.update()

    def options_changed(self) -> None:
        """Wird nach jeder Änderung aufgerufen; Widgets übernehmen hier ihre eigenen Optionen."""

    # --- Anzeige ------------------------------------------------------------
    def show_desklet(self) -> None:
        self.move(self.spec.get("x", 100), self.spec.get("y", 100))
        self.show()
        self.apply_theme()
        self.apply_layer()
        self.animator.enter(self.spec.get("opacity", 1.0))

    def apply_layer(self) -> None:
        hwnd = int(self.winId())
        win_api.own_by_desktop(hwnd)  # sonst räumt Win+D die Widgets mit den Fenstern weg
        if self.spec.get("z") == "overlay":
            self._zorder_timer.stop()
            win_api.set_zorder(hwnd, win_api.HWND_TOPMOST)
        else:
            win_api.set_zorder(hwnd, win_api.HWND_NOTOPMOST)
            win_api.set_zorder(hwnd, win_api.HWND_BOTTOM)
            self._desktop_state = "bottom"
            self._zorder_timer.start()

    def _keep_on_desktop(self) -> None:
        # Bei "Desktop anzeigen" (Win+D) liegt der Desktop über allem - das Widget muss mit nach oben.
        # Nur bei einem Wechsel: Jede Verschiebung kostet Zeit, und viele Widgets bremsen sonst die Oberfläche.
        hwnd = int(self.winId())
        foreground = win_api.foreground_window()
        if self._drag_offset is not None:
            return
        if foreground == hwnd:
            self._desktop_state = None
            return
        state = "top" if win_api.class_name(foreground) in ("Progman", "WorkerW") else "bottom"
        if state != self._desktop_state:
            self._desktop_state = state
            win_api.set_zorder(hwnd, win_api.HWND_TOP if state == "top" else win_api.HWND_BOTTOM)

    # --- Griffe: erscheinen nur, solange die Maus auf dem Widget ist -------------
    def _place_handles(self) -> None:
        self.close_button.adjustSize()
        self.close_button.move(self.frame.width() - self.close_button.width() - 4, 4)
        self.grip.move(self.frame.width() - WidthGrip.SIZE - 2, self.frame.height() - WidthGrip.SIZE - 2)

    def enterEvent(self, event) -> None:
        self._place_handles()
        self.close_button.show()
        self.close_button.raise_()
        self.grip.show()
        self.grip.raise_()

    def leaveEvent(self, event) -> None:
        # --- [Antigravity Start: Griff während Drag oder im Inhalt aktiv halten (Rest: Claude Code)] ---
        if getattr(self.grip, "_start", None) is not None:
            return
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(cursor_pos):
            return
        # --- [Antigravity Ende] ---
        self.close_button.hide()
        self.grip.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_handles()

    def _fit_height(self, height: int) -> None:
        """0 heißt: so hoch wie der Inhalt. Kleiner als der Inhalt geht nicht, sonst würde er abgeschnitten."""
        if height:
            self.frame.layout().activate()
            self.setFixedHeight(max(MIN_HEIGHT, self.frame.minimumSizeHint().height(), min(MAX_HEIGHT, height)))
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)

    def preview_size(self, width: int, height: int) -> None:
        self.setFixedWidth(max(MIN_WIDTH, min(MAX_WIDTH, width)))
        self._fit_height(max(1, height))

    def ask_remove(self) -> None:
        # Nur nachfragen, wenn etwas verloren ginge, zum Beispiel der Text einer Notiz.
        if self.spec.get("data"):
            answer = QMessageBox.question(self, "PopWin", f"{self.TITLE} samt Inhalt entfernen?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.ctx.widgets.remove(self.spec["id"])

    # --- Verschieben --------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.ctx.widgets.update_spec(self.spec["id"], x=self.x(), y=self.y())

    def contextMenuEvent(self, event) -> None:
        self.ctx.widgets.open_gallery(self)

    # --- Ebene und Transparenz, von der Galerie gesetzt -------------------------
    def set_opacity(self, value: float) -> None:
        self.setWindowOpacity(value)
        self.spec["opacity"] = value
        self.ctx.widgets.update_spec(self.spec["id"], opacity=value)

    def set_layer(self, layer: str) -> None:
        self.spec["z"] = layer
        self.ctx.widgets.update_spec(self.spec["id"], z=layer)
        self.apply_layer()
