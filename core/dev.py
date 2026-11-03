"""Erkennt den Entwickler-Build: nur aus dem Quellordner gestartet und mit dem Ordner devtools daneben.

devtools wird nie eingecheckt und nie mitgebaut. Wer PopWin installiert oder das Repository lädt, hat ihn nicht,
und alles, was hier freigeschaltet wird, bleibt für ihn unsichtbar.
"""
from __future__ import annotations

from core.paths import FROZEN, ROOT

DEV_DIR = ROOT / "devtools"
DEV_WORDS = ("dev", "dev-menü", "dev menü", "devmenü", "dev-menu", "entwickler", "release", "veröffentlichen", "pin")


def is_dev() -> bool:
    return not FROZEN and DEV_DIR.is_dir()


def dev_menu_wanted(query: str) -> bool:
    """Ob eine Suche das Dev-Menü meint. Außerhalb des Dev-Builds nie, egal was jemand tippt."""
    text = " ".join(query.lower().split())
    return is_dev() and len(text) >= 2 and any(word.startswith(text) or text.startswith(word) for word in DEV_WORDS)


def open_dev_menu(ctx) -> None:
    """Über importlib: So sieht PyInstaller den Ordner devtools nicht und packt ihn nie ein. Die PIN fragt das Menü selbst."""
    import importlib
    importlib.import_module("devtools.menu").open_menu(ctx)
