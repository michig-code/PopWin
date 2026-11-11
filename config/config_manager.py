"""Persistente Einstellungen: Laden, Validieren, atomar Speichern, API-Key."""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.paths import DATA_DIR

log = logging.getLogger(__name__)

SETTINGS_PATH = DATA_DIR / "settings.json"
KEYRING_SERVICE = "PopWin"
KEYRING_USER = "gemini_api_key"

THEMES = ("liquid_glass", "pop_basic", "clean", "minimalist", "tiles_graphite", "tiles_obsidian", "tiles_frost",
          "glass_clear", "glass_smoke", "neon_night")
CUSTOM_THEME_PREFIX = "custom_"  # eigene Themes aus dem Theme-Editor (core/theme_maker.py)
# Ausgemusterte Stile -> der ähnlichste, der geblieben ist
REMOVED_THEMES = {"tiles_glass": "tiles_obsidian", "tiles_nova": "tiles_obsidian", "tiles_neon": "tiles_graphite",
                  "tiles_amber": "tiles_graphite", "tiles_ocean": "tiles_frost", "glass_rose": "glass_clear",
                  "glass_emerald": "glass_clear", "glass_prism": "glass_clear", "glass_liquid": "liquid_glass"}


def migrate_themes(data: dict) -> None:
    """Wer einen ausgemusterten Stil eingestellt hatte, bekommt den ähnlichsten; überall, wo ein Stil stehen kann."""
    def swap(holder: dict, key: str) -> None:
        if isinstance(holder, dict) and holder.get(key) in REMOVED_THEMES:
            holder[key] = REMOVED_THEMES[holder[key]]
    swap(data, "theme")
    for section in ("context_menu", "launcher"):
        swap(data.get(section), "theme")
    swap(data.get("monitor_shared"), "theme")
    for profile in (data.get("monitors") or {}).values():
        swap(profile, "theme")
_TERMINAL = "shell:AppsFolder\\Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"

