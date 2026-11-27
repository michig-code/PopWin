"""Spielt ein Video oder GIF hinter den Desktop-Symbolen. Eigener Prozess, damit Videotechnik die Leisten nie mitreißt.

Aufruf: wallpaper DATEI all|primary PID-von-PopWin [Pausen-Kennung]
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402
from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMovie, QWindow  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink  # noqa: E402
from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from core import win_api  # noqa: E402
from core.monitor_manager import native_rect  # noqa: E402
from core.wallpapers import PAUSE_BATTERY, PAUSE_COVERED, PICTURE_TYPES, gif_fits_cache, should_play  # noqa: E402

CHECK_MS = 2000
TRIM_AFTER = 30.0  # so viele Sekunden Pause, dann geht der ungenutzte Arbeitsspeicher an Windows zurück
BATTERY_EVERY = 5  # der Akku wird nur bei jeder fünften Prüfung gelesen
SHARED_DECODER = True  # ein Video-Decoder für alle Bildschirme statt einem pro Bildschirm; False = altes Verhalten


def is_picture(file: str) -> bool:
    return os.path.splitext(file)[1].lower() in PICTURE_TYPES


class Scene(QWidget):
    """Ein Bildschirm. Ohne Tonausgabe bleibt jedes Video stumm."""

    def __init__(self, screen, file: str, own_player: bool = True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint)
        self.screen_ref = screen
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: black;")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.player: QMediaPlayer | None = None
        self.movie: QMovie | None = None
        self.video: QVideoWidget | None = None
        if is_picture(file):
            label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            self.movie = QMovie(file)
            size = screen.geometry().size()
            if gif_fits_cache(self.movie.frameCount(), size.width(), size.height()):
                self.movie.setCacheMode(QMovie.CacheMode.CacheAll)
                self.movie.setScaledSize(size)
            else:  # großes GIF: Bild für Bild lesen und beim Zeichnen strecken, statt alle Bilder im RAM zu halten
                self.movie.setCacheMode(QMovie.CacheMode.CacheNone)
                label.setScaledContents(True)
            label.setMovie(self.movie)
            box.addWidget(label)
        else:
            self.video = QVideoWidget()
            self.video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            if own_player:
                self.player = new_player(file, self)
                self.player.setVideoOutput(self.video)
            box.addWidget(self.video)

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


def new_player(file: str, parent) -> QMediaPlayer:
    player = QMediaPlayer(parent)
    player.setLoops(QMediaPlayer.Loops.Infinite)
    player.setSource(QUrl.fromLocalFile(file))
    return player


class SharedVideo:
    """Ein Decoder für alle Bildschirme: Jedes Bild wird einmal dekodiert und an jede Videofläche weitergereicht.
    Mit zwei Monitoren spart das einen ganzen Decoder samt seinem Bildspeicher."""

    def __init__(self, file: str, scenes: list[Scene], parent):
        self.sinks = [scene.video.videoSink() for scene in scenes if scene.video is not None]
        self.sink = QVideoSink(parent)
        self.player = new_player(file, parent)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._spread)

    def _spread(self, frame) -> None:
        for sink in self.sinks:
            sink.setVideoFrame(frame)

    def run(self, playing: bool) -> None:
        (self.player.play if playing else self.player.pause)()


def on_battery() -> bool:
    battery = psutil.sensors_battery()
    return bool(battery) and not battery.power_plugged


def main() -> int:
    file, screens, parent = sys.argv[1], sys.argv[2], int(sys.argv[3])
    flags = sys.argv[4] if len(sys.argv) > 4 else ""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PopWin.Wallpaper")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    host = win_api.wallpaper_host()
    if not host:
        return 1
    win_api.set_low_priority()  # der Hintergrund darf nie einem Programm im Vordergrund Rechenzeit wegnehmen
    chosen = [QGuiApplication.primaryScreen()] if screens == "primary" else QGuiApplication.screens()
    shared = SHARED_DECODER and len(chosen) > 1 and not is_picture(file)
    scenes = [Scene(screen, file, own_player=not shared) for screen in chosen]
    for scene in scenes:
        scene.attach(host)
    players = scenes + ([SharedVideo(file, scenes, app)] if shared else [])
    for player in players:
        player.run(True)
    state = {"playing": True, "paused_at": 0.0, "trimmed": False, "tick": 0, "battery": False}

    def check() -> None:
        if not psutil.pid_exists(parent) or not win_api.is_window(host):  # Fehlt der Host, startet PopWin neu, sobald der Explorer zurück ist
            app.quit()
            return
        if PAUSE_BATTERY in flags and state["tick"] % BATTERY_EVERY == 0:
            state["battery"] = on_battery()
        state["tick"] += 1
        covered = [win_api.desktop_covered(native_rect(scene.screen_ref)) for scene in scenes] if PAUSE_COVERED in flags else []
        # beim Spielen und hinter maximierten Fenstern keine Rechenleistung verschwenden
        playing = should_play(win_api.foreground_is_fullscreen(), covered, state["battery"], flags)
        if playing != state["playing"]:
            state.update(playing=playing, paused_at=time.monotonic(), trimmed=False)
            for player in players:
                player.run(playing)
        elif not playing and not state["trimmed"] and time.monotonic() - state["paused_at"] >= TRIM_AFTER:
            state["trimmed"] = True
            win_api.trim_working_set()

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
