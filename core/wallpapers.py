"""Animierte Hintergründe ohne Wallpaper Engine. Läuft sie, tritt PopWin zurück, denn beide wollen denselben Platz."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg
from pathlib import Path

import psutil
from PySide6.QtCore import QThread, Signal

from core import i18n, win_api
from core.schedule import rotation_pick
from core.paths import DATA_DIR, ROOT, app_command

log = logging.getLogger(__name__)

ENGINE_PROCESSES = ("wallpaper32.exe", "wallpaper64.exe")
ENGINE_STEAM_ID = "431960"
RUNNING, INSTALLED, MISSING = "running", "installed", "missing"
VIDEO_TYPES = (".mp4", ".webm", ".mkv", ".mov", ".m4v")
PICTURE_TYPES = (".gif",)
MEDIA_TYPES = VIDEO_TYPES + PICTURE_TYPES
MAX_DOWNLOAD = 4 * 1024 ** 3
CHUNK = 256 * 1024
TIMEOUT = 20
FREE_SOURCES = {"Pexels": "https://www.pexels.com/search/videos/abstract%20loop/",
                "Pixabay": "https://pixabay.com/videos/search/loop%20background/"}
FOLDER = DATA_DIR / "wallpapers"
PRESET_FOLDER = ROOT / "assets" / "wallpapers"
PRESETS = {"galaxie": "Galaxie", "linien": "Linien", "wellen": "Wellen", "waben": "Waben", "kristall": "Kristall", "sterne": "Sterne",
           "scherben": "Scherben", "gipfel": "Gipfel", "dash": "Dash", "saegen": "Sägen", "null": "Null",
           "polarlicht": "Polarlicht", "raster": "Raster", "glut": "Glut"}
PRESET_PREFIX = "preset:"
AUTO, ENGINE, OWN = "auto", "engine", "popwin"
ENGINE_START = f"steam://rungameid/{ENGINE_STEAM_ID}"
MAX_FAILURES = 3
GIF_CACHE_BUDGET = 96 * 1024 ** 2  # so viel RAM dürfen die fertig skalierten Bilder eines GIFs höchstens belegen
PAUSE_COVERED, PAUSE_BATTERY = "c", "b"  # Kennbuchstaben, die der Hintergrund-Prozess als viertes Argument bekommt


def gif_fits_cache(frames: int, width: int, height: int, budget: int = GIF_CACHE_BUDGET) -> bool:
    """Passen alle Bilder eines GIFs in Zielgröße in den Speicher? Ein GIF mit 100 Bildern belegt auf 1440p rund 1,4 GB;
    solche spielt PopWin Bild für Bild ab, statt alle vorzuhalten. Unbekannte Bildzahl (0) gilt als zu groß."""
    return frames > 0 and frames * width * height * 4 <= budget


def pause_flags(settings: dict) -> str:
    return (PAUSE_COVERED if settings.get("pause_covered", True) else "") + (PAUSE_BATTERY if settings.get("pause_on_battery", True) else "")


def should_play(fullscreen: bool, covered: list[bool], on_battery: bool, flags: str) -> bool:
    """Läuft der Hintergrund gerade? Nicht hinter einem Vollbild, nicht wenn jeder bespielte Bildschirm von einem
    maximierten Fenster verdeckt ist, und auf Wunsch nicht im Akkubetrieb."""
    if fullscreen:
        return False
    if PAUSE_COVERED in flags and covered and all(covered):
        return False
    return not (PAUSE_BATTERY in flags and on_battery)


def steam_libraries() -> list[Path]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [Path(path.replace("\\\\", "\\")) for path in re.findall(r'"path"\s+"([^"]+)"', text)]


def engine_state(libraries: list[Path] | None = None, process_names: set[str] | None = None) -> str:
    """Wird bei jedem Blick neu geprüft: Wer sie gerade installiert hat, soll nichts neu starten müssen."""
    names = process_names if process_names is not None else {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
    if any(name in names for name in ENGINE_PROCESSES):
        return RUNNING
    found = any((library / "steamapps" / f"appmanifest_{ENGINE_STEAM_ID}.acf").exists()
                for library in (steam_libraries() if libraries is None else libraries))
    return INSTALLED if found else MISSING


def is_media(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in MEDIA_TYPES


def library() -> list[Path]:
    if not FOLDER.exists():
        return []
    return sorted((path for path in FOLDER.iterdir() if path.is_file() and is_media(path.name)), key=lambda path: path.name.lower())


def media_path(choice: str) -> str:
    """Presets werden mit Namen gemerkt: Ihr Ordner hängt davon ab, wo PopWin installiert ist."""
    if choice.startswith(PRESET_PREFIX):
        return str(PRESET_FOLDER / f"{choice[len(PRESET_PREFIX):]}.mp4")
    return choice


def free_path(name: str) -> Path:
    """Fremde Namen dürfen nicht aus dem Ordner herausführen und nichts überschreiben."""
    stem, suffix = os.path.splitext(os.path.basename(name.replace("\\", "/")))
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "hintergrund"
    path = FOLDER / f"{stem}{suffix.lower()}"
    number = 2
    while path.exists():
        path = FOLDER / f"{stem} ({number}){suffix.lower()}"
        number += 1
    return path


def add_file(source: str) -> Path:
    if not is_media(source):
        raise ValueError(i18n.translate("Das ist kein Video und kein GIF."))
    FOLDER.mkdir(parents=True, exist_ok=True)
    target = free_path(source)
    shutil.copy2(source, target)
    return target


def name_from_url(url: str, content_type: str) -> str:
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    if is_media(name):
        return name
    for kind, suffix in (("video/mp4", ".mp4"), ("video/webm", ".webm"), ("image/gif", ".gif")):
        if content_type.startswith(kind):
            return f"hintergrund{suffix}"
    raise ValueError(i18n.translate("Der Link führt nicht direkt zu einem Video oder GIF."))


class WallpaperDownload(QThread):
    """Lädt nur Videos und GIFs. Sie werden abgespielt, nie ausgeführt, und unfertige Dateien bleiben nicht liegen."""
    progress = Signal(int)
    finished_with = Signal(str, str)  # Datei, Fehler

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url.strip()

    def run(self) -> None:
        part = None
        try:
            if urllib.parse.urlparse(self.url).scheme not in ("http", "https"):
                raise ValueError(i18n.translate("Bitte einen Link mit http oder https angeben."))
            request = urllib.request.Request(self.url, headers={"User-Agent": "PopWin"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                name = name_from_url(answer.geturl(), answer.headers.get("Content-Type", ""))
                total = int(answer.headers.get("Content-Length") or 0)
                if total > MAX_DOWNLOAD:
                    raise ValueError(i18n.translate("Die Datei ist zu groß."))
                FOLDER.mkdir(parents=True, exist_ok=True)
                target = free_path(name)
                part = target.with_name(target.name + ".part")
                done = 0
                with part.open("wb") as file:
                    while chunk := answer.read(CHUNK):
                        done += len(chunk)
                        if done > MAX_DOWNLOAD:
                            raise ValueError(i18n.translate("Die Datei ist zu groß."))
                        file.write(chunk)
                        if total:
                            self.progress.emit(done * 100 // total)
            part.rename(target)
            self.finished_with.emit(str(target), "")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            if part:
                part.unlink(missing_ok=True)
            self.finished_with.emit("", str(exc))


class WallpaperPlayer:
    """Hält den eigenen Hintergrund-Prozess am Laufen oder beendet ihn. Stürzt ein Video ab, trifft es nur diesen Prozess."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.process: subprocess.Popen | None = None
        self.running_with: tuple = ()
        self.engine_started = False
        self.failures = 0
        self.rotation_start = time.monotonic()
        self.rotation_from = ""  # die Wahl, bei der der Wechsel begonnen hat

    def wanted(self, engine: str) -> tuple:
        """Ohne eigene Wahl passiert nichts: PopWin setzt von sich aus nie einen Hintergrund."""
        settings = self.ctx.config.get("wallpaper")
        file = media_path(self.current_choice(settings))
        if settings["source"] == ENGINE or not settings["enabled"] or not file or not os.path.exists(file):
            return ()
        if engine in (RUNNING, "") and settings["source"] == AUTO:  # "": die erste Prüfung steht noch aus, nicht vorschnell drüberlegen
            return ()
        return file, settings["screens"], pause_flags(settings)

    def current_choice(self, settings: dict) -> str:
        """Mit eingeschaltetem Wechsel der Hintergrund, der gerade dran ist; sonst der gewählte."""
        choice, every = settings["file"], int(settings.get("rotate_minutes") or 0)
        if not choice or every <= 0:
            return choice
        if choice != self.rotation_from:  # neu gewählt: Der Wechsel beginnt dort von vorn
            self.rotation_from, self.rotation_start = choice, time.monotonic()
        choices = [PRESET_PREFIX + key for key in PRESETS] + [str(path) for path in library()]
        elapsed = (time.monotonic() - self.rotation_start) / 60
        return rotation_pick(choices, choice, elapsed, every, bool(settings.get("rotate_shuffle")), seed=os.getpid())

    def _follow_source(self, engine: str) -> None:
        source = self.ctx.config.get("wallpaper.source")
        if source == ENGINE and engine == INSTALLED and not self.engine_started:
            self.engine_started = True  # einmal pro Sitzung: Wer sie beendet, will das vermutlich so
            win_api.shell_open(ENGINE_START)

    @staticmethod
    def close_engine() -> None:
        """Nur nach einem Ja in den Einstellungen. Von selbst beendet PopWin Wallpaper Engine nie, auch nicht beim Start."""
        for process in psutil.process_iter(["name"]):
            if (process.info["name"] or "").lower() in ENGINE_PROCESSES:
                try:
                    process.terminate()
                except psutil.Error:
                    pass

    def sync(self) -> None:
        engine = self.ctx.status.state.get("wallpaper_engine", MISSING)
        self._follow_source(engine)
        wanted = self.wanted(engine)
        alive = self.process is not None and self.process.poll() is None
        if wanted == self.running_with and (alive or not wanted):
            return
        if wanted != self.running_with:
            self.failures = 0
        elif self.process is not None and self.process.poll() not in (0, None):  # 0: kommt mit neuen Bildschirmen neu
            self.failures += 1
        self.stop()
        if self.failures >= MAX_FAILURES:  # kaputte Datei: nicht ewig neu starten
            self.running_with = wanted
            return
        if wanted:
            self.process = subprocess.Popen(app_command("wallpaper", wanted[0], wanted[1], str(os.getpid()), wanted[2]), cwd=ROOT,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            self.running_with = wanted

    def stop(self) -> None:
        self.running_with = ()
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
            win_api.redraw_wallpaper()  # sonst bliebe die Fläche schwarz, bis Windows sie von selbst neu zeichnet
