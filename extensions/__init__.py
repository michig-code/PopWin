"""Erweiterungen: Zusatzfunktionen, die man einzeln einschaltet. Eigene .py-Dateien kommen in den Erweiterungsordner.

Eine eigene Erweiterung ist eine Datei mit einer Klasse EXTENSION, die von extensions.Extension erbt.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
from pathlib import Path

from core.paths import DATA_DIR

log = logging.getLogger(__name__)
USER_FOLDER = DATA_DIR / "extensions"


class Extension:
    key = ""
    title = ""
    description = ""

    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def running(self) -> bool:
        return False

    def start(self) -> None:
        """Wird beim Einschalten aufgerufen und beim Start von PopWin, wenn die Erweiterung aktiv ist."""

    def stop(self) -> None:
        """Wird beim Ausschalten und beim Beenden von PopWin aufgerufen."""

    def settings(self, form, window) -> None:
        """Füllt das QFormLayout dieser Erweiterung auf der Einstellungsseite."""


class ExtensionManager:
    def __init__(self, ctx):
        from extensions.discord import DiscordStream
        self.ctx = ctx
        self.items: dict[str, Extension] = {}
        self.errors: dict[str, str] = {}
        self._register(DiscordStream)
        self.load_user_folder()

    def _register(self, cls) -> None:
        extension = cls(self.ctx)
        if not extension.key:
            raise ValueError("Die Erweiterung braucht einen key")
        self.items[extension.key] = extension

    def load_user_folder(self) -> None:
        if USER_FOLDER.is_dir():
            for path in sorted(USER_FOLDER.glob("*.py")):
                self._load(path)

    def _load(self, path: Path) -> None:
        """Ein Fehler in einer eigenen Datei darf PopWin nicht aufhalten; er steht dann in den Einstellungen."""
        try:
            spec = importlib.util.spec_from_file_location(f"popwin_extension_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._register(module.EXTENSION)
            self.errors.pop(path.name, None)
        except Exception as exc:
            log.warning("Erweiterung %s nicht geladen: %s", path.name, exc)
            self.errors[path.name] = str(exc)

    def add_file(self, source: str) -> None:
        USER_FOLDER.mkdir(parents=True, exist_ok=True)
        target = USER_FOLDER / os.path.basename(source)
        shutil.copy2(source, target)
        self._load(target)

    def enabled(self, key: str) -> bool:
        return bool(self.ctx.config.get("extensions.enabled").get(key))

    def set_enabled(self, key: str, on: bool) -> None:
        enabled = dict(self.ctx.config.get("extensions.enabled"))
        enabled[key] = on
        self.ctx.config.set("extensions.enabled", enabled)
        extension = self.items[key]
        try:
            (extension.start if on and getattr(extension, "AUTO_START", True) else extension.stop)()
        except Exception as exc:
            log.warning("Erweiterung %s: %s", key, exc)

    def start(self) -> None:
        for key, extension in self.items.items():
            if self.enabled(key) and getattr(extension, "AUTO_START", True):
                try:
                    extension.start()
                except Exception as exc:
                    log.warning("Erweiterung %s nicht gestartet: %s", key, exc)

    def shutdown(self) -> None:
        for extension in self.items.values():
            try:
                extension.stop()
            except Exception as exc:
                log.warning("Erweiterung %s nicht beendet: %s", extension.key, exc)
