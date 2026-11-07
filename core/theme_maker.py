"""Eigene Themes: aus wenigen Werten (Akzent, Hintergrund, Transparenz, Ecken, Leuchten) wird ein komplettes
QSS-Theme. Auch das eingebaute Theme „Neon-Nacht“ entsteht so.

Gespeichert wird jedes eigene Theme als kleine JSON-Datei in DATA_DIR/themes. Sein Name in den Einstellungen
ist "custom_<kennung>". Teilen geht über .popwin-theme-Dateien (dasselbe JSON)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.paths import DATA_DIR

CUSTOM_PREFIX = "custom_"
CUSTOM_DIR = DATA_DIR / "themes"
FILE_FORMAT = "popwin-theme"
FILE_SUFFIX = ".popwin-theme"
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_SPEC = {"name": "Mein Theme", "accent": "#C04BE0", "accent2": "#FF4FA3", "bg": "#0B0816", "bg2": "#1A1033",
                "alpha": 82, "corner": 18, "glow": 60}
# Eingebaut, im Stil einer dunklen Neon-Webseite: tiefes Violett, Verlauf von Lila nach Pink, leichtes Leuchten
NEON_SPEC = {"name": "Neon-Nacht", "accent": "#8B5CF6", "accent2": "#FF2FB3", "bg": "#0B0816", "bg2": "#120C24",
             "alpha": 84, "corner": 18, "glow": 70}
LIMITS = {"alpha": (35, 100), "corner": (0, 26), "glow": (0, 100)}


def _hex(value, fallback: str) -> str:
    return value.upper() if isinstance(value, str) and HEX_RE.match(value) else fallback


def clean_spec(spec: dict) -> dict:
    """Unbrauchbare Werte (aus fremden Dateien) werden durch die Standardwerte ersetzt."""
    spec = spec if isinstance(spec, dict) else {}
    out = {"name": str(spec.get("name") or DEFAULT_SPEC["name"]).strip()[:40] or DEFAULT_SPEC["name"]}
    for key in ("accent", "accent2", "bg", "bg2"):
        out[key] = _hex(spec.get(key), DEFAULT_SPEC[key])
    for key, (low, high) in LIMITS.items():
        try:
            out[key] = max(low, min(high, int(spec.get(key, DEFAULT_SPEC[key]))))
        except (TypeError, ValueError):
            out[key] = DEFAULT_SPEC[key]
    return out


def rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def rgba(color: str, alpha: float) -> str:
    red, green, blue = rgb(color)
    return f"rgba({red}, {green}, {blue}, {round(alpha, 3)})"


def is_light(color: str) -> bool:
    red, green, blue = rgb(color)
    return (0.299 * red + 0.587 * green + 0.114 * blue) > 150


def mix(first: str, second: str, amount: float) -> str:
    """Farbe zwischen `first` (0) und `second` (1)."""
    a, b = rgb(first), rgb(second)
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b))


def values(spec: dict) -> dict[str, str]:
    spec = clean_spec(spec)
    light = is_light(spec["bg"])
    fg = "#14171D" if light else "#FFFFFF"
    ink = "0, 0, 0" if light else "255, 255, 255"  # für dezente Flächen und Linien
    alpha = spec["alpha"] / 100
    glow = spec["glow"] / 100
    corner = spec["corner"]
    return {
        "fg": fg,
        "fg_muted": f"rgba({ink}, 0.68)",
        "fg_off": f"rgba({ink}, 0.42)",
        "accent": spec["accent"], "accent2": spec["accent2"],
        "accent_soft": rgba(spec["accent"], 0.22), "accent_mid": rgba(spec["accent"], 0.4),
        "accent_glow": rgba(spec["accent"], 0.25 + 0.6 * glow), "accent2_glow": rgba(spec["accent2"], 0.2 + 0.6 * glow),
        "on_accent": "#FFFFFF" if not is_light(mix(spec["accent"], spec["accent2"], 0.5)) else "#14171D",
        "grad": f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {spec['accent']}, stop:1 {spec['accent2']})",
        "grad_soft": (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(spec['accent'], 0.32)}, "
                      f"stop:1 {rgba(spec['accent2'], 0.32)})"),
        "surface": (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(spec['bg2'], alpha)}, "
                    f"stop:1 {rgba(spec['bg'], alpha)})"),
        "solid": mix(spec["bg"], spec["bg2"], 0.5),
        "solid2": mix(spec["bg2"], fg, 0.06),
        "field": f"rgba({ink}, 0.06)", "field_hover": f"rgba({ink}, 0.12)", "field_press": f"rgba({ink}, 0.2)",
        "line": f"rgba({ink}, 0.1)", "line_strong": f"rgba({ink}, 0.2)",
        "edge": rgba(spec["accent"], 0.15 + 0.45 * glow),  # Rand mit leichtem Leuchten
        "edge_top": rgba(spec["accent2"], 0.2 + 0.5 * glow),
        "r": f"{corner}px", "r_btn": f"{max(4, round(corner * 0.55))}px", "r_pill": f"{max(6, round(corner * 0.75))}px",
        "r_task": f"{max(4, min(18, round(corner * 0.8)))}px",
        "blur": "acrylic" if alpha < 0.99 else "none",
        "tint": spec["bg"] + f"{round(alpha * 160):02X}",
        "ind": spec["accent2"], "corner": str(corner), "rounded": "1" if corner else "0",
    }


TEMPLATE = """/* Theme „@title“, erzeugt von PopWin (core/theme_maker.py). */
/* popwin: blur=@blur tint=@tint bar=36 dock=60 margin=6 rounded=@rounded quick_on=@accent ind=@ind ind_active=@accent2 ind_light=@accent ind_active_light=@accent2 tiles=1 corner=@corner */
* { font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; color: @fg; outline: none; }

