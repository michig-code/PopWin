"""Spielt ein Video oder GIF hinter den Desktop-Symbolen. Eigener Prozess, damit Videotechnik die Leisten nie mitreißt.

Aufruf: wallpaper DATEI all|primary PID-von-PopWin
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402
from PySide6.QtCore import QSize, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMovie, QWindow  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from core import win_api  # noqa: E402
from core.monitor_manager import native_rect  # noqa: E402
from core.wallpapers import PICTURE_TYPES  # noqa: E402

CHECK_MS = 2000


class Scene(QWidget):
    """Ein Bildschirm. Ohne Tonausgabe bleibt jedes Video stumm."""

    def __init__(self, screen, file: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint)
        self.screen_ref = screen
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: black;")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.player: QMediaPlayer | None = None
        self.movie: QMovie | None = None
        if os.path.splitext(file)[1].lower() in PICTURE_TYPES:
            label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            self.movie = QMovie(file)
            self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self.movie.setScaledSize(QSize(screen.geometry().size()))
            label.setMovie(self.movie)
            box.addWidget(label)
        else:
            video = QVideoWidget()
            video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(video)
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            self.player.setSource(QUrl.fromLocalFile(file))
            box.addWidget(video)

    def attach(self, host: int) -> None:
        """Qt muss den neuen Elternteil selbst kennen: Hängt man es an Qt vorbei um, bleibt das Fenster unsichtbar."""
        hwnd = int(self.winId())
        self.windowHandle().setParent(QWindow.fromWinId(host))
        self.resize(self.screen_ref.size())  # sonst bleibt die Videofläche darin bei 0 × 0
        self.show()
        win_api.place_behind_icons(hwnd, host, native_rect(self.screen_ref))

    def run(self, playing: bool) -> None:
        if self.player:
            (self.player.play if playing else self.player.pause)()
        if self.movie:
            self.movie.setPaused(not playing) if self.movie.state() != QMovie.MovieState.NotRunning else self.movie.start()


def main() -> int:
    file, screens, parent = sys.argv[1], sys.argv[2], int(sys.argv[3])
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PopWin.Wallpaper")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    host = win_api.wallpaper_host()
    if not host:
        return 1
    chosen = [QGuiApplication.primaryScreen()] if screens == "primary" else QGuiApplication.screens()
    scenes = [Scene(screen, file) for screen in chosen]
    for scene in scenes:
        scene.attach(host)
        scene.run(True)
    state = {"playing": True}

    def check() -> None:
        if not psutil.pid_exists(parent) or not win_api.is_window(host):  # Fehlt der Host, startet PopWin neu, sobald der Explorer zurück ist
            app.quit()
            return
        covered = win_api.foreground_is_fullscreen()  # beim Spielen keine Rechenleistung verschwenden
        if covered == state["playing"]:
            state["playing"] = not covered
            for scene in scenes:
                scene.run(not covered)

    app.screenAdded.connect(lambda _screen: app.quit())  # PopWin startet den Hintergrund passend neu
    app.screenRemoved.connect(lambda _screen: app.quit())
    timer = QTimer(interval=CHECK_MS)
    timer.timeout.connect(check)
    timer.start()
    code = app.exec()
    win_api.redraw_wallpaper()
    return code


if __name__ == "__main__":
    sys.exit(main())
