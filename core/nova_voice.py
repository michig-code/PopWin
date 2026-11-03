"""Mit Nova sprechen wie früher: über die Gemini-Live-Verbindung, Ton rein und Ton raus, beides live mitgeschrieben.

Das Mikrofon ist nur offen, solange der Host spricht. Gemini erkennt selbst, wann er fertig ist; dann geht das Mikro zu,
bevor Nova antwortet. Ein offenes Headset-Mikrofon zwingt Bluetooth-Kopfhörer in den Telefon-Modus, und alles klänge dünn.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from datetime import datetime

from PySide6.QtCore import Signal

from core import nova_brain, nova_tools
from core.gemini_client import client_for, friendly_error
from core.nova_controller import NovaTurn

log = logging.getLogger(__name__)

# Dieselben Stimmen wie beim alten Nova, mit dem, wonach sie klingen.
VOICES = {
    "Zephyr": "Hell", "Puck": "Aufgeweckt", "Charon": "Sachlich", "Kore": "Bestimmt", "Fenrir": "Temperamentvoll",
    "Leda": "Jugendlich", "Orus": "Fest", "Aoede": "Luftig", "Callirrhoe": "Entspannt", "Autonoe": "Strahlend",
    "Enceladus": "Hauchig", "Iapetus": "Klar", "Umbriel": "Gelassen", "Algieba": "Geschmeidig", "Despina": "Sanft",
    "Erinome": "Deutlich", "Algenib": "Rau", "Rasalgethi": "Informativ", "Laomedeia": "Munter", "Achernar": "Weich",
    "Alnilam": "Kraftvoll", "Schedar": "Ausgeglichen", "Gacrux": "Reif", "Pulcherrima": "Direkt", "Achird": "Freundlich",
    "Zubenelgenubi": "Lässig", "Vindemiatrix": "Zart", "Sadachbia": "Lebhaft", "Sadaltager": "Kenntnisreich", "Sulafat": "Warm",
}
RATE_IN, RATE_OUT = 16000, 24000
BLOCK = 1600  # 100 ms Ton pro Stück
NO_SPEECH_SECONDS = 8  # so lange wartet das offene Mikro auf das erste Wort
CONTEXT_MESSAGES = 16  # so viel vom Text-Chat kennt Nova im Gespräch
QUESTION_END = re.compile(r"\?[\W_]*$")


def voice_name(name: str) -> str:
    return name if name in VOICES else "Zephyr"


def voice_rule(style: str) -> str:
    return f"\n\nSTIMME: Sprich so: {style.strip()}." if style.strip() else ""


def text_context(contents: list) -> list:
    """Nur der lesbare Teil des Chats; halbe Werkzeug-Aufrufe brächten die Live-Verbindung durcheinander."""
    from google.genai import types
    kept = []
    for content in contents[-CONTEXT_MESSAGES:]:
        texts = [part.text for part in content.parts or [] if part.text and not part.thought]
        if texts:
            kept.append(types.Content(role=content.role, parts=[types.Part(text="\n".join(texts))]))
    return kept


class Speaker:
    """Spielt Nova ab, während der Ton noch ankommt."""

    def __init__(self):
        self.sound: queue.Queue = queue.Queue()
        self.playing = threading.Event()
        self._rest = b""
        self._thread = threading.Thread(target=self._run, daemon=True, name="nova-live-out")
        self._thread.start()

    def play(self, data: bytes) -> None:
        self.playing.set()
        self.sound.put(data)

    def flush(self) -> None:
        """Nova wurde unterbrochen: Was noch in der Schlange steht, fällt weg."""
        while not self.sound.empty():
            try:
                self.sound.get_nowait()
            except queue.Empty:
                break
        self.playing.clear()

    def close(self) -> None:
        self.flush()
        self.sound.put(None)

    def idle(self) -> bool:
        return self.sound.empty() and not self.playing.is_set()

    def _run(self) -> None:
        output = None
        try:
            while True:
                try:
                    data = self.sound.get(timeout=0.25)
                except queue.Empty:
                    self.playing.clear()
                    continue
                if data is None:
                    break
                data = self._rest + data
                usable = len(data) - len(data) % 2
                self._rest = data[usable:]
                if output is None:
                    import sounddevice
                    output = sounddevice.RawOutputStream(samplerate=RATE_OUT, channels=1, dtype="int16")
                    output.start()
                output.write(data[:usable])
                if self.sound.empty():
                    self.playing.clear()
        except Exception as exc:  # kein Lautsprecher: Der Text steht ja im Chat
            log.info("Live-Ton nicht abspielbar: %s", str(exc)[:120])
        finally:
            if output is not None:
                output.abort()
                output.close()


class Microphone:
    """Offen nur zwischen open() und close(). Die Stücke gehen direkt in die Schlange der Live-Verbindung."""

    def __init__(self, loop: asyncio.AbstractEventLoop, target: asyncio.Queue):
        self.loop = loop
        self.target = target
        self.stream = None
        self.opened_at = 0.0

    def open(self) -> None:
        if self.stream is not None:
            return
        import sounddevice
        self.stream = sounddevice.RawInputStream(samplerate=RATE_IN, channels=1, dtype="int16", blocksize=BLOCK,
                                                 callback=self._on_audio)
        self.stream.start()
        self.opened_at = time.monotonic()

    def _on_audio(self, data, _frames, _time, _status) -> None:
        try:
            self.loop.call_soon_threadsafe(self.target.put_nowait, bytes(data))
        except RuntimeError:  # die Verbindung ist schon zu
            pass

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    @property
    def is_open(self) -> bool:
        return self.stream is not None


class LiveTalk(NovaTurn):
    """Ein Gespräch: Mikro auf, der Host spricht, Nova antwortet mit Stimme. Werkzeuge fragen genau wie im Text-Chat."""
    heard = Signal(str)  # was Gemini gerade vom Host verstanden hat, Stück für Stück
    stage = Signal(str)  # verbindet, hört zu, denkt nach, spricht
    listening = Signal(bool)

    def __init__(self, nova, key: str, contents: list, typed: str = ""):
        super().__init__(nova, key, contents)
        self.typed = typed  # getippt statt gesprochen: Nova antwortet trotzdem mit Stimme
        self.heard_text = ""
        self.stop_listening = threading.Event()

    def run(self) -> None:
        try:
            asyncio.run(self._talk())
        except Exception as exc:
            log.warning("Nova live: %s", str(exc)[:200])
            self.failed.emit(friendly_error(exc))

    def _config(self):
        from google.genai import types
        nova = self.nova
        settings = nova.ctx.config.get("nova")
        prompt = nova_brain.system_prompt(nova.memory.as_prompt() if settings["allow_memory"] else "", datetime.now(),
                                          nova.artifacts.as_prompt())
        prompt += voice_rule(settings["voice_style"]) + "\n\nDu sprichst gerade mit dem Host. Antworte kurz und natürlich, wie im Gespräch."
        detection = types.AutomaticActivityDetection(
            disabled=False, silence_duration_ms=int(settings["listen_silence"] * 1000),
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH)
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO], system_instruction=prompt,
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name(settings["voice"])))),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(automatic_activity_detection=detection),
            tools=nova_tools.declarations(settings))

    async def _talk(self) -> None:
        from google.genai import types
        settings = self.nova.ctx.config.get("nova")
        client = client_for(self.key)
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue = asyncio.Queue()
        microphone = Microphone(loop, chunks)
        speaker = Speaker()
        self.stage.emit("verbindet")
        try:
            async with client.aio.live.connect(model=settings["live_model"], config=self._config()) as session:
                context = text_context(self.contents[:-1] if self.typed else self.contents)  # das Getippte geht gleich als Frage raus
                if context:
                    await session.send_client_content(turns=context, turn_complete=False)
                sender = asyncio.create_task(self._send_audio(session, chunks, microphone))
                try:
                    typed = self.typed
                    while not self.cancelled:
                        if typed:
                            await session.send_client_content(turns=[types.Content(role="user", parts=[types.Part(text=typed)])],
                                                              turn_complete=True)
                            self.stage.emit("denkt nach")
                            typed = ""
                        else:
                            microphone.open()
                            self.listening.emit(True)
                            self.stage.emit("hört zu")
                        finished = await self._one_turn(session, microphone, speaker)
                        if not finished or self.cancelled:
                            break
                        while not speaker.idle() and not self.cancelled:
                            await asyncio.sleep(0.1)
                        # Fragt Nova zurück, hört sie danach gleich wieder zu, wie früher
                        if not (settings["listen_followup"] and QUESTION_END.search(self.answer.strip())):
                            break
                        self.answer += "\n\n"
                        self.heard_text += "\n"
                finally:
                    sender.cancel()
        finally:
            microphone.close()
            self.listening.emit(False)
            if self.cancelled:
                speaker.close()
            else:
                while not speaker.idle():
                    await asyncio.sleep(0.1)
                speaker.close()

    async def _send_audio(self, session, chunks: asyncio.Queue, microphone: Microphone) -> None:
        from google.genai import types
        while True:
            chunk = await chunks.get()
            if microphone.is_open:  # was nach dem Zumachen noch ankommt, gehört nicht mehr zur Frage
                await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={RATE_IN}"))

    def _stop_microphone(self, microphone: Microphone) -> None:
        if microphone.is_open:
            microphone.close()
            self.listening.emit(False)
            self.stage.emit("denkt nach")

    async def _one_turn(self, session, microphone: Microphone, speaker: Speaker) -> bool:
        """Eine Frage und Novas Antwort darauf, samt Werkzeugen. Falsch heißt: nichts gehört oder abgebrochen."""
        from google.genai import types
        limit = self.nova.ctx.config.get("launcher.voice_seconds")
        spoke = False
        receiver = session.receive().__aiter__()
        while not self.cancelled:
            if microphone.is_open:
                waited = time.monotonic() - microphone.opened_at
                if self.stop_listening.is_set():  # Klick aufs Mikro: fertig gesprochen
                    self.stop_listening.clear()
                    self._stop_microphone(microphone)
                    await session.send_realtime_input(audio_stream_end=True)
                elif not spoke and waited > NO_SPEECH_SECONDS:
                    self._stop_microphone(microphone)
                    self.failed.emit("Ich habe nichts gehört.")
                    return False
                elif waited > limit:
                    self._stop_microphone(microphone)
                    await session.send_realtime_input(audio_stream_end=True)
            try:
                message = await asyncio.wait_for(receiver.__anext__(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                receiver = session.receive().__aiter__()  # receive() endet nach jedem Zug
                continue
            if message.tool_call:
                self._stop_microphone(microphone)
                responses = []
                for call in message.tool_call.function_calls:
                    parts = await asyncio.to_thread(self._use_tool, call)
                    responses.append(parts[0].function_response)
                    for part in parts[1:]:
                        if part.inline_data:  # das Bildschirmfoto geht als Bild in die Verbindung
                            await session.send_realtime_input(video=types.Blob(data=part.inline_data.data, mime_type="image/jpeg"))
                await session.send_tool_response(function_responses=responses)
                self.stage.emit("denkt nach")
                continue
            content = message.server_content
            if not content:
                continue
            if content.input_transcription and content.input_transcription.text:
                spoke = True
                self.heard_text += content.input_transcription.text
                self.heard.emit(content.input_transcription.text)
            if content.output_transcription and content.output_transcription.text:
                self._stop_microphone(microphone)
                self.answer += content.output_transcription.text
                self.text.emit(content.output_transcription.text)
            for part in (content.model_turn.parts or []) if content.model_turn else []:
                if part.inline_data and part.inline_data.data:
                    self._stop_microphone(microphone)
                    self.stage.emit("spricht")
                    speaker.play(part.inline_data.data)
            if content.interrupted:
                speaker.flush()
            if content.turn_complete:
                return True
        return False

    def finish_speaking(self) -> None:
        """Der Host sagt per Klick, dass er fertig ist; sonst merkt Gemini das an der Pause."""
        self.stop_listening.set()