#Bar { background: @surface; border: 1px solid @edge; border-top-color: @edge_top; border-radius: @r; }
#Panel, #Desklet { background: @surface; border: 1px solid @edge; border-top-color: @edge_top; border-radius: @r; }

QPushButton, QToolButton { background: transparent; border: none; border-radius: @r_btn; padding: 3px 7px; }
QPushButton:hover, QToolButton:hover { background: @field_hover; }
QPushButton:pressed, QToolButton:pressed { background: @field_press; }
QPushButton:checked { background: @grad_soft; }
QPushButton:disabled { color: @fg_off; }

#IconButton { font-size: 16px; padding: 3px 6px; }
#IconButton[active="true"] { color: @accent2; }
#IconButton[alert="true"] { color: #FF9D7A; }
#IconButton[off="true"] { color: @fg_off; }
#UserLabel { font-weight: 600; padding: 0 8px; }
#WorkspaceChip { padding: 1px 8px; font-weight: 600; }
#WorkspaceChip[current="true"] { background: @grad; color: @on_accent; }
#CenterLabel { font-weight: 600; }
#ClockTime { font-size: 13px; font-weight: 600; }
#ClockDate { font-size: 10px; color: @fg_muted; }

#TaskButton { border-radius: @r_task; padding: 3px; }
#TaskButton[focused="true"] { background: @accent_soft; }
#StartButton { font-size: 20px; padding: 3px 9px; }
#Trash { font-size: 20px; padding: 3px 9px; }
#Trash[dropping="true"] { background: @accent_mid; }
#MediaBox { background: @field; border: 1px solid @line; border-radius: @r_btn; }
#MediaTitle { font-weight: 600; font-size: 12px; }
#MediaArtist { font-size: 11px; color: @fg_muted; }

#PanelTitle { font-size: 16px; font-weight: 700; }
#Muted { color: @fg_muted; font-size: 12px; }
#AppTile { padding: 8px 4px; border-radius: @r_btn; font-size: 11px; }
#AppTile:hover { background: @field_hover; }
#Danger { color: #FFB39B; font-weight: 600; }
#Danger:hover { background: rgba(255, 110, 80, 0.45); color: #FFFFFF; }

#SearchInput { background: @field; border: 1px solid @edge; border-radius: @r_pill; padding: 10px 14px; font-size: 18px; selection-background-color: @accent_mid; }
#SearchInput:focus { border-color: @accent2; }
#Results { background: transparent; border: none; }
#Results::item { padding: 7px 10px; border-radius: @r_btn; }
#Results::item:selected { background: @grad_soft; color: @fg; }
#Answer { background: @field; border-radius: @r_btn; padding: 10px; }