DEFAULTS: dict[str, Any] = {
    "first_run": True,
    "settings_v2": True,  # Einstellungen mit Übersicht und Kategorien; aus = klassische Liste
    "settings_v2_since_4": True,  # Merker: Wer von 3.x kommt, bekommt das neue Menü einmal eingeschaltet
    "language": "auto",
    "hide_missing_hardware": True,
    "network": {"hourly_test": False},
    "wallpaper": {"enabled": False, "file": "", "screens": "all", "source": "auto"},
    "privacy": {"online_check": True},
    "keep_icons_off_widgets": False,
    "popwin_file_dialog": False,
    "desktop_groups": {"style": "off", "layout": {"order": [], "groups": {}}},
    "animations": {"enabled": True, "duration": 220},
    # saver: keine Animationen, seltener nachsehen; balanced: wie bisher; beautiful: alle Effekte
    # game_mode: Leisten ruhen hinter Vollbild-Spielen
    "performance": {"level": "balanced", "game_mode": True},
    "windows_trim": {},  # Windows entlasten: Kennung aus core.process_trim -> an/aus
    "theme": "pop_basic",
    "mode": "dark",
    "icon_tiles": "theme",  # Kacheln um die Taskbar-Icons: theme = wie im UI-Stil, on, off
    "tile_shape": "theme",  # Form der Kacheln: theme = wie im UI-Stil, round = komplett rund
    "monitor_mode": "sync",
    "monitor_shared": {},
    "monitors": {},
    "bar_screens": {
        "topbar": {"mode": "all", "screens": []},
        "taskbar": {"mode": "all", "screens": []},
    },
    "extensions": {"enabled": {}, "discord": {"monitors": [], "layout": "windows", "size": 1080, "fps": 15}},
    # Beta: Die PopWin-Apps ersetzen ihre Windows-Gegenstücke erst, wenn man sie einzeln einschaltet.
    "replacements": {"explorer": False, "editor": False, "terminal": False, "studio": False},
    "notifications": {"side": "right", "width": 380, "group": True, "show_count": True, "hidden": []},
    "taskbar": {
        "windows_pins_imported": False,
        "window_filter": "smart",
        "pin_scope": "all",
        "sync_icons": False,
        "screen_pins": {},
        "order": {},
        "preview": True,
        "drag_switch": True,  # beim Ziehen über einem Task hinhalten wechselt zur App
        "magnify": False,  # Beta: Icons wachsen wie am Mac, wenn die Maus darüber fährt
        "pinned": [
            {"name": "Explorer", "target": "explorer.exe"},
            {"name": "Terminal", "target": _TERMINAL, "exe": "windowsterminal.exe"},
        ],
    },
    "context_menu": {
        "enabled": True,
        "size": "normal",
        "font_size": 13,
        "icon_size": 16,
        "opacity": 0.98,
        "theme": "",
    },
    "launcher": {
        "hotkey": "alt+space",
        "override_win_s": False,
        "ai_fallback": True,
        "gemini_model": "gemini-3.5-flash-lite",  # alte Einstellung, wird nicht mehr gelesen
        "search_models": "gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-3.1-flash-lite",
        "repair_models": "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.6-flash,gemini-3.5-flash-lite",
        "ai_actions": True,
        "task_side": "right",
        "voice_seconds": 12,
        "live_actions": True,
        "situation_actions": True,
        "width": 640,
        "position": "upper",
        "screen": "cursor",
        "max_results": 8,
        "input_font": 18,
        "result_font": 13,
        "opacity": 1.0,
        "theme": "",
        "show_icons": True,
        "show_commands": True,
        "placeholder": "Suchen, Befehl ausführen oder ? für KI",
    },
    "start_menu": {
        "win_key": "home",
        "win_key_screen": "cursor",  # cursor, primary
        "layout": "grid",  # Aussehen: grid, list, tiles, strip, search (ui/start_menu.py VARIANTS)
        "columns": 4,
        "icon_size": 36,
        "width": 420,
        "pinned": [
            {"name": "Explorer", "target": "explorer.exe"},
            {"name": "Terminal", "target": _TERMINAL},
            {"name": "Editor", "target": "shell:AppsFolder\\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
            {"name": "Rechner", "target": "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
            {"name": "Paint", "target": "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App"},
            {"name": "Task-Manager", "target": "taskmgr.exe"},
            {"name": "Einstellungen", "target": "ms-settings:"},
            {"name": "Systemsteuerung", "target": "control.exe"},
        ],
    },
    "widgets": [],
    "widgets_visible": True,
    "nova": {
        "enabled": True,  # Nein im Setup: Knöpfe und Tastenkürzel weg, Nova wartet in den Einstellungen
        "hotkey": "ctrl+alt+n",
        "economy": "sparsam",
        "from_search": True,
        "side": "right",
        "screen": "cursor",  # cursor, primary oder ein bestimmter Bildschirm
        "reserve_space": True,
        "width": 420,
        "look": "popwin",
        "keep_open": True,
        "speak": False,
        "speak_engine": "live",  # live: mit Stimme läuft Nova über Gemini Live; tts: normales Modell und Vorlese-Stimme
        "voice": "Zephyr",
        "voice_style": "",  # Sprechweise in Worten, etwa „ruhig und etwas langsamer“
        "live_model": "gemini-3.8-live",  # [Antigravity: Aktualisiert von gemini-3.1-flash-live-preview auf gemini-3.8-live]
        "listen_silence": 1.0,  # Sekunden Pause, nach denen Nova merkt, dass man fertig ist
        "listen_followup": True,  # fragt Nova zurück, hört sie gleich wieder zu
        "listen_live": True,  # beim Diktat live zeigen, was verstanden wurde
        "listen_auto_stop": True,
        "artifacts_notify": True,
        "artifacts_tab": True,
        "connections": {},  # Name -> Felder und Hilfeseite; die Werte liegen nur im Windows-Anmeldespeicher
        "allow_memory": True,
        "allow_screen": True,
        "allow_input": False,
        "models": "gemini-3.5-flash,gemini-3.6-flash,gemini-3-flash-preview,gemini-3.5-flash-lite",
    },
    "shell": {"hide_native_taskbar": True, "saved_taskbar_state": None},
    "updates": {"auto_check": True, "skipped": "", "seen": ""},  # seen: Version, deren „Was ist neu“ schon lief
    "app_usage": {},  # [Antigravity: App-Nutzungshäufigkeit für Such-Ranking]
}

MONITOR_DEFAULTS: dict[str, Any] = {
    "theme": "",
    "topbar_position": "top",
    "taskbar_position": "bottom",
    "center": "none",  # title oder none; die Uhr hat ihren eigenen Platz (clock_place)
    "clock_place": "top_right",
    "dock_tools": "off",  # Beta: off, hover, always
    "dock_stack_order": "clock_below",
    "dev_claude_button": True,  # nur im Dev-Build: Claude-Knopf ganz links statt Claude als Task  # clock_below oder tools_below: wer von Uhr und Symbolleiste näher an der Taskbar sitzt
    "dock_tools_items": ["start", "search", "nova", "trash", "desktop"],  # top_right, top_center, dock_left, dock_center, dock_right, none
    "show_user": True,
    "show_workspaces": True,
    "show_quick": True,
    "show_nova": False,  # der Nova-Knopf sitzt in der Taskbar; hier nur auf Wunsch
    "show_notifications": True,
    "show_windows": True,
    "user_click": "account",  # account: Benutzer-Bereich, windows: gleich die offenen Fenster
    "topbar_auto_hide": False,
    "show_tray": True,
    "show_cloud": True,
    "show_mic": True,
    "show_network": True,
    "show_bluetooth": True,
    "show_battery": True,
    "show_volume": True,
    "show_language": True,  # Sprachanzeige wie „DEU“, nur wenn mehr als ein Tastatur-Layout da ist
    "show_project": True,  # Knopf „Bildschirm projizieren“ (Win+P)
    "show_snip": True,  # Knopf fürs Ausschneide-Tool
    "top_order": ["tray", "cloud", "mic", "lang", "network", "bluetooth", "battery", "volume", "project", "snip",
                  "quick", "nova", "bell"],
    "topbar_height": 0,  # 0 = Höhe des Themes
    "clock_style": "both",  # both, time, date
    "clock_lines": "two",  # two, one
    "clock_seconds": True,
    "auto_hide": False,  # beim ersten Start soll alles zu sehen sein und bleiben
    "edge_level": 3,
    "reserve_space": True,
    "show_media": True,
    "show_recycle": True,
    "show_nova_button": True,
    "show_start": True,
    "show_search": True,
    "show_desktop": False,
    "start_style": "home",  # home, windows, logo
    "search_style": "icon",  # icon, pill, field, wide
    "media_style": "full",  # full, compact, controls, cover
    "media_width": 250,
    "task_indicator": "bar",  # bar, dot, none
    "task_labels": False,
    "task_counts": False,
    "task_badges": True,  # roter Punkt mit der Zahl offener Benachrichtigungen am App-Icon
    "icon_size": 26,
    "icon_spacing": 3,
    "dock_height": 0,  # 0 = Höhe des Themes
    "dock_style": "full",
    "dock_floating": False,  # schwebende runde Pille mit Abstand zum Rand
    # Reihenfolge der festen Knöpfe der Taskbar; "apps" steht für die App-Icons. Was davor steht, sitzt links neben
    # den Apps, der Rest rechts. Lässt sich direkt auf der Taskbar per Drag-and-drop umstellen.
    "dock_order": ["start", "search", "apps", "media", "nova", "trash", "desktop"],
    "dock_gap": 10,  # Abstand der schwebenden Pille zum Bildschirmrand in Pixeln
    "dock_align": "center",
}
# Diese Felder verändern, welche Leisten es gibt; die übrigen übernehmen die Leisten direkt.
STRUCTURAL_FIELDS = ("theme", "topbar_position", "taskbar_position")


def migrate_clock(profile: dict) -> None:
    """Früher konnten die Uhr rechts und die Uhr in der Mitte gleichzeitig laufen. Jetzt gibt es einen Platz;
    wer beide hatte, behält die rechte."""
    if "clock_place" in profile:
        return
    if profile.pop("show_clock", True):
        profile["clock_place"] = "top_right"
    elif profile.get("center", "clock") == "clock":
        profile["clock_place"] = "top_center"
    else:
        profile["clock_place"] = "none"
    if profile.get("center", "clock") == "clock":
        profile["center"] = "none"
    profile.pop("center_seconds", None)


def _merge(defaults: dict, loaded: dict) -> dict:
    """Mischt geladene Werte in die Defaults; falsche Typen fallen auf den Default zurück."""
    merged = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if key not in defaults:
            merged[key] = value
        elif isinstance(defaults[key], dict):
            if isinstance(value, dict):
                merged[key] = _merge(defaults[key], value)
        elif defaults[key] is None or isinstance(value, type(defaults[key])):
            merged[key] = value
        elif isinstance(defaults[key], float) and isinstance(value, int):
            merged[key] = float(value)
    return merged


class ConfigManager(QObject):
    changed = Signal(str)

    def __init__(self, path: Path = SETTINGS_PATH):
        super().__init__()
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("settings.json ist kein Objekt")
        except FileNotFoundError:
            loaded = {}
        except (ValueError, OSError) as exc:
            # Kaputte Datei nicht überschreiben, sondern zur Fehlersuche aufheben.
            log.warning("settings.json unlesbar (%s) - nutze Standardwerte", exc)
            try:
                self.path.replace(self.path.with_suffix(".broken.json"))
            except OSError:
                pass
            loaded = {}
        data = _merge(DEFAULTS, loaded)
        migrate_themes(data)
        if loaded and "settings_v2_since_4" not in loaded:  # ab 4.0 Standard, auch wenn früher „aus“ gespeichert war
            data["settings_v2"] = True
        if data["theme"] not in THEMES and not str(data["theme"]).startswith(CUSTOM_THEME_PREFIX):
            data["theme"] = DEFAULTS["theme"]
        if data["mode"] not in ("dark", "light"):
            data["mode"] = "dark"
        if data["monitor_mode"] not in ("sync", "separate"):
            data["monitor_mode"] = "sync"
        for profile in (data["monitor_shared"], *data["monitors"].values()):
            migrate_clock(profile)
        return data

    def save(self) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    @staticmethod
    def default_of(dotted: str) -> Any:
        """Der Auslieferungswert einer Einstellung, für die Zurücksetzen-Knöpfe."""
        node: Any = DEFAULTS
        for part in dotted.split("."):
            node = node[part]
        return node

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, notify: bool = True) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        self.save()
        if notify:
            self.changed.emit(dotted)

    def set_screen_pins(self, key: str, pins: list[dict]) -> None:
        """Eigener Weg, weil Monitornamen wie \\\\.\\DISPLAY1 Punkte enthalten."""
        self.data["taskbar"]["screen_pins"][key] = pins
        self.set("taskbar.screen_pins", self.data["taskbar"]["screen_pins"])

    # --- Monitore -----------------------------------------------------------
    def bar_on(self, kind: str, key: str, primary: bool) -> bool:
        """Soll die Leiste ("topbar"/"taskbar") auf diesem Monitor erscheinen?"""
        choice = self.data["bar_screens"][kind]
        if choice["mode"] == "primary":
            return primary
        return choice["mode"] != "custom" or key in choice["screens"]

    def set_bar_screens(self, kind: str, mode: str, screens: list[str]) -> None:
        self.data["bar_screens"][kind] = {"mode": mode, "screens": screens}
        self.save()
        self.changed.emit("monitors")

    @property
    def synced(self) -> bool:
        return self.data["monitor_mode"] == "sync"

    def shared_profile(self) -> dict:
        return {**MONITOR_DEFAULTS, **self.data["monitor_shared"]}

    def monitor(self, key: str | None = None) -> dict:
        """Das geltende Profil: im Sync-Modus für alle gleich, sonst das des Monitors."""
        if self.synced or key is None:
            return self.shared_profile()
        return {**self.shared_profile(), **self.data["monitors"].get(key, {})}

    def set_monitor(self, key: str | None, field: str, value: Any) -> None:
        if self.synced or key is None:
            self.data["monitor_shared"][field] = value
        else:
            # Ein neuer Monitor startet als Kopie des gemeinsamen Profils.
            self.data["monitors"].setdefault(key, self.shared_profile())[field] = value
        self.save()
        self.changed.emit("monitors" if field in STRUCTURAL_FIELDS else "profile")

    def set_monitor_mode(self, mode: str) -> None:
        """Einzelprofile bleiben beim Wechsel erhalten und gelten beim Zurückwechseln wieder."""
        if mode in ("sync", "separate") and mode != self.data["monitor_mode"]:
            self.data["monitor_mode"] = mode
            self.save()
            self.changed.emit("monitors")

    def copy_profile_to_all(self, key: str, keys: list[str]) -> None:
        profile = self.monitor(key)
        self.data["monitor_shared"] = dict(profile)
        for other in keys:
            self.data["monitors"][other] = dict(profile)
        self.save()
        self.changed.emit("monitors")

    def models(self, kind: str) -> list[str]:
        """Modell-Kette "search" oder "repair", frei editierbar als Komma-Liste."""
        return [m.strip() for m in self.get(f"launcher.{kind}_models", "").split(",") if m.strip()]

    # --- Gemini-Key: im Windows-Anmeldeinformationsspeicher, nie in settings.json ---
    def api_key(self, user: str = KEYRING_USER) -> str:
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, user) or ""
        except Exception as exc:
            log.warning("Keyring nicht lesbar: %s", exc)
            return ""

    def set_api_key(self, key: str, user: str = KEYRING_USER) -> bool:
        try:
            import keyring
            if key:
                keyring.set_password(KEYRING_SERVICE, user, key)
            elif keyring.get_password(KEYRING_SERVICE, user):
                keyring.delete_password(KEYRING_SERVICE, user)
            return True
        except Exception as exc:
            log.error("Keyring nicht beschreibbar: %s", exc)
            return False

    # --- [Antigravity Start: App-Nutzungshäufigkeit für Such-Ranking (Rest: Claude Code)] ---
    def record_app_launch(self, name: str, target: str = "") -> None:
        usage = self.data.setdefault("app_usage", {})
        dirty = False
        for raw in (name, target):
            key = raw.lower().strip()
            if key:
                entry = usage.get(key)
                if isinstance(entry, dict):
                    entry["count"] = entry.get("count", 0) + 1
                elif isinstance(entry, (int, float)):
                    usage[key] = {"count": int(entry) + 1}
                else:
                    usage[key] = {"count": 1}
                dirty = True
        if dirty:
            self.save()

    def get_app_usage(self) -> dict[str, int]:
        raw = self.data.get("app_usage", {})
        result: dict[str, int] = {}
        for k, v in raw.items():
            if isinstance(v, dict):
                result[k] = int(v.get("count", 0))
            elif isinstance(v, (int, float)):
                result[k] = int(v)
        return result
    # --- [Antigravity Ende: App-Nutzungshäufigkeit für Such-Ranking] ---


