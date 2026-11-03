"""Novas Artefakte: dauerhafte Aufgaben, Notizen, Überwachungen und Werkzeuge, die Nova selbst anlegt und pflegt.

Jedes Artefakt ist eine JSON-Datei im PopWin-Datenordner, nie im Projekt: Was dort steht, wird weder eingecheckt noch gebaut
noch hochgeladen. Zugangsdaten stehen nie in einem Artefakt, die liegen im Windows-Anmeldespeicher.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from core.paths import DATA_DIR

log = logging.getLogger(__name__)

ARTIFACT_DIR = DATA_DIR / "nova_artifacts"
KINDS = {"aufgabe": "Aufgabe", "notiz": "Notiz", "ueberwachung": "Überwachung", "werkzeug": "Werkzeug"}
STATES = {"offen": "Offen", "aktiv": "Aktiv", "pausiert": "Pausiert", "erledigt": "Erledigt"}
CHECKS = {"ovh_domains": "OVH-Domains"}  # eingebaute Prüfungen, die ohne Code von Nova auskommen
MAX_HISTORY = 200
MAX_CODE = 20000
PROMPT_ITEMS = 8
PROMPT_CHARS = 1600
MIN_HOURS, MAX_HOURS = 1, 24 * 7
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")


def stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).replace(microsecond=0).isoformat()


def code_hash(code: str) -> str:
    """Eine Freigabe gilt genau für diesen Code: Ändert Nova ihn, fragt PopWin wieder."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def slug(title: str) -> str:
    text = title.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "artefakt"


def kind_of(text: str) -> str:
    """Nova schreibt mal „Überwachung“, mal „monitor“: Alles landet bei einer der vier Arten."""
    lowered = text.strip().lower()
    aliases = {"task": "aufgabe", "todo": "aufgabe", "note": "notiz", "monitor": "ueberwachung", "überwachung": "ueberwachung",
               "watch": "ueberwachung", "tool": "werkzeug", "skript": "werkzeug", "script": "werkzeug"}
    lowered = aliases.get(lowered, lowered)
    if lowered not in KINDS:
        raise ValueError(f"Unbekannte Art: {text}. Erlaubt: {', '.join(KINDS)}")
    return lowered


def progress(artifact: dict) -> tuple[int, int]:
    steps = artifact.get("steps", [])
    return sum(1 for step in steps if step.get("done")), len(steps)


def is_open(artifact: dict) -> bool:
    return artifact.get("state") != "erledigt"


def new_artifact(title: str, kind: str, content: str = "", steps: list[str] | None = None, moment: datetime | None = None) -> dict:
    title = title.strip()
    if not title:
        raise ValueError("Ein Artefakt braucht einen Titel.")
    kind = kind_of(kind)
    now = stamp(moment)
    artifact = {"id": f"{slug(title)}-{uuid.uuid4().hex[:6]}", "title": title[:120], "kind": kind,
                "state": "aktiv" if kind == "ueberwachung" else "offen", "notes": "", "steps": [], "history": [],
                "due": "", "schedule_hours": 0, "last_check": "", "next_check": "", "check": "", "tool": "",
                "code": "", "read_only": False, "approved": "", "connection": "", "snapshot": None, "warned": [], "unseen": 0,
                "created": now, "updated": now}
    if kind == "werkzeug":
        artifact["code"] = content[:MAX_CODE]
    else:
        artifact["notes"] = content.strip()
    artifact["steps"] = [{"text": text.strip(), "done": False} for text in steps or [] if text.strip()]
    add_history(artifact, f"Angelegt als {KINDS[kind]}", moment)
    return artifact


def add_history(artifact: dict, text: str, moment: datetime | None = None) -> None:
    artifact["history"].append({"ts": stamp(moment), "text": text.strip()[:600]})
    del artifact["history"][:-MAX_HISTORY]


def find_step(artifact: dict, step: str) -> dict:
    """Nummer ab 1 oder ein Teil des Textes."""
    steps = artifact["steps"]
    key = str(step).strip()
    if key.isdigit() and 1 <= int(key) <= len(steps):
        return steps[int(key) - 1]
    match = next((item for item in steps if key.lower() and key.lower() in item["text"].lower()), None)
    if match is None:
        raise ValueError(f"Kein Schritt passt zu {step}.")
    return match


def schedule(artifact: dict, hours: int, moment: datetime | None = None) -> None:
    hours = int(hours)
    if hours and not MIN_HOURS <= hours <= MAX_HOURS:
        raise ValueError(f"Der Abstand muss zwischen {MIN_HOURS} und {MAX_HOURS} Stunden liegen.")
    artifact["schedule_hours"] = hours
    artifact["next_check"] = stamp((moment or datetime.now()) + timedelta(hours=hours)) if hours else ""


def is_due(artifact: dict, moment: datetime) -> bool:
    if artifact.get("state") not in ("aktiv", "offen") or not artifact.get("schedule_hours"):
        return False
    upcoming = artifact.get("next_check")
    return not upcoming or datetime.fromisoformat(upcoming) <= moment


