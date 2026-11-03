"""Live-Medienanzeige über die Windows Global System Media Transport Controls."""
from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)
_PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
READ_TIMEOUT = 4.0
RECONNECT_AFTER = 10  # Abfragen ohne Player


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
            if state != self._last_state:
                self._last_state = state
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
        key = (props.title, props.artist)
        if key != self._last_key:
            self._last_key = key
            self._thumbnail = await self._read_thumbnail(props.thumbnail)
        return {
            "title": props.title or "",
            "artist": props.artist or "",
            "app": session.source_app_user_model_id or "",
            "playing": info.playback_status == _PLAYING,
            "thumbnail": self._thumbnail,
        }

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