# --- Einstellungen teilen: Export, Import und Presets -------------------------------
EXPORT_FORMAT = "popwin-settings"
EXPORT_VERSION = 1
PRESETS_DIR = DATA_DIR / "presets"
# Was nur auf diesen PC passt oder privat ist, wandert nie in eine geteilte Datei
PRIVATE_KEYS = ("first_run", "monitors", "app_usage", "shell", "updates", "widgets", "monitor_mode")
PRIVATE_PATHS = ("taskbar.screen_pins", "taskbar.order", "taskbar.windows_pins_imported", "nova.connections",
                 "wallpaper.file", "notifications.hidden", "desktop_groups.layout", "privacy")


def shareable(data: dict) -> dict:
    """Kopie der Einstellungen ohne Privates und ohne das, was nur auf diesem PC Sinn ergibt."""
    shared = copy.deepcopy(data)
    for key in PRIVATE_KEYS:
        shared.pop(key, None)
    for dotted in PRIVATE_PATHS:
        *parents, last = dotted.split(".")
        node = shared
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(last, None)
    return shared


def apply_shared(current: dict, shared: dict) -> dict:
    """Mischt geteilte Einstellungen über die eigenen; Privates und Falsches bleiben wie sie sind."""
    incoming = shareable(shared)
    migrate_themes(incoming)  # eine Datei von einem Freund mit älterem PopWin
    merged = _merge(current, incoming)
    for key in PRIVATE_KEYS:  # _merge übernimmt unbekannte Schlüssel; Privates nie
        if key in current:
            merged[key] = copy.deepcopy(current[key])
    for dotted in PRIVATE_PATHS:
        *parents, last = dotted.split(".")
        source, target = current, merged
        for part in parents:
            source = source.get(part, {}) if isinstance(source, dict) else {}
            target = target.get(part) if isinstance(target, dict) else None
        if isinstance(target, dict) and isinstance(source, dict) and last in source:
            target[last] = copy.deepcopy(source[last])
    return merged


