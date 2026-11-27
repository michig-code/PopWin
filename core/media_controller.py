"""Live-Medienanzeige über die Windows Global System Media Transport Controls."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)
_PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
READ_TIMEOUT = 4.0
RECONNECT_AFTER = 10  # Abfragen ohne Player
SEEK_JUMP = 3.0  # Sekunden: springt die Stelle weiter, hat jemand im Song gespult


def media_changed(old, new, elapsed: float) -> bool:
    """Neu senden nur, wenn sich wirklich etwas ändert. Dass die Stelle im Song weiterläuft, rechnet die
    Anzeige selbst; nur ein Sprung (Spulen) zählt als Änderung."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return old != new
    if {k: v for k, v in old.items() if k != "position"} != {k: v for k, v in new.items() if k != "position"}:
        return True
    expected = old.get("position", 0.0) + (elapsed if old.get("playing") else 0.0)
    return abs(new.get("position", 0.0) - expected) > SEEK_JUMP


def song_position(position: float, duration: float, playing: bool, updated, now) -> float:
    """Windows liefert die Stelle vom Zeitpunkt der letzten Meldung des Players; bis jetzt ist sie weitergelaufen."""
    if playing and updated is not None and now is not None:
        try:
            position += max(0.0, (now - updated).total_seconds())
        except TypeError:  # eine Zeit ohne Zeitzone
            pass
    return max(0.0, min(position, duration)) if duration > 0 else 0.0


class MediaController(QThread):
    updated = Signal(object)  # dict oder None, wenn nichts läuft

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager = None
        self._stop = False
        self._last_key = None
        self._thumbnail = b""
        self._last_state = object()
        self._sent_at = 0.0
        self._idle = 0

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._poll())
        finally:
            self._loop.close()

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)

    async def _connect(self) -> None:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager)
        try:
            self._manager = await asyncio.wait_for(Manager.request_async(), READ_TIMEOUT)
        except Exception as exc:
            log.warning("Medien-Sitzungen nicht erreichbar: %r", exc)

    async def _poll(self) -> None:
        await self._connect()
        while not self._stop:
            state = await self._next_state()
            now = time.monotonic()
            if media_changed(self._last_state, state, now - self._sent_at):
                self._last_state = state
                self._sent_at = now
                self.updated.emit(state)
            await asyncio.sleep(1)

    async def _next_state(self) -> dict | None:
        """Ein hängender Player darf die Anzeige nicht für immer anhalten: Windows antwortet dann schlicht nie."""
        try:
            state = await asyncio.wait_for(self._read(), READ_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Medienabfrage hängt, verbinde neu")
            await self._connect()
            state = None
        except Exception as exc:  # Player verschwinden jederzeit mitten in der Abfrage
            log.debug("Medienabfrage: %s", exc)
            state = None
        self._idle = self._idle + 1 if state is None else 0
        if self._idle >= RECONNECT_AFTER:  # eine alte Verbindung bekommt neue Player nicht immer mit
            self._idle = 0
            await self._connect()
        return state

    async def _read(self) -> dict | None:
        session = self._manager.get_current_session() if self._manager else None
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        playing = info.playback_status == _PLAYING
        position, duration = self._timeline(session, playing)
        key = (props.title, props.artist)
        if key != self._last_key:
            self._last_key = key
            self._thumbnail = await self._read_thumbnail(props.thumbnail)
        return {
            "title": props.title or "",
            "artist": props.artist or "",
            "app": session.source_app_user_model_id or "",
            "playing": playing,
            "thumbnail": self._thumbnail,
            "position": position,
            "duration": duration,
        }

    @staticmethod
    def _timeline(session, playing: bool) -> tuple[float, float]:
        """Stelle und Länge in Sekunden; Player ohne Zeitleiste (manche Browser) liefern 0, 0."""
        try:
            timeline = session.get_timeline_properties()
            duration = (timeline.end_time - timeline.start_time).total_seconds()
            return song_position(timeline.position.total_seconds(), duration, playing,
                                 timeline.last_updated_time, datetime.now(timezone.utc)), duration
        except Exception as exc:
            log.debug("Zeitleiste nicht lesbar: %s", exc)
            return 0.0, 0.0

    @staticmethod
    async def _read_thumbnail(reference) -> bytes:
        if reference is None:
            return b""
        from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions
        try:
            stream = await reference.open_read_async()
            buffer = Buffer(stream.size)
            await stream.read_async(buffer, buffer.capacity, InputStreamOptions.READ_AHEAD)
            reader = DataReader.from_buffer(buffer)
            data = bytearray(buffer.length)
            reader.read_bytes(data)
            return bytes(data)
        except Exception as exc:
            log.debug("Thumbnail nicht lesbar: %s", exc)
            return b""

    def _command(self, name: str) -> None:
        async def run():
            session = self._manager.get_current_session() if self._manager else None
            if session is not None:
                await getattr(session, name)()
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(run(), self._loop)

    def play_pause(self) -> None:
        self._command("try_toggle_play_pause_async")

    def next(self) -> None:
        self._command("try_skip_next_async")

    def previous(self) -> None:
        self._command("try_skip_previous_async")
