"""Schnelle Antworten für die Suche, alles lokal und ohne Internet: Rechner, Umrechnen von Einheiten, Timer,
Dateisuche nach Namen und ein Verlauf der Zwischenablage. Reine Logik ohne Fenster und ohne Windows-Aufrufe."""
from __future__ import annotations

import ast
import math
import operator
import os
import re
import threading
import time
import urllib.parse

# --- Rechner ------------------------------------------------------------------
MAX_EXPRESSION = 80
MAX_POWER = 1000  # größere Hochzahlen rechnen ewig und füllen den Speicher
_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {"sqrt": math.sqrt, "wurzel": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
              "ln": math.log, "abs": abs, "round": round, "runden": round}
_NAMES = {"pi": math.pi, "e": math.e}
_ALLOWED = re.compile(r"^[\d\s.,+\-*/^()%a-zäöü]+$")
_PERCENT_OF = re.compile(r"^\s*([\d.,]+)\s*%\s*(?:von|of|de)\s*([\d.,]+)\s*$")


def _evaluate(node):
    """Nur Zahlen, Grundrechenarten und die Funktionen aus der Liste. Alles andere ist kein Rechenausdruck."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_POWER or abs(left) > 1e6:
                raise ValueError("zu groß")
            return left ** right
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        return _FUNCTIONS[node.func.id](*[_evaluate(argument) for argument in node.args])
    if isinstance(node, ast.Name):
        return _NAMES[node.id]
    raise ValueError("nicht erlaubt")


def format_number(value: float, comma: bool = False) -> str:
    """Höchstens zehn gültige Stellen, ganze Zahlen ohne Nachkommastellen, auf Wunsch mit Komma."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    text = str(value) if isinstance(value, int) else f"{value:.10g}"
    return text.replace(".", ",") if comma else text


def calculate(text: str) -> str | None:
    """Das Ergebnis eines Rechenausdrucks wie „12*3+4“, „2^10“, „sqrt(2)“ oder „15% von 80“. Sonst None,
    auch für eine bloße Zahl: Wer „2024“ tippt, sucht etwas und rechnet nicht."""
    source = text.strip().lower()
    if not source or len(source) > MAX_EXPRESSION or not _ALLOWED.match(source) or not any(c.isdigit() for c in source):
        return None
    comma = "," in source and "(" not in source  # in Funktionen trennt das Komma die Werte
    share = _PERCENT_OF.match(source)
    if share:
        source = f"({share[1].replace(',', '.')})/100*({share[2].replace(',', '.')})"
    else:
        if comma:
            source = source.replace(",", ".")
        source = re.sub(r"(\d)\s*%", r"\1/100", source).replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
        if isinstance(tree.body, (ast.Constant, ast.Name)):
            return None
        if isinstance(tree.body, ast.UnaryOp) and isinstance(tree.body.operand, ast.Constant):
            return None
        value = _evaluate(tree.body)
    except (SyntaxError, ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError, RecursionError, MemoryError):
        return None
    if isinstance(value, complex) or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return format_number(value, comma)


# --- Einheiten ----------------------------------------------------------------
# Einheit -> (Größe, Faktor zur Grundeinheit)
_UNITS: dict[str, tuple[str, float]] = {}
for _kind, _table in {
    "length": {"mm": 0.001, "cm": 0.01, "dm": 0.1, "m": 1, "km": 1000, "in": 0.0254, "inch": 0.0254, "zoll": 0.0254,
               "ft": 0.3048, "fuß": 0.3048, "yd": 0.9144, "mi": 1609.344, "meile": 1609.344, "meilen": 1609.344, "sm": 1852},
    "weight": {"mg": 1e-6, "g": 0.001, "kg": 1, "t": 1000, "oz": 0.028349523125, "lb": 0.45359237, "lbs": 0.45359237,
               "pfund": 0.5},
    "data": {"bit": 0.125, "b": 1, "byte": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4},
    "time": {"ms": 0.001, "s": 1, "sek": 1, "min": 60, "h": 3600, "std": 3600, "d": 86400, "tag": 86400, "tage": 86400,
             "woche": 604800, "wochen": 604800},
    "volume": {"ml": 0.001, "cl": 0.01, "l": 1, "gal": 3.785411784, "cup": 0.2365882365},
    "speed": {"m/s": 1, "km/h": 1 / 3.6, "kmh": 1 / 3.6, "mph": 0.44704, "kn": 1852 / 3600},
    "area": {"m2": 1, "m²": 1, "km2": 1e6, "km²": 1e6, "ha": 10000, "ft2": 0.09290304, "ft²": 0.09290304},
}.items():
    for _name, _factor in _table.items():
        _UNITS[_name] = (_kind, _factor)
