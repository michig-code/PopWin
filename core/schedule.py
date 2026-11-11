"""Was sich nach der Uhr richtet: Hell und Dunkel nach Tageszeit, Wechsel des Hintergrunds. Reine Rechnung."""
from __future__ import annotations

import random


def parse_clock(text: str, default: int) -> int:
    """„7:30“ -> Minuten seit Mitternacht. Unlesbares fällt auf `default` zurück."""
    try:
        hours, _colon, minutes = str(text).strip().partition(":")
        value = int(hours) * 60 + int(minutes or 0)
    except ValueError:
        return default
    return value if 0 <= value < 24 * 60 else default


def mode_for(minute: int, light_from: int, dark_from: int) -> str:
    """„light“ zwischen `light_from` und `dark_from`, sonst „dark“. Liegt hell nach dunkel, läuft der helle Teil über Mitternacht."""
    if light_from == dark_from:
        return "dark"
    if light_from < dark_from:
        return "light" if light_from <= minute < dark_from else "dark"
    return "dark" if dark_from <= minute < light_from else "light"


def rotation_pick(choices: list[str], start: str, elapsed_minutes: float, every: int, shuffle: bool, seed: int = 0) -> str:
    """Welcher Hintergrund gerade dran ist: beginnt beim gewählten und geht alle `every` Minuten einen weiter.
    Gemischt bleibt die Reihenfolge für diese Sitzung fest (`seed`), jeder kommt einmal dran, bevor sich etwas wiederholt."""
    if not choices or every <= 0:
        return start
    order = list(choices)
    if shuffle:
        random.Random(seed).shuffle(order)
    first = order.index(start) if start in order else 0
    return order[(first + int(elapsed_minutes // every)) % len(order)]
