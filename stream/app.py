"""PopWin Stream: setzt Monitore zu einem Bild zusammen und gibt es als virtuelle Kamera und als Fenster aus.

Eigener Prozess: Das Aufnehmen mehrerer Monitore kostet Rechenzeit, die Leisten von PopWin sollen davon nichts merken.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRect, Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap, QPolygonF  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout  # noqa: E402

from core.monitor_manager import native_rect, screen_key  # noqa: E402
from ui.app_shell import run_app  # noqa: E402
from ui.window_frame import NativeWindow  # noqa: E402

log = logging.getLogger(__name__)
WDA_EXCLUDEFROMCAPTURE = 0x11
GAP = 8
CURSOR_SHAPE = ((0, 0), (0, 15), (4, 11), (7, 17), (10, 16), (7, 10), (12, 10))


def canvas_size(height: int) -> tuple[int, int]:
    return height * 16 // 9, height


def layout_rects(rects: list[tuple], width: int, height: int, mode: str) -> list[QRect]:
    """Wo jeder Monitor im Bild landet: wie in Windows angeordnet oder einfach nebeneinander, zentriert."""
    if mode == "row":
        x, placed = 0, []
        for left, top, right, bottom in rects:
            placed.append((x, 0, x + right - left, bottom - top))
            x += right - left + GAP * 4
        rects = placed
    left, top = min(r[0] for r in rects), min(r[1] for r in rects)
    right, bottom = max(r[2] for r in rects), max(r[3] for r in rects)
    scale = min(width / (right - left), height / (bottom - top))
    offset_x = (width - (right - left) * scale) / 2
    offset_y = (height - (bottom - top) * scale) / 2
    return [QRect(round(offset_x + (r[0] - left) * scale), round(offset_y + (r[1] - top) * scale),
                  round((r[2] - r[0]) * scale), round((r[3] - r[1]) * scale)) for r in rects]


def cursor_spot(point: tuple[int, int], rects: list[tuple], places: list[QRect]) -> tuple[float, float] | None:
    """Wo der Mauszeiger im Bild liegt; die Aufnahme enthält ihn nicht von selbst."""
    x, y = point
    for rect, place in zip(rects, places):
        left, top, right, bottom = rect
        if left <= x < right and top <= y < bottom:
            return (place.x() + (x - left) * place.width() / (right - left),
                    place.y() + (y - top) * place.height() / (bottom - top))
    return None


def frame_bytes(image: QImage):
    """Das Bild als Zahlenfeld; Qt rundet jede Zeile auf, die Kamera will sie lückenlos."""
    import numpy
    data = numpy.frombuffer(image.constBits(), numpy.uint8).reshape(image.height(), image.bytesPerLine())
    return data[:, :image.width() * 3].reshape(image.height(), image.width(), 3)


class Capture(QThread):
    frame = Signal(QImage)
    status = Signal(str)
    camera_on = Signal()

    def __init__(self, rects: list[tuple], size: tuple[int, int], fps: int, mode: str, parent=None):
        super().__init__(parent)
        self.rects, self.size, self.fps, self.mode = rects, size, fps, mode
        self.stopped = False

    def open_camera(self):
        try:
            import pyvirtualcam
            camera = pyvirtualcam.Camera(*self.size, self.fps, fmt=pyvirtualcam.PixelFormat.BGR)
            self.status.emit(f"Kamera läuft: {camera.device}. In Discord als Kamera auswählen.")
            self.camera_on.emit()
            return camera
        except Exception as exc:  # ohne OBS bleibt das teilbare Fenster
            log.warning("Virtuelle Kamera nicht verfügbar: %s", exc)
            self.status.emit("Keine virtuelle Kamera gefunden. Teile in Discord dieses Fenster.")
            return None

    def compose(self, grabber, places: list[QRect]) -> QImage:
        canvas = QImage(*self.size, QImage.Format.Format_BGR888)
        canvas.fill(QColor("#101114"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for rect, place in zip(self.rects, places):
            left, top, right, bottom = rect
            shot = grabber.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
            painter.drawImage(place, QImage(shot.bgra, shot.width, shot.height, QImage.Format.Format_RGB32))
        self.draw_cursor(painter, places)
        painter.end()
        return canvas

    def draw_cursor(self, painter: QPainter, places: list[QRect]) -> None:
        import win32gui
        spot = cursor_spot(win32gui.GetCursorPos(), self.rects, places)
        if spot is None:
            return
        x, y = spot
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#000000"))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPolygon(QPolygonF([QPointF(x + a, y + b) for a, b in CURSOR_SHAPE]))

    def run(self) -> None:
        import mss
        places = layout_rects(self.rects, *self.size, self.mode)
        camera = self.open_camera()
        pause = 1 / self.fps
        with mss.mss() as grabber:
            while not self.stopped:
                started = time.monotonic()
                image = self.compose(grabber, places)
                self.frame.emit(image)
                if camera is not None:
                    camera.send(frame_bytes(image))
                time.sleep(max(0.0, pause - (time.monotonic() - started)))
        if camera is not None:
            camera.close()

    def stop(self) -> None:
        self.stopped = True
        self.wait(2000)


class StreamWindow(NativeWindow):
    def __init__(self, ctx, _arguments: list[str] | None = None):
        super().__init__(ctx, "PopWin Stream", "apps/stream")
        self.resize(960, 600)
        settings = ctx.config.get("extensions.discord")
        chosen = settings["monitors"]
        screens = QGuiApplication.screens()
        rects = [native_rect(screen) for screen in screens if not chosen or screen_key(screen) in chosen]
        box = QVBoxLayout(self.content)
        box.setContentsMargins(10, 4, 10, 10)
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 180)
        self.status = QLabel("Startet...", objectName="Muted")
        stop = QPushButton("Stream beenden", objectName="Danger")
        stop.clicked.connect(self.close)
        box.addWidget(self.preview, 1)
        box.addWidget(self.status)
        box.addWidget(stop)
        self.capture = Capture(rects or [native_rect(screens[0])], canvas_size(settings["size"]), settings["fps"],
                               settings["layout"], self)
        self.capture.frame.connect(self._show_frame)
        self.capture.status.connect(self.status.setText)
        self.capture.camera_on.connect(self._hide_from_capture)
        self.capture.start()
        self.apply_theme()

    def _hide_from_capture(self) -> None:
        """Sonst zeigte die Vorschau sich selbst, wie ein Spiegel im Spiegel."""
        ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)

    def _show_frame(self, image: QImage) -> None:
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def closeEvent(self, event) -> None:
        self.capture.stop()
        super().closeEvent(event)


def main() -> int:
    return run_app("Stream", "PopWin Stream", lambda ctx, arguments: StreamWindow(ctx, arguments))


if __name__ == "__main__":
    sys.exit(main())
