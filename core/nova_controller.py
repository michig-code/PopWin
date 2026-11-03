"""Nova als Teil von PopWin: Sie läuft im selben Programm und braucht nichts außer dem API-Key.

Das frühere Nova war ein eigener Prozess mit Python-Umgebung, WebView2 und Audio-Bausteinen. Fehlte eines davon, passierte
beim Start nichts, und ohne Key blieb das Fenster für die Eingabe versteckt. Jetzt zeigt der Chat selbst, was fehlt.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from datetime import datetime

import shiboken6
from PySide6.QtCore import QObject, QThread, Signal, Slot

from core import ai_actions, nova_brain, nova_tools
from core.nova_artifacts import ArtifactStore
from core.nova_watch import ArtifactWatcher
from core.gemini_client import client_for, friendly_error, should_try_next
from core.nova_brain import NOVA_DATA, ChatLog, Memory

log = logging.getLogger(__name__)

NOVA_KEY = "nova_api_key"
MAX_STEPS = 12  # Werkzeug-Runden pro Nachricht, damit sich nichts endlos im Kreis dreht
HISTORY_LIMITS = {"sparsam": 24, "qualitaet": 60}
IMAGE_NOTE = "[Bildschirmfoto, inzwischen entfernt]"
TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview")
TTS_VOICE = "Zephyr"
TTS_RATE = 24000
TTS_LIMIT = 1200
TTS_MIN_SENTENCE = 18  # kürzere Stücke klängen abgehackt
TTS_LONG_SENTENCE = 140
SENTENCE_END = re.compile(r"[.!?…:]+[\"')\]]*\s|\n+")
PAUSE = re.compile(r"[,;]\s|\s-\s")


def is_key_error(message: str) -> bool:
    return "API-Key ist ungültig" in message


def trim_history(contents: list, limit: int) -> list:
    """Kürzt von vorn und beginnt immer bei einer Nachricht des Hosts, nie mitten in einem Werkzeug-Aufruf."""
    if len(contents) <= limit:
        return contents
    for index in range(len(contents) - limit, len(contents)):
        content = contents[index]
        if content.role == "user" and not any(part.function_response for part in content.parts):
            return contents[index:]
    return []


def strip_images(contents: list) -> None:
    """Ein Bild ginge sonst mit jeder weiteren Nachricht erneut an die KI und kostete jedes Mal."""
    from google.genai import types
    for content in contents:
        content.parts = [types.Part(text=IMAGE_NOTE) if part.inline_data else part for part in content.parts]


class NovaTurn(QThread):
    """Eine Antwort von Nova samt ihrer Werkzeuge."""
    text = Signal(str)
    tool = Signal(str)
    approval = Signal(int, str, str, bool, bool)  # Nummer, Titel, Inhalt, gefährlich, darf für den Chat gelten
    failed = Signal(str)

    def __init__(self, nova: "NovaController", key: str, contents: list):
        super().__init__(nova)
        self.nova = nova
        self.key = key
        self.contents = contents
        self.cancelled = False
        self.discarded = False  # der Chat wurde geleert, nichts davon soll mehr erscheinen
        self.answer = ""
        self._waiting: dict[int, tuple[threading.Event, list]] = {}
        self._counter = 0

    def cancel(self) -> None:
        self.cancelled = True
        for event, _reply in self._waiting.values():
            event.set()

    def resolve(self, number: int, allowed: bool, for_chat: bool) -> None:
        if number in self._waiting:
            event, reply = self._waiting[number]
            reply[:] = [allowed, for_chat]
            event.set()

    def run(self) -> None:
        try:
            self._converse()
        except Exception as exc:
            log.warning("Nova: %s", str(exc)[:200])
            self.failed.emit(friendly_error(exc))

    def _settings(self):
        from google.genai import types
        nova = self.nova
        allowed = nova.ctx.config.get("nova")
        return types.GenerateContentConfig(
            system_instruction=nova_brain.system_prompt(nova.memory.as_prompt() if allowed["allow_memory"] else "", datetime.now(),
                                                        nova.artifacts.as_prompt()),
            tools=nova_tools.declarations(allowed))

    def _converse(self) -> None:
        from google.genai import types
        client = client_for(self.key)
        settings = self._settings()
        models = self.nova.models()
        for _step in range(MAX_STEPS):
            parts, models = self._answer(client, settings, models)
            if self.cancelled:
                return
            if not parts:
                self.failed.emit("Darauf kam keine Antwort. Versuch es bitte noch einmal.")
                return
            self.contents.append(types.Content(role="model", parts=parts))
            calls = [part.function_call for part in parts if part.function_call]
            if not calls:
                return
            self.contents.append(types.Content(role="user", parts=[part for call in calls for part in self._use_tool(call)]))
            if self.cancelled:
                return
        self.failed.emit("Das waren zu viele Schritte auf einmal. Ich habe hier angehalten.")

    def _answer(self, client, settings, models: list[str]) -> tuple[list, list[str]]:
        """Liefert die Antwort und die Kette ab dem Modell, das geantwortet hat: Es bleibt für den Rest der Nachricht."""
        last: Exception | None = None
        for index, model in enumerate(models):
            parts: list = []
            try:
                for chunk in client.models.generate_content_stream(model=model, contents=self.contents, config=settings):
                    if self.cancelled:
                        return parts, models
                    content = chunk.candidates[0].content if chunk.candidates else None
                    for part in (content.parts or []) if content else []:
                        parts.append(part)
                        if part.text and not part.thought:
                            self.answer += part.text
                            self.text.emit(part.text)
                return parts, models[index:]
            except Exception as exc:
                last = exc
                log.info("Nova: Modell %s fällt aus: %s", model, str(exc)[:120])
                if parts or not should_try_next(exc):
                    break
        raise last or RuntimeError("Keine Modelle eingestellt")

    def _permitted(self, name: str, arguments: dict) -> bool:
        tool = nova_tools.BY_NAME[name]
        toolbox = self.nova.toolbox
        if tool.risk == nova_tools.FREE or name in self.nova.allowed_now or toolbox.preapproved(name, arguments):
            return True
        shown = toolbox.preview(name, arguments)  # kann scheitern, dann fragt PopWin gar nicht erst
        detail, dangerous = shown or (nova_tools.approval_text(name, arguments), nova_tools.is_dangerous(name, arguments))
        self._counter += 1
        number = self._counter
        event, reply = threading.Event(), [False, False]
        self._waiting[number] = (event, reply)
        self.approval.emit(number, tool.label, detail, dangerous, tool.risk == nova_tools.ASK)
        event.wait()
        del self._waiting[number]
        if reply[1] and tool.risk == nova_tools.ASK:  # was immer fragt, fragt auch immer
            self.nova.allowed_now.add(name)
        return reply[0] and not self.cancelled

    def _use_tool(self, call) -> list:
        from google.genai import types
        name, arguments = call.name, dict(call.args or {})
        toolbox = self.nova.toolbox
        settings = self.nova.ctx.config.get("nova")
        tool = nova_tools.BY_NAME.get(name)
        try:
            permitted = tool is not None and nova_tools.available(tool, settings) and self._permitted(name, arguments)
            problem = ""
        except Exception as exc:  # etwa ein unbekanntes Artefakt oder eine DNS-Änderung, die so nicht geht
            permitted, problem = False, ai_actions.redact(str(exc))[:400]
        if tool is None or not nova_tools.available(tool, settings):
            result = {"ok": False, "result": "Dieses Werkzeug ist nicht erlaubt."}
        elif problem:
            result = {"ok": False, "result": problem}
        elif not permitted:
            result = {"ok": False, "result": "ABGELEHNT: Der Host hat das nicht erlaubt."}
        else:
            self.tool.emit(tool.label)
            result = toolbox.run(name, arguments)
            if self.answer:
                self.answer += "\n\n"
        parts = [types.Part(function_response=types.FunctionResponse(id=call.id, name=name, response=result))]
        image = toolbox.take_image()
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
        return parts


def speakable(text: str) -> str:
    """Was vorgelesen werden soll: ohne Markdown-Zeichen, aus Links bleibt der Text."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return " ".join(re.sub(r"[*_#`>~|]", "", text).split())


class SentenceCutter:
    """Schneidet den ankommenden Text in Sätze. Je früher ein Stück fertig ist, desto früher kann Nova sprechen."""

    def __init__(self):
        self.buffer = ""
        self.in_code = False

    def feed(self, chunk: str) -> list[str]:
        self.buffer += chunk
        sentences = []
        while True:
            match = SENTENCE_END.search(self.buffer, TTS_MIN_SENTENCE)
            if match is None and len(self.buffer) > TTS_LONG_SENTENCE:
                match = PAUSE.search(self.buffer, TTS_MIN_SENTENCE)
            if match is None:
                return sentences
            piece, self.buffer = self.buffer[:match.end()], self.buffer[match.end():]
            sentences += self._clean(piece)

    def flush(self) -> list[str]:
        piece, self.buffer = self.buffer, ""
        return self._clean(piece)

    def _clean(self, piece: str) -> list[str]:
        spoken = []
        for part in re.split(r"(```)", piece):
            if part == "```":
                self.in_code = not self.in_code  # Code vorzulesen hilft niemandem
            elif not self.in_code and (text := speakable(part)):
                spoken.append(text)
        return spoken


class LiveSpeech(QThread):
    """Spricht, während die Antwort entsteht. Ein Thread holt Satz für Satz den Ton, dieser hier spielt ihn sofort ab,
    noch bevor der Satz ganz vertont ist. So wächst der Abstand zum Text nicht mit der Länge der Antwort."""

    def __init__(self, key: str, parent=None, voice: str = TTS_VOICE, style: str = ""):
        super().__init__(parent)
        self.key = key
        self.voice = voice or TTS_VOICE
        self.style = style
        self.cutter = SentenceCutter()
        self.sentences: queue.Queue = queue.Queue()
        self.sound: queue.Queue = queue.Queue()
        self.stopped = threading.Event()
        self.spoken = 0

    @Slot(str)
    def feed(self, chunk: str) -> None:
        self._queue(self.cutter.feed(chunk))

    def _queue(self, sentences: list[str]) -> None:
        for sentence in sentences:
            if self.spoken < TTS_LIMIT:
                self.spoken += len(sentence)
                self.sentences.put(sentence)

    def finish(self) -> None:
        self._queue(self.cutter.flush())
        self.sentences.put(None)

    def stop(self) -> None:
        self.stopped.set()
        self.sentences.put(None)
        self.sound.put(None)

    def _settings(self):
        from google.genai import types
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))))

    def _voice(self, client, sentence: str, models: list[str]) -> None:
        """Bleibt beim ersten Modell, das antwortet: Ein Wechsel mitten im Text hörte sich falsch an."""
        for model in list(models):
            try:
                started = False
                spoken = f"Sprich {self.style.strip().rstrip(':')}: {sentence}" if self.style.strip() else sentence
                for chunk in client.models.generate_content_stream(model=model, contents=spoken, config=self._settings()):
                    content = chunk.candidates[0].content if chunk.candidates else None
                    for part in (content.parts or []) if content else []:
                        if self.stopped.is_set():
                            return
                        if part.inline_data and part.inline_data.data:
                            started = True
                            self.sound.put(part.inline_data.data)
                if started:
                    return
            except Exception as exc:
                log.info("Vorlesen mit %s fehlgeschlagen: %s", model, str(exc)[:120])
            models.remove(model)

    def _fetch(self) -> None:
        client = client_for(self.key)
        models = list(TTS_MODELS)
        while not self.stopped.is_set():
            sentence = self.sentences.get()
            if sentence is None or not models:
                break
            self._voice(client, sentence, models)
        self.sound.put(None)

    def _open_output(self):
        import sounddevice
        output = sounddevice.RawOutputStream(samplerate=TTS_RATE, channels=1, dtype="int16")
        output.start()
        return output

    def run(self) -> None:
        threading.Thread(target=self._fetch, daemon=True, name="nova-tts").start()
        output = None
        rest = b""
        try:
            while True:
                data = self.sound.get()
                if data is None or self.stopped.is_set():
                    break
                data = rest + data
                usable = len(data) - len(data) % 2  # Ton kommt in Blöcken, die mitten im 16-Bit-Wert enden können
                rest = data[usable:]
                output = output or self._open_output()
                output.write(data[:usable])
        except Exception as exc:  # kein Lautsprecher oder gerade umgeschaltet: Der Text steht ja im Chat
            log.info("Abspielen fehlgeschlagen: %s", str(exc)[:120])
        finally:
            if output is not None:
                (output.abort if self.stopped.is_set() else output.stop)()
                output.close()