def record_check(artifact: dict, findings: list[str], moment: datetime | None = None, failed: str = "") -> None:
    """Hält eine Prüfung im Verlauf fest; nur Neues zählt als ungelesen."""
    moment = moment or datetime.now()
    artifact["last_check"] = stamp(moment)
    if artifact.get("schedule_hours"):
        artifact["next_check"] = stamp(moment + timedelta(hours=artifact["schedule_hours"]))
    if failed:
        text = f"Prüfung fehlgeschlagen: {failed}"
        if not artifact["history"] or artifact["history"][-1]["text"] != text:  # derselbe Fehler alle paar Stunden ist keine Neuigkeit
            add_history(artifact, text, moment)
    elif findings:
        for finding in findings:
            add_history(artifact, finding, moment)
        artifact["unseen"] = artifact.get("unseen", 0) + len(findings)
    else:
        add_history(artifact, "Geprüft, nichts Neues", moment)


def summary_line(artifact: dict) -> str:
    done, total = progress(artifact)
    parts = [KINDS.get(artifact["kind"], artifact["kind"]), STATES.get(artifact["state"], artifact["state"])]
    if total:
        parts.append(f"{done} von {total} erledigt")
        upcoming = next((step["text"] for step in artifact["steps"] if not step["done"]), "")
        if upcoming:
            parts.append(f"als Nächstes: {upcoming[:80]}")
    if artifact.get("due"):
        parts.append(f"fällig {artifact['due']}")
    if artifact.get("schedule_hours"):
        parts.append(f"alle {artifact['schedule_hours']} Std. geprüft")
    if artifact.get("unseen"):
        parts.append(f"{artifact['unseen']} neue Meldungen")
    return f"- [{artifact['id']}] {artifact['title']} ({', '.join(parts)})"


def describe(artifact: dict) -> str:
    """Alles, was Nova über ein Artefakt lesen darf. Der Verlauf nur am Ende, sonst wächst der Prompt endlos."""
    lines = [summary_line(artifact)[2:]]
    if artifact.get("notes"):
        lines.append("Notizen:\n" + artifact["notes"][:4000])
    for number, step in enumerate(artifact.get("steps", []), 1):
        lines.append(f"{number}. [{'x' if step['done'] else ' '}] {step['text']}")
    if artifact.get("check"):
        lines.append(f"Prüfung: {CHECKS.get(artifact['check'], artifact['check'])}")
    if artifact.get("tool"):
        lines.append(f"Prüft mit dem Werkzeug {artifact['tool']}")
    if artifact.get("code"):
        lines.append(("Nur lesend. " if artifact.get("read_only") else "") + "Code:\n```python\n" + artifact["code"] + "\n```")
    if artifact.get("history"):
        lines.append("Verlauf (neueste zuletzt):\n" + "\n".join(f"{entry['ts']}: {entry['text']}" for entry in artifact["history"][-12:]))
    return "\n".join(lines)


class ArtifactStore:
    """Eine Datei pro Artefakt. Gelesen wird erst beim ersten Gebrauch, damit PopWin beim Start keine Daten anfasst."""

    def __init__(self, folder: Path = ARTIFACT_DIR):
        self.folder = folder
        self._items: dict[str, dict] | None = None

    @property
    def items(self) -> dict[str, dict]:
        if self._items is None:
            self._items = {}
            for path in sorted(self.folder.glob("*.json")) if self.folder.is_dir() else []:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    log.warning("Artefakt %s unlesbar: %s", path.name, exc)
                    continue
                if isinstance(data, dict) and _ID.match(str(data.get("id", ""))) and data.get("kind") in KINDS:
                    self._items[data["id"]] = data
        return self._items

    def all(self) -> list[dict]:
        """Offene zuerst, darin das zuletzt Geänderte oben."""
        return sorted(self.items.values(), key=lambda item: (is_open(item), item.get("updated", "")), reverse=True)

    def get(self, artifact_id: str) -> dict:
        artifact = self.items.get(str(artifact_id).strip())
        if artifact is None:
            needle = str(artifact_id).strip().lower()
            artifact = next((item for item in self.items.values() if needle and needle in item["title"].lower()), None)
        if artifact is None:
            raise KeyError(f"Kein Artefakt {artifact_id}.")
        return artifact

    def _path(self, artifact_id: str) -> Path:
        if not _ID.match(artifact_id):
            raise ValueError(f"Ungültige Kennung: {artifact_id}")
        return self.folder / f"{artifact_id}.json"

    def save(self, artifact: dict, moment: datetime | None = None) -> None:
        artifact["updated"] = stamp(moment)
        path = self._path(artifact["id"])
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        self.items[artifact["id"]] = artifact

    def add(self, artifact: dict) -> dict:
        self.save(artifact)
        return artifact

    def delete(self, artifact_id: str) -> str:
        artifact = self.get(artifact_id)
        self._path(artifact["id"]).unlink(missing_ok=True)
        del self.items[artifact["id"]]
        return artifact["title"]

    def as_prompt(self) -> str:
        """Die offenen Artefakte, gekürzt wie das Gedächtnis: Nova soll wissen, woran sie gerade dran ist."""
        lines, used = [], 0
        for artifact in [item for item in self.all() if is_open(item)][:PROMPT_ITEMS]:
            line = summary_line(artifact)
            if used + len(line) > PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line)
        rest = sum(1 for item in self.items.values() if is_open(item)) - len(lines)
        if rest > 0:
            lines.append(f"- … und {rest} weitere (artifact_list)")
        return "\n".join(lines)

    def due(self, moment: datetime) -> list[dict]:
        return [artifact for artifact in self.items.values() if is_due(artifact, moment)]