_TEMPERATURES = {"c": "c", "°c": "c", "celsius": "c", "f": "f", "°f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
_CONVERSION = re.compile(r"^\s*(-?[\d.,]+)\s*([a-zäöüß°²/0-9]+?)\s+(?:in|to|nach|zu|als|en|=|->)\s+([a-zäöüß°²/0-9]+)\s*$")


def _temperature(value: float, source: str, target: str) -> float:
    kelvin = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[source]
    return {"c": kelvin - 273.15, "f": (kelvin - 273.15) * 9 / 5 + 32, "k": kelvin}[target]


def convert(text: str) -> str | None:
    """„5 km in mi“ -> „3,106855961 mi“. None, wenn es keine Umrechnung ist oder die Einheiten nicht zusammenpassen."""
    match = _CONVERSION.match(text.lower())
    if not match:
        return None
    number, source, target = match.groups()
    comma = "," in number
    try:
        value = float(number.replace(",", "."))
    except ValueError:
        return None
    if source in _TEMPERATURES and target in _TEMPERATURES:
        result = _temperature(value, _TEMPERATURES[source], _TEMPERATURES[target])
    elif source in _UNITS and target in _UNITS and _UNITS[source][0] == _UNITS[target][0]:
        result = value * _UNITS[source][1] / _UNITS[target][1]
    else:
        return None
    return f"{format_number(float(f'{result:.10g}'), comma)} {match[3]}"


# --- Timer --------------------------------------------------------------------
_TIMER = re.compile(r"^\s*(?:timer|fokus|focus)\s+(\d{1,3})\s*(?:m|min|minuten|minutes)?\s*$", re.I)


def timer_minutes(text: str) -> int | None:
    """„timer 10“ -> 10. Zwischen einer Minute und acht Stunden."""
    match = _TIMER.match(text)
    minutes = int(match[1]) if match else 0
    return minutes if 1 <= minutes <= 480 else None


# --- Im Web suchen --------------------------------------------------------------
SEARCH_ENGINES = {"duckduckgo": "https://duckduckgo.com/?q={}", "google": "https://www.google.com/search?q={}",
                  "bing": "https://www.bing.com/search?q={}", "startpage": "https://www.startpage.com/do/search?q={}"}
WEB_PREFIX = "w "


def web_search_url(query: str, engine: str) -> str:
    return SEARCH_ENGINES.get(engine, SEARCH_ENGINES["duckduckgo"]).format(urllib.parse.quote_plus(query.strip()))


def split_web_prefix(text: str) -> str | None:
    """„w wetter berlin“ -> „wetter berlin“; None ohne das Kürzel."""
    return text[len(WEB_PREFIX):].strip() if text.lower().startswith(WEB_PREFIX) and text[len(WEB_PREFIX):].strip() else None


# --- Dateisuche nach Namen ------------------------------------------------------
MAX_FILES = 30000
MAX_DEPTH = 4
INDEX_AGE = 300  # nach so vielen Sekunden wird beim nächsten Öffnen der Suche neu gelesen
SKIPPED_FOLDERS = {"node_modules", "__pycache__", "appdata", "$recycle.bin", "venv", "site-packages"}


class FileIndex:
    """Dateinamen aus ein paar Ordnern, im Hintergrund gelesen. Die Suche selbst geht nur durch die Liste im Speicher
    und fasst die Platte nicht an; so bleibt das Tippen flüssig."""

    def __init__(self, roots: list[str], limit: int = MAX_FILES, depth: int = MAX_DEPTH):
        self.roots = roots
        self.limit = limit
        self.depth = depth
        self.names: list[str] = []
        self.paths: list[str] = []
        self.built_at = 0.0
        self._thread: threading.Thread | None = None

    def build(self) -> None:
        names: list[str] = []
        paths: list[str] = []
        for root in self.roots:
            base = root.rstrip("\\/").count(os.sep)
            for folder, folders, files in os.walk(root, onerror=lambda _error: None):
                level = folder.rstrip("\\/").count(os.sep) - base
                folders[:] = [] if level >= self.depth - 1 else [
                    name for name in folders if not name.startswith(".") and name.lower() not in SKIPPED_FOLDERS]
                for name in files:
                    if name.startswith(".") or name.lower() == "desktop.ini":
                        continue
                    names.append(name.lower())
                    paths.append(os.path.join(folder, name))
                    if len(names) >= self.limit:
                        break
                if len(names) >= self.limit:
                    break
        self.names, self.paths = names, paths  # in einem Schritt getauscht: Die Suche sieht nie eine halbe Liste
        self.built_at = time.monotonic()

    def refresh(self, max_age: float = INDEX_AGE) -> bool:
        """Liest im Hintergrund neu, wenn die Liste fehlt oder alt ist. True, wenn ein Lauf gestartet wurde."""
        if self._thread is not None and self._thread.is_alive():
            return False
        if self.built_at and time.monotonic() - self.built_at < max_age:
            return False
        self._thread = threading.Thread(target=self.build, name="PopWin-Dateiindex", daemon=True)
        self._thread.start()
        return True

    def search(self, query: str, limit: int = 5) -> list[str]:
        """Pfade, in deren Dateinamen alle Wörter vorkommen. Namen, die mit der Suche beginnen, stehen vorn, kurze vor langen."""
        words = query.lower().split()
        if not words or len(query.strip()) < 3:
            return []
        names, paths = self.names, self.paths
        found = [(not name.startswith(words[0]), len(name), index) for index, name in enumerate(names)
                 if all(word in name for word in words)]
        return [paths[index] for _starts, _length, index in sorted(found)[:limit]]


# --- Verlauf der Zwischenablage -------------------------------------------------
CLIP_PREFIX = "v "
MAX_CLIPS = 25
MAX_CLIP_LENGTH = 10000


class ClipHistory:
    """Die letzten kopierten Texte, nur im Arbeitsspeicher. Nichts davon kommt auf die Platte oder ins Internet."""

    def __init__(self, limit: int = MAX_CLIPS):
        self.limit = limit
        self.items: list[str] = []

    def add(self, text: str) -> None:
        if not text or not text.strip() or len(text) > MAX_CLIP_LENGTH:
            return
        if text in self.items:
            self.items.remove(text)
        self.items.insert(0, text)
        del self.items[self.limit:]

    def clear(self) -> None:
        self.items.clear()

    def search(self, query: str) -> list[str]:
        needle = query.strip().lower()
        return [item for item in self.items if needle in item.lower()]


def clip_label(text: str, width: int = 70) -> str:
    """Eine Zeile für die Liste: Zeilenumbrüche werden Leerzeichen, Langes endet mit Auslassungspunkten."""
    line = " ".join(text.split())
    return line if len(line) <= width else line[:width - 1] + "…"


def split_clip_prefix(text: str) -> str | None:
    """„v “ oder „v rechnung“ -> Suchtext im Verlauf (auch leer); None ohne das Kürzel."""
    return text[len(CLIP_PREFIX):] if text.lower().startswith(CLIP_PREFIX) else None
