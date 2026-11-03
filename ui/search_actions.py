"""Live-Vorschläge der Searchbar: Aktionen beim Tippen und Vorschläge zur aktuellen Situation."""
from __future__ import annotations

import io
import logging
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor, QGuiApplication

from core import system_status
from core import win_api
from core.dev import dev_menu_wanted, open_dev_menu
from core.gemini_client import TranscribeWorker
from core.monitor_manager import native_point, native_rect

log = logging.getLogger(__name__)

RECENT_SECONDS = 15 * 60
DEV_GLYPH = ""  # DeveloperTools aus der Windows-Symbolschrift
_PARTIAL = (".crdownload", ".part", ".tmp", ".partial", ".download")
_URL = re.compile(r"^https?://\S+$", re.I)
SAMPLE_RATE = 16000


@dataclass
class Suggestion:
    label: str
    action: Callable[[], None]
    glyph: str = ""  # eigenes Symbol, wenn es keine Datei mit Icon gibt


OUTPUT_WORDS = ("lautsprecher", "headset", "kopfhörer", "audio", "ausgabe", "speaker", "sound")


def typed_actions(ctx, query: str) -> list[Suggestion]:
    """Aktionen, die lokal zum Getippten passen. Kostet keine Tokens."""
    text = query.lower().strip()
    if len(text) < 2:
        return []
    bluetooth = ctx.status.state.get("bluetooth")
    wifi = ctx.status.state.get("wifi")
    dark = ctx.theme.mode == "dark"
    entries = [
        (("lautstärke", "laut", "volume", "ton"), "Lautstärke-Einstellungen", lambda: win_api.shell_open("ms-settings:sound")),
        (("fokus", "focus", "timer"), "Fokus-Timer starten (25 Minuten)", lambda: ctx.focus.start(25)),
        (("dark", "dunkel", "hell", "white", "modus"), "Hellen Modus aktivieren" if dark else "Dunklen Modus aktivieren",
         lambda: ctx.set_dark_mode("light" if dark else "dark")),
        (("taskbar", "leiste"), "Taskbar-Einstellungen", lambda: ctx.open_settings_at("Taskbar")),
        (("widget",), "Widget-Galerie öffnen", ctx.open_widget_settings),
        (("einstellungen", "popwin", "settings"), "PopWin-Einstellungen", ctx.open_settings),
        (("nova",), "Nova öffnen oder schließen", ctx.nova.toggle),
        (("explorer", "dateien", "ordner"), "PopWin Explorer öffnen", ctx.open_explorer),
        (("papierkorb", "müll", "trash"), "Papierkorb öffnen", lambda: win_api.shell_open("shell:RecycleBinFolder")),
        (("sperren", "lock"), "PC sperren", win_api.lock_workstation),
        (("screenshot", "bildschirmfoto"), "Screenshot aufnehmen", lambda: win_api.send_keys(0x5B, 0x10, 0x53)),
    ]
    if ctx.has_feature("bluetooth"):
        entries.append((("bluetooth",), "Bluetooth ausschalten" if bluetooth else "Bluetooth einschalten",
                        lambda: ctx.status.set_bluetooth(not bluetooth)))
    if ctx.has_feature("wifi"):
        entries.append((("wlan", "wifi"), "WLAN ausschalten" if wifi else "WLAN einschalten",
                        lambda: ctx.status.set_wifi(not wifi)))
    found = [Suggestion(label, action) for words, label, action in entries
             if any(word.startswith(text) or text.startswith(word) for word in words)]
    if dev_menu_wanted(text):  # nur im Dev-Build; die PIN fragt das Menü selbst
        found.insert(0, Suggestion("Dev-Menü", lambda: open_dev_menu(ctx), DEV_GLYPH))
    if any(word.startswith(text) or text.startswith(word) for word in OUTPUT_WORDS):
        found += [Suggestion(f"Audio auf {device.name} umstellen", lambda d=device.id: system_status.set_audio_output(d))
                  for device in system_status.audio_outputs() if not device.active]
    return found


def _newest(folder: Path, skip: tuple[str, ...] = ()) -> Path | None:
    try:
        files = [p for p in folder.iterdir() if p.is_file() and not p.name.lower().endswith(skip)
                 and p.name.lower() != "desktop.ini"]
    except OSError:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime, default=None)
    return newest if newest and time.time() - newest.stat().st_mtime < RECENT_SECONDS else None


def _cursor_windows(ctx) -> tuple[list[int], tuple[int, int, int, int]]:
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    left, top = native_point(screen, area.x(), area.y())
    right, bottom = native_point(screen, area.right() + 1, area.bottom() + 1)
    rect = native_rect(screen)
    hwnds = [w.hwnd for w in ctx.windows() if not win_api.is_minimized(w.hwnd) and win_api.window_monitor(w.hwnd) == rect]
    return hwnds, (left, top, right, bottom)


def situation_actions(ctx) -> list[Suggestion]:
    """Vorschläge beim Öffnen der Suche. Liest die Zwischenablage nur jetzt und nur lokal."""
    results: list[Suggestion] = []
    download = _newest(Path.home() / "Downloads", _PARTIAL)
    if download:
        results.append(Suggestion(f"Download öffnen: {download.name}", lambda: win_api.shell_open(str(download))))
        results.append(Suggestion("Im Ordner zeigen", lambda: win_api.shell_open("explorer.exe", f'/select,"{download}"')))
    clip = QGuiApplication.clipboard().text().strip()
    if _URL.match(clip):
        label = clip if len(clip) < 60 else clip[:57] + "…"
        results.append(Suggestion(f"Link öffnen: {label}", lambda: win_api.shell_open(clip)))
    shot = _newest(Path.home() / "Pictures" / "Screenshots")
    if shot:
        results.append(Suggestion("Neuen Screenshot bearbeiten", lambda: win_api.shell_open("mspaint.exe", f'"{shot}"')))
    hwnds, area = _cursor_windows(ctx)
    if 2 <= len(hwnds) <= 4:
        results.append(Suggestion(f"{len(hwnds)} Fenster nebeneinander anordnen",
                                  lambda: win_api.arrange_side_by_side(hwnds, area)))
    return results