def export_document(data: dict, name: str = "") -> dict:
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "name": name, "settings": shareable(data)}


def read_document(text: str) -> tuple[str, dict]:
    """Name und Einstellungen aus einer geteilten Datei; ValueError, wenn es keine PopWin-Datei ist."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Die Datei ist kein gültiges JSON.") from exc
    if not isinstance(document, dict) or document.get("format") != EXPORT_FORMAT:
        raise ValueError("Das ist keine PopWin-Einstellungsdatei.")
    settings = document.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("In der Datei stehen keine Einstellungen.")
    return str(document.get("name") or ""), settings


def preset_file(name: str, folder: Path = PRESETS_DIR) -> Path:
    """Dateiname für ein Preset; Zeichen, die Windows in Dateinamen verbietet, fallen weg."""
    clean = "".join(char for char in name.strip() if char not in '<>:"/\\|?*' and ord(char) >= 32).strip(" .")
    if not clean:
        raise ValueError("Bitte gib dem Preset einen Namen.")
    return folder / f"{clean[:60]}.json"


def list_presets(folder: Path = PRESETS_DIR) -> list[str]:
    return sorted((file.stem for file in folder.glob("*.json")), key=str.lower) if folder.is_dir() else []


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def export_settings(config: "ConfigManager", path: Path, name: str = "") -> None:
    _write_json(Path(path), export_document(config.data, name))


def save_preset(config: "ConfigManager", name: str, folder: Path = PRESETS_DIR) -> Path:
    path = preset_file(name, folder)
    _write_json(path, export_document(config.data, name.strip()))
    return path


def import_settings(config: "ConfigManager", path: Path) -> str:
    """Übernimmt eine geteilte Datei oder ein Preset; gibt dessen Namen zurück."""
    name, settings = read_document(Path(path).read_text(encoding="utf-8"))
    config.data = apply_shared(config.data, settings)
    config.save()
    config.changed.emit("monitors")
    config.changed.emit("profile")
    return name


def delete_preset(name: str, folder: Path = PRESETS_DIR) -> None:
    preset_file(name, folder).unlink(missing_ok=True)