class NovaController(QObject):
    busy_changed = Signal(bool)
    text = Signal(str)
    tool = Signal(str)
    approval = Signal(int, str, str, bool, bool)
    failed = Signal(str)
    user_message = Signal(str)
    finished = Signal()
    cleared = Signal()
    artifacts_changed = Signal()
    notice = Signal(str, str, str)  # Titel, Meldung, Kennung des Artefakts
    connection_wanted = Signal(str)  # Nova bittet den Host, die Keys einer Verbindung einzutragen
    heard = Signal(str)  # live: was Nova gerade vom Host hört
    stage = Signal(str)
    listening = Signal(bool)
    _main_call = Signal(object)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.memory = Memory(NOVA_DATA / "memory.json", nova_brain.OLD_MEMORY)
        self.log = ChatLog(NOVA_DATA / "chat.json")
        self.artifacts = ArtifactStore()
        self.toolbox = nova_tools.Toolbox(ctx, self.memory, self._on_main, self.artifacts, self.artifacts_changed.emit)
        self.toolbox.on_connection = self.connection_wanted.emit
        self.watcher = ArtifactWatcher(self)
        self.allowed_now: set[str] = set()  # gilt nur für diesen Chat
        self.contents: list | None = None
        self.turn: NovaTurn | None = None
        self.speech: LiveSpeech | None = None
        self.waiting: list[str] = []
        self._main_call.connect(self._run_on_main)

    # Bleiben, weil Suche und Schnelleinstellungen sie benutzen.
    @property
    def installed(self) -> bool:
        return True

    @property
    def running(self) -> bool:
        return self.ctx.nova_panel.isVisible()

    @property
    def busy(self) -> bool:
        return self.turn is not None

    def toggle(self) -> None:
        self.ctx.nova_panel.toggle()

    def api_key(self) -> str:
        """Ihr eigener Key oder der von PopWin: Auf einem neuen PC reicht einer von beiden."""
        return self.ctx.config.api_key(NOVA_KEY) or self.ctx.config.api_key()

    def save_key(self, key: str) -> bool:
        return self.ctx.config.set_api_key(key.strip(), NOVA_KEY)

    def models(self) -> list[str]:
        return [model.strip() for model in self.ctx.config.get("nova.models").split(",") if model.strip()]

    def _on_main(self, function):
        holder: list = []
        done = threading.Event()
        self._main_call.emit(lambda: (holder.append(function()), done.set()))
        done.wait(10)
        return holder[0] if holder else "PopWin antwortet gerade nicht."

    @Slot(object)
    def _run_on_main(self, call) -> None:
        call()

    def _history(self) -> list:
        from google.genai import types
        if self.contents is None:  # nach einem Neustart kennt Nova den sichtbaren Chat wieder
            self.contents = [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["text"])])
                             for m in self.log.messages]
        limit = HISTORY_LIMITS.get(self.ctx.config.get("nova.economy"), HISTORY_LIMITS["sparsam"])
        self.contents = trim_history(self.contents, limit)
        return self.contents

    def ask(self, text: str) -> None:
        """Öffnet den Chat und schickt die Nachricht; läuft schon eine Antwort, kommt sie danach dran."""
        self.ctx.nova_panel.reveal()
        self.send(text)

    def send(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.busy:
            self.waiting.append(text)
            return
        key = self.api_key()
        if not key:
            self.ctx.nova_panel.ask_for_key(text)
            return
        from google.genai import types
        self.log.add("user", text)
        self.user_message.emit(text)
        contents = self._history()
        contents.append(types.Content(role="user", parts=[types.Part(text=text)]))
        if self.live_wanted():  # mit Stimme antwortet Nova über Gemini Live, getippt wie gesprochen
            from core.nova_voice import LiveTalk
            self._silence()
            self._begin(LiveTalk(self, key, contents, typed=text))
            return
        turn = NovaTurn(self, key, contents)
        self._silence()
        if self.ctx.config.get("nova.speak"):
            self.speech = LiveSpeech(key, self, self.ctx.config.get("nova.voice"), self.ctx.config.get("nova.voice_style"))
            self.speech.finished.connect(self.speech.deleteLater)
            turn.text.connect(self.speech.feed)
            self.speech.start()
        self._begin(turn)

    def live_wanted(self) -> bool:
        settings = self.ctx.config.get("nova")
        return bool(settings["speak"]) and settings["speak_engine"] == "live"

    def _begin(self, turn: NovaTurn) -> None:
        turn.text.connect(self.text)
        turn.tool.connect(self.tool)
        turn.approval.connect(self.approval)
        turn.failed.connect(self.failed)
        turn.finished.connect(self._finish)
        for name in ("heard", "stage", "listening"):  # nur das Gespräch über Gemini Live hat sie
            if hasattr(turn, name):
                getattr(turn, name).connect(getattr(self, name))
        self.turn = turn
        self.busy_changed.emit(True)
        turn.start()

    def talk(self) -> None:
        """Ein Gespräch über Gemini Live. Ein Klick, während Nova zuhört, heißt: fertig gesprochen."""
        from core.nova_voice import LiveTalk
        if isinstance(self.turn, LiveTalk):
            self.turn.finish_speaking()
            return
        if self.busy:
            return
        key = self.api_key()
        if not key:
            self.ctx.nova_panel.ask_for_key("")
            return
        self._silence()
        self._begin(LiveTalk(self, key, self._history()))

    def resolve(self, number: int, allowed: bool, for_chat: bool = False) -> None:
        if self.turn:
            self.turn.resolve(number, allowed, for_chat)

    def cancel(self) -> None:
        self.waiting.clear()
        self._silence()
        if self.turn:
            self.turn.cancel()

    @Slot()
    def _finish(self) -> None:
        from google.genai import types
        turn = self.sender()
        self.turn = None
        strip_images(turn.contents)
        heard = getattr(turn, "heard_text", "").strip()
        if heard and not turn.discarded:  # Gesprochenes steht danach wie Getipptes im Chat
            self.log.add("user", heard)
            turn.contents.append(types.Content(role="user", parts=[types.Part(text=heard)]))
        if hasattr(turn, "heard_text") and turn.answer.strip():
            turn.contents.append(types.Content(role="model", parts=[types.Part(text=turn.answer.strip())]))
        if turn.answer.strip() and not turn.discarded:
            self.log.add("nova", turn.answer.strip())
        if self._speaking():
            self.speech.finish()
        turn.deleteLater()
        self.finished.emit()
        self.busy_changed.emit(False)
        if self.waiting:
            self.send(self.waiting.pop(0))

    def _speaking(self) -> bool:
        return self.speech is not None and shiboken6.isValid(self.speech) and self.speech.isRunning()

    def _silence(self) -> None:
        if self._speaking():
            self.speech.stop()

    def new_chat(self) -> None:
        self.cancel()
        turn = self.turn
        if turn:
            turn.discarded = True
            for signal, target in ((turn.text, self.text), (turn.tool, self.tool), (turn.approval, self.approval),
                                   (turn.failed, self.failed)):
                signal.disconnect(target)
            for name in ("heard", "stage", "listening"):
                if hasattr(turn, name):
                    getattr(turn, name).disconnect(getattr(self, name))
        self.contents = None
        self.allowed_now.clear()
        self.log.clear()
        self.cleared.emit()

    @property
    def connections(self):
        return self.toolbox.connections

    def start(self) -> None:
        """Beim Start von PopWin: Überwachungen laufen lassen; der Dev-Build bringt sein OVH-Beispiel mit."""
        from core.dev import is_dev
        if is_dev():
            import importlib
            try:  # über importlib, damit PyInstaller devtools nie einpackt
                importlib.import_module("devtools.nova_seed").seed(self)
            except Exception as exc:
                log.warning("Dev-Beispiele für Nova nicht angelegt: %s", exc)
        self.watcher.start()

    def use_artifacts(self, store: ArtifactStore) -> None:
        """Ein anderer Ordner für die Artefakte; die Tests arbeiten so nie mit den echten."""
        self.artifacts = self.toolbox.artifacts = store
        self.artifacts_changed.emit()

    def stop(self) -> None:
        """Beim Beenden: Nichts soll nach dem Ende von PopWin noch laufen."""
        self.watcher.stop()
        self.cancel()
        if self.turn:
            self.turn.wait(1500)