def wav_bytes(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class SpeechGate:
    """Merkt am Pegel, wann jemand spricht und wann er fertig ist. Die ersten Stücke zeigen, wie laut die Stille ist."""
    NOISE_BLOCKS = 3
    FLOOR = 350.0  # leiser ist nie Sprache
    FACTOR = 3.0
    NO_SPEECH = 8.0

    def __init__(self, silence: float):
        self.silence = silence
        self.noise: list[float] = []
        self.spoke = False
        self.last_voice = 0.0
        self.started: float | None = None

    def threshold(self) -> float:
        noise = sum(self.noise) / len(self.noise) if self.noise else 0.0
        return max(self.FLOOR, noise * self.FACTOR)

    def feed(self, level: float, now: float) -> None:
        if self.started is None:
            self.started = now
        if len(self.noise) < self.NOISE_BLOCKS and not self.spoke:
            self.noise.append(level)
            return
        if level >= self.threshold():
            self.spoke = True
            self.last_voice = now

    def finished(self, now: float) -> bool:
        if self.spoke:
            return now - self.last_voice >= self.silence
        return self.started is not None and now - self.started >= self.NO_SPEECH


def audio_level(data: bytes) -> float:
    import numpy
    samples = numpy.frombuffer(data[:len(data) - len(data) % 2], dtype=numpy.int16).astype(numpy.float32)
    return float(numpy.sqrt(numpy.mean(samples * samples))) if samples.size else 0.0


PARTIAL_SECONDS = 1.6  # so oft zeigt das Diktat, was es bisher verstanden hat


class VoiceInput(QObject):
    """Mikrofon nur, solange aufgenommen wird: Knopf startet und stoppt, das Limit stoppt notfalls.

    Mit `options` hört es auf, sobald man eine Pause macht, und zeigt unterwegs, was es schon verstanden hat."""
    state_changed = Signal(bool)
    text_ready = Signal(str)
    partial = Signal(str)
    failed = Signal(str)

    def __init__(self, ctx, key_source=None, options=None):
        super().__init__()
        self.ctx = ctx
        self.key_source = key_source or ctx.config.api_key  # Nova nimmt mit ihrem eigenen Key auf
        self.options = options  # () -> {"auto_stop", "silence", "live"}; ohne sie stoppt nur der Knopf
        self._stream = None
        self._frames: list[bytes] = []
        self._worker = None
        self._partial = None
        self._last_partial = 0.0
        self._started = 0.0
        self.gate: SpeechGate | None = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def toggle(self) -> None:
        if self.recording:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if not self.key_source():
            self.failed.emit("Für die Spracheingabe braucht es einen Gemini API-Key.")
            return
        self._frames = []
        self._started = self._last_partial = time.monotonic()
        self.gate = SpeechGate(float(self.options()["silence"])) if self.options else None
        try:
            import sounddevice as sd
            self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=self._on_audio)
            self._stream.start()
        except Exception as exc:
            self._stream = None
            log.warning("Mikrofon nicht verfügbar: %s", exc)
            self.failed.emit("Das Mikrofon konnte nicht geöffnet werden.")
            return
        self.state_changed.emit(True)

    def _on_audio(self, data, _frames, _time, _status) -> None:
        chunk = bytes(data)
        self._frames.append(chunk)
        if self.gate is not None:
            self.gate.feed(audio_level(chunk), time.monotonic())

    def check_limit(self) -> None:
        if not self.recording:
            return
        now = time.monotonic()
        options = self.options() if self.options else {}
        if now - self._started >= self.ctx.config.get("launcher.voice_seconds"):
            self.stop()
        elif options.get("auto_stop") and self.gate is not None and self.gate.finished(now):
            self.stop()
        elif options.get("live") and self.gate is not None and self.gate.spoke and now - self._last_partial >= PARTIAL_SECONDS:
            self._show_partial(now)

    def _show_partial(self, now: float) -> None:
        if self._partial is not None:  # eine Zwischenfassung auf einmal reicht
            return
        self._last_partial = now
        worker = TranscribeWorker(self.key_source(), self.ctx.config.models("search"), wav_bytes(b"".join(self._frames)), self)
        worker.transcribed.connect(lambda text: self.recording and text and self.partial.emit(text))
        worker.finished.connect(lambda: setattr(self, "_partial", None))
        worker.finished.connect(worker.deleteLater)
        self._partial = worker
        worker.start()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        stream.stop()
        stream.close()
        self.state_changed.emit(False)
        audio = b"".join(self._frames)
        if self.gate is not None and not self.gate.spoke:  # nur Stille: nichts verschicken, was Tokens kostet
            self.failed.emit("Ich habe nichts gehört.")
            return
        if not audio:
            return
        models = self.ctx.config.models("search")
        self._worker = TranscribeWorker(self.key_source(), models, wav_bytes(audio), self)
        self._worker.transcribed.connect(self.text_ready)
        self._worker.failed.connect(self.failed)
        self._worker.start()
