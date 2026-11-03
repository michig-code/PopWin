"""Verbindungen zu Diensten wie OVH, die Nova mit dem Host einrichtet. Jeder kann sich so eigene Anbindungen bauen lassen.

Welche Felder eine Verbindung hat, steht in den Einstellungen. Die Werte selbst stehen nur im Windows-Anmeldespeicher: nie in
settings.json, nie in einem Artefakt, nie im Chat und nie im Log. Eingetippt werden sie in ein PopWin-Feld, nicht bei Nova.
"""
from __future__ import annotations

import re

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")
MAX_FIELDS = 6


def _check(text: str, what: str) -> str:
    text = str(text).strip().lower()
    if not NAME.match(text):
        raise ValueError(f"{what} {text!r} geht nicht: nur Kleinbuchstaben, Ziffern, - und _.")
    return text


def keyring_user(name: str, field: str) -> str:
    return f"conn:{name}:{field}"


class Connections:
    def __init__(self, config):
        self.config = config

    def all(self) -> dict[str, dict]:
        return dict(self.config.get("nova.connections") or {})

    def names(self) -> list[str]:
        return sorted(self.all())

    def get(self, name: str) -> dict:
        found = self.all().get(str(name).strip().lower())
        if found is None:
            raise KeyError(f"Keine Verbindung {name}.")
        return found

    def define(self, name: str, fields, help_url: str = "", note: str = "") -> str:
        """Legt fest, welche Felder es gibt. Werte setzt nur der Host."""
        name = _check(name, "Der Name")
        fields = [_check(field, "Das Feld") for field in fields if str(field).strip()]
        if not 1 <= len(fields) <= MAX_FIELDS:
            raise ValueError(f"Eine Verbindung hat 1 bis {MAX_FIELDS} Felder.")
        if help_url and not re.match(r"^https://", help_url.strip()):
            raise ValueError("Die Hilfeseite muss mit https:// beginnen.")
        known = self.all()
        known[name] = {"fields": fields, "help": help_url.strip(), "note": note.strip()[:300]}
        self.config.set("nova.connections", known)
        return name

    def is_set(self, name: str, field: str) -> bool:
        return bool(self.config.api_key(keyring_user(name, field)))

    def ready(self, name: str) -> bool:
        try:
            return all(self.is_set(name, field) for field in self.get(name)["fields"])
        except KeyError:
            return False

    def save(self, name: str, values: dict[str, str]) -> bool:
        """Leere Felder bleiben, wie sie sind: So lässt sich ein einzelner Key austauschen."""
        fields = self.get(name)["fields"]
        return all(self.config.set_api_key(str(value).strip(), keyring_user(name, field))
                   for field, value in values.items() if field in fields and str(value).strip())

    def secrets(self, name: str) -> dict[str, str]:
        """Nur für den Werkzeug-Prozess und zum Säubern von Ausgaben, nie für Nova."""
        try:
            fields = self.get(name)["fields"]
        except KeyError:
            return {}
        return {field: self.config.api_key(keyring_user(name, field)) for field in fields}

    def forget(self, name: str, remove: bool = False) -> None:
        for field in self.get(name)["fields"]:
            self.config.set_api_key("", keyring_user(name, field))
        if remove:
            known = self.all()
            known.pop(name, None)
            self.config.set("nova.connections", known)

    def describe(self) -> str:
        """Was Nova über die Verbindungen wissen darf: Namen, Felder und ob sie gesetzt sind, nie die Werte."""
        lines = []
        for name, entry in sorted(self.all().items()):
            fields = ", ".join(f"{field} {'gesetzt' if self.is_set(name, field) else 'fehlt'}" for field in entry["fields"])
            lines.append(f"{name}: {fields}" + (f" (Keys gibt es auf {entry['help']})" if entry.get("help") else ""))
        return "\n".join(lines) or "Es gibt noch keine Verbindungen."
