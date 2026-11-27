"""Fenster an Bildschirmhälften und -viertel legen. Reine Rechnung, ohne Windows: Das Platzieren macht win_api."""
from __future__ import annotations

MAXIMIZE = "max"
# Kennung -> (Beschriftung, Anteil links, oben, rechts, unten am Arbeitsbereich)
TILES: dict[str, tuple[str, float, float, float, float]] = {
    "left": ("Linke Hälfte", 0.0, 0.0, 0.5, 1.0),
    "right": ("Rechte Hälfte", 0.5, 0.0, 1.0, 1.0),
    "top": ("Obere Hälfte", 0.0, 0.0, 1.0, 0.5),
    "bottom": ("Untere Hälfte", 0.0, 0.5, 1.0, 1.0),
    "top_left": ("Oben links", 0.0, 0.0, 0.5, 0.5),
    "top_right": ("Oben rechts", 0.5, 0.0, 1.0, 0.5),
    "bottom_left": ("Unten links", 0.0, 0.5, 0.5, 1.0),
    "bottom_right": ("Unten rechts", 0.5, 0.5, 1.0, 1.0),
}


def tile_rect(area: tuple[int, int, int, int], place: str) -> tuple[int, int, int, int]:
    """x, y, Breite, Höhe für `place` im Arbeitsbereich (links, oben, rechts, unten). Nachbarn stoßen ohne Lücke aneinander."""
    left, top, right, bottom = area
    _label, x1, y1, x2, y2 = TILES[place]
    width, height = right - left, bottom - top
    x, y = left + round(width * x1), top + round(height * y1)
    return x, y, left + round(width * x2) - x, top + round(height * y2) - y


def next_window(hwnds: list[int], current: int, step: int) -> int:
    """Das nächste (step 1) oder vorige (step -1) Fenster einer App; ist keines davon aktiv, das erste. 0 ohne Fenster."""
    if not hwnds:
        return 0
    if current not in hwnds:
        return hwnds[0]
    return hwnds[(hwnds.index(current) + step) % len(hwnds)]
