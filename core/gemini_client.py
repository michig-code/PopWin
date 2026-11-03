"""Gemini im Hintergrund: Modell-Kette, Aktionsplanung, Fehleranalyse, Transkription."""
from __future__ import annotations

import json
import logging
import re

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du antwortest in einem kleinen Desktop-Launcher. Antworte knapp, höchstens ein paar Sätze, "
    "in der Sprache der Frage. Reiner Text, kein Markdown."
)
PLAN_PROMPT = (
    "Du steuerst einen Windows-11-PC für den Nutzer. Antworte nur mit JSON:\n"
    '{"antwort": "kurzer Text, wenn es nichts auszuführen gibt, sonst leer", '
    '"aufgaben": [{"action_summary": "kurze Erklärung auf Deutsch", "kind": "powershell|cmd|uri|setting", '
    '"command": "ausführbarer Befehl oder URI", "setting": "Schlüssel bei kind=setting", "value": "neuer Wert"}]}\n'
    "Regeln: Zerlege größere Aufträge in einzelne Schritte, die nacheinander laufen. Erfinde keine Pfade. "
    "Ist es nur eine Frage, gib aufgaben leer zurück. Für Windows-Einstellungen nutze ms-settings:-URIs. "
    "PopWin-Einstellungen änderst du nur über kind=setting mit diesen Schlüsseln:\n"
)
REPAIR_PROMPT = (
    "Ein Windows-Befehl ist fehlgeschlagen. Antworte nur mit JSON: "
    '{"ursache": "kurz auf Deutsch", "erklaerung": "was der Nutzer wissen muss", '
    '"kind": "powershell|cmd|uri", "command": "korrigierter Befehl oder leer", "action_summary": "kurz"}. '
    "Erfinde keine Pfade oder Laufwerke. Fehlt eine Information, lass command leer und erkläre es."
)
TRANSCRIBE_PROMPT = "Schreibe wortgetreu auf, was gesagt wird. Nur den Text, keine Erklärung."
TIMEOUT_MS = 45000
_SKIP_MARKERS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "404", "NOT_FOUND", "timed out",
                 "DEADLINE_EXCEEDED", "500", "INTERNAL")


def should_try_next(exc: Exception) -> bool:
    """Ausgeschöpft oder nicht erreichbar: Das nächste Modell der Kette kann helfen, ein falscher Key nicht."""
    text = str(exc)
    return any(marker in text for marker in _SKIP_MARKERS)


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_tasks(data: dict) -> list[dict]:
    """Macht aus der KI-Antwort saubere Aufgaben; Unvollständiges fliegt raus."""
    tasks = []
    for item in data.get("aufgaben") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") if item.get("kind") in ("powershell", "cmd", "uri", "setting") else "powershell"
        command = str(item.get("command") or "").strip()
        setting = str(item.get("setting") or "").strip()
        if (kind == "setting" and setting) or (kind != "setting" and command):
            summary = str(item.get("action_summary") or command or setting)
            tasks.append({"summary": summary, "kind": kind, "command": command, "setting": setting,
                          "value": item.get("value")})
    return tasks


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "API_KEY_INVALID" in text or "API key not valid" in text:
        return "Der Gemini API-Key ist ungültig. Prüfe ihn unter Einstellungen → Setup."
    if should_try_next(exc):
        return "Kein Gemini-Modell der Kette ist gerade verfügbar. Das Kontingent ist vermutlich ausgeschöpft."
    return f"Gemini hat nicht geantwortet: {text[:160]}"


def client_for(api_key: str):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))


def generate(api_key: str, models: list[str], contents, system: str) -> tuple[str, str]:
    """Probiert die Kette der Reihe nach und gibt (Text, Modell) des ersten Erfolgs zurück."""
    from google.genai import types
    client = client_for(api_key)
    last: Exception | None = None
    for model in models:
        try:
            reply = client.models.generate_content(
                model=model, contents=contents, config=types.GenerateContentConfig(system_instruction=system))
            return reply.text or "", model
        except Exception as exc:
            last = exc
            log.info("Modell %s fällt aus: %s", model, str(exc)[:120])
            if not should_try_next(exc):
                break
    raise last or RuntimeError("Keine Modelle eingestellt")


class GeminiWorker(QThread):
    """Chat-Antwort, die Stück für Stück erscheint."""
    chunk = Signal(int, str)
    failed = Signal(int, str)
    done = Signal(int)
    model_used = Signal(int, str)

    def __init__(self, request_id: int, api_key: str, models: list[str], prompt: str, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.api_key = api_key
        self.models = models
        self.prompt = prompt

    def run(self) -> None:
        from google.genai import types
        client = client_for(self.api_key)
        last: Exception | None = None
        for model in self.models:
            started = False
            try:
                stream = client.models.generate_content_stream(
                    model=model, contents=self.prompt,
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT))
                for part in stream:
                    if part.text:
                        if not started:
                            self.model_used.emit(self.request_id, model)
                        started = True
                        self.chunk.emit(self.request_id, part.text)
                self.done.emit(self.request_id)
                return
            except Exception as exc:
                last = exc
                log.warning("Gemini-Fehler (%s): %s", model, str(exc)[:160])
                if started or not should_try_next(exc):
                    break
        self.failed.emit(self.request_id, friendly_error(last or RuntimeError("Keine Modelle eingestellt")))


class PlanWorker(QThread):
    """Fragt die KI nach einem Plan und liefert Aufgaben oder eine Textantwort."""
    planned = Signal(int, dict, str)
    failed = Signal(int, str)

    def __init__(self, request_id: int, api_key: str, models: list[str], prompt: str, catalog: str, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.api_key = api_key
        self.models = models
        self.prompt = prompt
        self.catalog = catalog

    def run(self) -> None:
        try:
            text, model = generate(self.api_key, self.models, self.prompt, PLAN_PROMPT + self.catalog)
            data = parse_json(text)
            self.planned.emit(self.request_id, {"answer": str(data.get("antwort") or ("" if data else text)),
                                                "tasks": normalize_tasks(data)}, model)
        except Exception as exc:
            self.failed.emit(self.request_id, friendly_error(exc))


class RepairWorker(QThread):
    """Untersucht einen fehlgeschlagenen Task. `analysed` liefert Ursache und Korrektur."""
    analysed = Signal(dict, str)

    def __init__(self, api_key: str, models: list[str], report: str, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.models = models
        self.report = report

    def run(self) -> None:
        try:
            text, model = generate(self.api_key, self.models, self.report, REPAIR_PROMPT)
            self.analysed.emit(parse_json(text), model)
        except Exception as exc:
            self.analysed.emit({"ursache": friendly_error(exc)}, "")


class TranscribeWorker(QThread):
    transcribed = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, models: list[str], wav: bytes, parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.models = models
        self.wav = wav

    def run(self) -> None:
        from google.genai import types
        try:
            part = types.Part.from_bytes(data=self.wav, mime_type="audio/wav")
            text, _model = generate(self.api_key, self.models, [part], TRANSCRIBE_PROMPT)
            self.transcribed.emit(text.strip())
        except Exception as exc:
            self.failed.emit(friendly_error(exc))
