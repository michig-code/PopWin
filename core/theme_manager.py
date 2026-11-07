"""Lädt die QSS-Dateien, erkennt Änderungen zur Laufzeit (Hot-Reload) und wendet sie an.

Jede .qss-Datei beginnt mit einer Kommentarzeile für Werte, die QSS selbst nicht
ausdrücken kann:  /* popwin: blur=acrylic tint=#20232A88 bar=34 dock=52 margin=6 */
Der Hell-Modus steckt ebenfalls rein in QSS, über den Selektor [popMode="light"].
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from core import theme_maker

log = logging.getLogger(__name__)

THEME_DIR = Path(__file__).resolve().parent.parent / "themes"
PRESETS = {
    "liquid_glass": "Liquid Glass",
    "pop_basic": "Pop!_OS Basic",
    "clean": "Clean",
    "minimalist": "Minimalistisch",
    # Kachel-Stile: Jedes Icon der Taskbar sitzt in einer eigenen dunklen Kachel. Ruhige Farben, die beim Zocken nicht ablenken.
    "tiles_graphite": "Graphit",
    "tiles_obsidian": "Obsidian",
    "tiles_frost": "Frostglas",
    # Glas-Familie im Stil von Liquid Glass, hell und gut lesbar für die Arbeit
    "glass_clear": "Klarglas",
    "glass_smoke": "Rauchglas",
    # Dunkles Violett mit Verlauf von Lila nach Pink; erzeugt von core/theme_maker.py (NEON_SPEC)
    "neon_night": "Neon-Nacht",
}
TILE_MODES = {"theme": "Wie im Theme", "on": "An", "off": "Aus"}
TILE_SHAPES = {"theme": "Wie im Theme", "round": "Komplett rund"}
# Qt rundet nur bis zur halben Höhe; mehr, und die Ecke bleibt eckig. Darum je Knopfart ein eigener Radius.
TILES_ROUND = """
#Bar { border-radius: 31px; }
#Bar #TaskButton, #Bar #TaskButton:hover, #Bar #TaskButton:pressed, #Bar #TaskButton[focused="true"] { border-radius: 18px; }
#Bar #StartButton, #Bar #Trash, #Bar #StartButton:hover, #Bar #Trash:hover, #Bar #StartButton:pressed, #Bar #Trash:pressed { border-radius: 15px; }
#Panel, #Desklet { border-radius: 18px; }
#MediaBox { border-radius: 16px; }
"""
TILES_OFF = """
#Bar #TaskButton, #Bar #StartButton, #Bar #Trash { background: transparent; border: none; margin: 0; }
#Bar #TaskButton:hover, #Bar #StartButton:hover, #Bar #Trash:hover { background: rgba(128, 128, 128, 0.22); }
#Bar #TaskButton[focused="true"] { background: rgba(128, 128, 128, 0.3); border: none; }
"""
NOVA_CLASSICS = {  # Novas Farben aus der Zeit als eigenes Programm
    "nacht": {"bg": "#05060d", "solid": "#0d0e1d", "field": "rgba(255, 255, 255, 0.045)", "line": "rgba(190, 200, 255, 0.12)",
              "fg": "#eceef7", "muted": "#8d93ab", "accent": "#8b6cff", "b1": "#5b3cff", "b2": "#00b7ff", "b3": "#ff3fa4"},
    "ozean": {"bg": "#030b12", "solid": "#091a26", "field": "rgba(255, 255, 255, 0.06)", "line": "rgba(160, 220, 255, 0.12)",
              "fg": "#e6f3fa", "muted": "#7fa3b8", "accent": "#2fb4ff", "b1": "#0077ff", "b2": "#00e0c6", "b3": "#7a5cff"},
    "neon": {"bg": "#030307", "solid": "#0a0a14", "field": "rgba(255, 255, 255, 0.05)", "line": "rgba(0, 255, 166, 0.16)",
             "fg": "#eafff6", "muted": "#6f9186", "accent": "#00ffa6", "b1": "#00ffa6", "b2": "#00a2ff", "b3": "#ff2ee6"},
    "amber": {"bg": "#0b0805", "solid": "#18120a", "field": "rgba(255, 255, 255, 0.06)", "line": "rgba(255, 200, 120, 0.13)",
              "fg": "#f8ecd8", "muted": "#a58f6d", "accent": "#ffa31a", "b1": "#ff8a00", "b2": "#ffd000", "b3": "#ff3d6e"},
    "sakura": {"bg": "#fff1f6", "solid": "#ffe3ec", "field": "rgba(224, 64, 126, 0.07)", "line": "rgba(224, 64, 126, 0.16)",
               "fg": "#3a2630", "muted": "#9b7684", "accent": "#e0407e", "b1": "#ff7aa8", "b2": "#ffb86b", "b3": "#b57bff"},
    "hell": {"bg": "#eef0f8", "solid": "#ffffff", "field": "rgba(60, 70, 120, 0.06)", "line": "rgba(60, 70, 120, 0.14)",
             "fg": "#1c2030", "muted": "#69708a", "accent": "#5b4bff", "b1": "#7a6bff", "b2": "#35c4ff", "b3": "#ff7ac8"},
}
META_DEFAULTS = {
    "blur": "none", "tint": "#00000000", "tint_light": "", "bar": "34", "dock": "52",
    "margin": "0", "rounded": "0", "compact": "0", "quick_on": "#FFFFFF", "tiles": "0", "corner": "0",
    "ind": "#FFFFFF", "ind_active": "#FFFFFF", "ind_light": "#202020", "ind_active_light": "#000000",
}
_META_RE = re.compile(r"/\*\s*popwin:(.*?)\*/", re.S)


def _soft(color: str, alpha: float) -> str:
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})"


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._cache: dict[str, tuple[str, dict]] = {}
        self._watcher = QFileSystemWatcher(self)
        self._reload_timer = QTimer(self, singleShot=True, interval=150)
        self._reload_timer.timeout.connect(self._reload)
        self._watcher.fileChanged.connect(lambda _p: self._reload_timer.start())
        self._watch()

    def _watch(self) -> None:
        # Editoren ersetzen Dateien beim Speichern - danach muss neu überwacht werden.
        files = [str(THEME_DIR / f"{name}.qss") for name in (*PRESETS, "_base", "_tiles")]
        missing = [f for f in files if f not in self._watcher.files()]
        if missing:
            self._watcher.addPaths(missing)

    def _reload(self) -> None:
        self._cache.clear()
        self._watch()
        self.changed.emit()

    @property
    def mode(self) -> str:
        return self.config.get("mode", "dark")

    @property
    def current(self) -> str:
        return self.config.get("theme")

    def exists(self, name: str) -> bool:
        return name in PRESETS or (theme_maker.is_custom(name) and theme_maker.custom_path(name).exists())

    def all_themes(self) -> dict[str, str]:
        """Eingebaute und eigene Themes, Kennung -> Anzeigename."""
        return {**PRESETS, **theme_maker.list_custom()}

    def forget(self, name: str) -> None:
        """Nach dem Speichern oder Löschen eines eigenen Themes neu laden."""
        self._cache.pop(name, None)
        self.changed.emit()

    def preview_sheet(self, spec: dict) -> str:
        """Stylesheet für die Live-Vorschau im Theme-Editor, ohne etwas zu speichern."""
        return self._base_qss() + theme_maker.render(spec)

    def _base_qss(self) -> str:
        try:
            return (THEME_DIR / "_base.qss").read_text(encoding="utf-8")
        except OSError:
            return ""

    def set_theme(self, name: str) -> None:
        if self.exists(name) and name != self.current:
            self.config.set("theme", name, notify=False)
            self.changed.emit()

    @property
    def tile_mode(self) -> str:
        mode = self.config.get("icon_tiles")
        return mode if mode in TILE_MODES else "theme"

    def set_tile_mode(self, mode: str) -> None:
        if mode in TILE_MODES and mode != self.tile_mode:
            self.config.set("icon_tiles", mode, notify=False)
            self.changed.emit()

    @property
    def tile_shape(self) -> str:
        shape = self.config.get("tile_shape")
        return shape if shape in TILE_SHAPES else "theme"

    def set_tile_shape(self, shape: str) -> None:
        if shape in TILE_SHAPES and shape != self.tile_shape:
            self.config.set("tile_shape", shape, notify=False)
            self.changed.emit()

    def tiles(self, name: str = "") -> bool:
        """Sitzen die Icons der Taskbar in Kacheln? Das Theme entscheidet, außer man stellt es fest ein."""
        mode = self.tile_mode
        return self.meta(name)["tiles"] == "1" if mode == "theme" else mode == "on"

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.config.set("mode", mode, notify=False)
            self.changed.emit()

    def _load(self, name: str) -> tuple[str, dict]:
        if not self.exists(name):
            name = self.current if self.exists(self.current) else "liquid_glass"
        if name not in self._cache:
            if theme_maker.is_custom(name):
                spec = theme_maker.load_spec(name) or theme_maker.DEFAULT_SPEC
                qss = self._base_qss() + theme_maker.render(spec)
            else:
                try:
                    qss = (THEME_DIR / "_base.qss").read_text(encoding="utf-8")
                    qss += (THEME_DIR / f"{name}.qss").read_text(encoding="utf-8")
                except OSError as exc:
                    log.error("Theme %s nicht lesbar: %s", name, exc)
                    qss = ""
            meta = dict(META_DEFAULTS)
            match = _META_RE.search(qss)
            if match:
                meta.update(p.split("=", 1) for p in match.group(1).split() if "=" in p)
            self._cache[name] = (qss, meta)
        return self._cache[name]

    def stylesheet(self, name: str = "") -> str:
        qss, meta = self._load(name or self.current)
        own = meta["tiles"] == "1"
        wanted = self.tiles(name)
        if wanted and not own:
            qss = qss + "\n" + self._tiles_qss()
        elif own and not wanted:
            qss = qss + TILES_OFF
        if wanted and self.tile_shape == "round":
            qss = qss + TILES_ROUND
        return qss

    def _tiles_qss(self) -> str:
        if "_tiles" not in self._cache:
            try:
                self._cache["_tiles"] = ((THEME_DIR / "_tiles.qss").read_text(encoding="utf-8"), {})
            except OSError as exc:
                log.error("Kacheln nicht lesbar: %s", exc)
                self._cache["_tiles"] = ("", {})
        return self._cache["_tiles"][0]

    def meta(self, name: str = "") -> dict:
        return self._load(name or self.current)[1]

    def nova_classic(self, name: str) -> str:
        colors = NOVA_CLASSICS[name]
        values = {**colors, "accent_soft": _soft(colors["accent"], 0.3), "accent_edge": _soft(colors["accent"], 0.7),
                  "b3_soft": _soft(colors["b3"], 0.25)}
        qss = (THEME_DIR / "nova_classic.qss").read_text(encoding="utf-8")
        return re.sub(r"@(\w+)", lambda match: values[match[1]], qss)

    def tint(self, name: str = "") -> str:
        meta = self.meta(name)
        return (self.mode == "light" and meta["tint_light"]) or meta["tint"]