QMenu { background: @solid; border: 1px solid @edge; border-radius: @r_btn; padding: 6px; }
QMenu::item { background: transparent; border-radius: @r_btn; padding: 5px 18px 5px 10px; }
QMenu::item:selected { background: @grad_soft; }
QMenu::item:disabled { color: @fg_off; }
QMenu::separator { height: 1px; background: @line; margin: 5px 8px; }
#MenuChip { padding: 2px 8px; font-size: 12px; }
#MenuRowLabel { color: @fg_muted; font-size: 12px; }

QSlider::groove:horizontal { height: 4px; background: @line_strong; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @grad; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: @fg; border: 2px solid @accent2; }
QProgressBar { background: @field_hover; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: @grad; border-radius: 3px; }

#SettingsRoot, QDialog { background: @solid; }
QTabWidget::pane, QScrollArea, #SettingsPage { background: @solid; border: none; }
QTabWidget, QTabBar { background: @solid; }
QTabBar::tab { background: transparent; padding: 8px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { background: @field; }
QTabBar::tab:selected { border-bottom: 2px solid @accent2; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QListWidget { background: @field; border: 1px solid @line_strong; border-radius: @r_btn; padding: 5px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: @accent; }
QComboBox QAbstractItemView { background: @solid2; selection-background-color: @accent_mid; }
QAbstractItemView QLineEdit { background: @solid2; }
QPushButton#Primary { background: @grad; color: @on_accent; font-weight: 700; padding: 7px 16px; border: none; border-radius: @r_pill; }
QPushButton#Primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 @accent2, stop:1 @accent); }
QPushButton#Secondary { background: @field; border: 1px solid @edge; padding: 7px 16px; border-radius: @r_pill; }
QPushButton#Secondary:hover { background: @field_hover; border-color: @accent2; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid @line_strong; background: @field; }
QCheckBox::indicator:checked { background: @grad; border-color: @accent2; }
QGroupBox { border: 1px solid @line; border-radius: @r_btn; margin-top: 12px; padding: 10px; background: @field; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: @accent2; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: @accent_mid; border-radius: 5px; margin: 2px; min-height: 48px; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; }

#QuickTile { background: @field; border: 1px solid @line; border-radius: @r_btn; padding: 8px 4px; font-size: 11px; }
#QuickTile:hover { border-color: @edge_top; }
#QuickTile:checked { background: @grad; color: @on_accent; border-color: @accent2; }
#QuickTile:disabled { color: @fg_off; }
#SettingsNav::item:selected, #SearchHits::item:selected { background: @grad_soft; color: @fg; }
#NovaCard { background: @field; border-radius: @r_btn; }

/* Kacheln um die Icons der Taskbar */
#Bar #TaskButton, #Bar #StartButton, #Bar #Trash { border: 1px solid @line; padding: 5px; margin: 1px 2px; background: @field; }
#Bar #TaskButton { border-radius: @r_task; } #Bar #StartButton, #Bar #Trash { border-radius: @r_task; padding-left: 8px; padding-right: 8px; }
#Bar #TaskButton:hover, #Bar #StartButton:hover, #Bar #Trash:hover { background: @field_hover; border-color: @edge_top; }
#Bar #TaskButton:pressed, #Bar #StartButton:pressed, #Bar #Trash:pressed { background: @field_press; }
#Bar #TaskButton[focused="true"] { background: @grad_soft; border: 1px solid @accent2; }
#Bar #Trash[dropping="true"] { border: 2px solid @accent2; }

/* Hell-Modus */
[popMode="light"] * { color: #14171D; }
#Bar[popMode="light"], #Panel[popMode="light"], #Desklet[popMode="light"] { background: rgba(255, 255, 255, 0.6); border-color: @accent_mid; }
QMenu[popMode="light"] { background: #FFFFFF; border-color: @accent_mid; }
QMenu[popMode="light"], QMenu[popMode="light"]::item { color: #14171D; }
QMenu[popMode="light"]::item:selected { background: @accent_soft; color: #14171D; }
[popMode="light"] #Results::item:selected, [popMode="light"] #SettingsNav::item:selected, [popMode="light"] #SearchHits::item:selected { background: @accent_soft; color: #14171D; }
#SettingsRoot[popMode="light"], [popMode="light"] QTabWidget::pane, [popMode="light"] QScrollArea, [popMode="light"] #SettingsPage, [popMode="light"] QTabWidget, [popMode="light"] QTabBar { background: #F3F1F8; }
[popMode="light"] QLineEdit, [popMode="light"] QPlainTextEdit, [popMode="light"] QTextEdit, [popMode="light"] QSpinBox, [popMode="light"] QComboBox, [popMode="light"] QListWidget, [popMode="light"] #SearchInput { background: #FFFFFF; border-color: rgba(0, 0, 0, 0.18); }
[popMode="light"] QComboBox QAbstractItemView, [popMode="light"] QAbstractItemView QLineEdit { background: #FFFFFF; }
[popMode="light"] #Muted, [popMode="light"] #ClockDate, [popMode="light"] #MediaArtist, [popMode="light"] #MenuRowLabel { color: rgba(0, 0, 0, 0.62); }
[popMode="light"] QPushButton:hover, [popMode="light"] QToolButton:hover { background: rgba(0, 0, 0, 0.08); }
[popMode="light"] QGroupBox, [popMode="light"] #QuickTile, [popMode="light"] #MediaBox, [popMode="light"] #Bar #TaskButton, [popMode="light"] #Bar #StartButton, [popMode="light"] #Bar #Trash { background: rgba(0, 0, 0, 0.04); border-color: rgba(0, 0, 0, 0.12); }
[popMode="light"] QPushButton#Primary, [popMode="light"] #QuickTile:checked, [popMode="light"] #WorkspaceChip[current="true"] { color: #FFFFFF; }
[popMode="light"] QPushButton#Secondary { background: rgba(0, 0, 0, 0.06); }
[popMode="light"] #IconButton[off="true"] { color: rgba(0, 0, 0, 0.42); }
"""


def render(spec: dict, title: str = "") -> str:
    data = values(spec)
    data["title"] = title or clean_spec(spec)["name"]
    return re.sub(r"@(\w+)", lambda match: data.get(match[1], match[0]), TEMPLATE)


# --- Eigene Themes speichern ----------------------------------------------
def slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
    return text.strip("_")[:30] or "theme"


def is_custom(name: str) -> bool:
    return isinstance(name, str) and name.startswith(CUSTOM_PREFIX)


def custom_path(name: str, folder: Path | None = None) -> Path:
    return (folder or CUSTOM_DIR) / f"{name[len(CUSTOM_PREFIX):]}.json"


def list_custom(folder: Path | None = None) -> dict[str, str]:
    """Kennung -> Anzeigename aller eigenen Themes."""
    folder = folder or CUSTOM_DIR
    found = {}
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        spec = load_spec(CUSTOM_PREFIX + path.stem, folder)
        if spec:
            found[CUSTOM_PREFIX + path.stem] = spec["name"]
    return found


def load_spec(name: str, folder: Path | None = None) -> dict | None:
    try:
        data = json.loads(custom_path(name, folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return clean_spec(data.get("spec", data))


def save_spec(spec: dict, name: str = "", folder: Path | None = None) -> str:
    """Speichert (oder überschreibt `name`) und gibt die Kennung zurück."""
    spec = clean_spec(spec)
    folder = folder or CUSTOM_DIR
    folder.mkdir(parents=True, exist_ok=True)
    if not is_custom(name):
        base = CUSTOM_PREFIX + slug(spec["name"])
        name, number = base, 2
        while custom_path(name, folder).exists():
            name, number = f"{base}_{number}", number + 1
    custom_path(name, folder).write_text(json.dumps({"format": FILE_FORMAT, "spec": spec}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return name


def delete_spec(name: str, folder: Path | None = None) -> None:
    if is_custom(name):
        custom_path(name, folder).unlink(missing_ok=True)


def export_spec(spec: dict, target: Path) -> None:
    target.write_text(json.dumps({"format": FILE_FORMAT, "spec": clean_spec(spec)}, ensure_ascii=False, indent=1),
                      encoding="utf-8")


def read_shared(source: Path) -> dict:
    """Liest eine geteilte .popwin-theme-Datei; ValueError, wenn sie kein Theme ist."""
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Die Datei ist nicht lesbar.") from exc
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("Das ist keine PopWin-Theme-Datei.")
    return clean_spec(data.get("spec"))
