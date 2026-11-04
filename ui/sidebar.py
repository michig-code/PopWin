"""[Antigravity] Sidebar-Unterstützung (links/rechts) für die Taskbar.

Zentral gesteuert über `SIDEBAR_ENABLED = True` und exklusiv im Dev-Build verfügbar.
Sollte der Sidebar-Support später nicht gefallen, genügt `SIDEBAR_ENABLED = False`
oder das Entfernen der mit `# --- [Antigravity Start: Sidebar Support (links/rechts)] ---`
markierten Blöcke.
"""
# --- [Antigravity Start: Sidebar Support (links/rechts)] ---
from __future__ import annotations

from core.dev import is_dev

# Schalter: Kann jederzeit auf False gesetzt werden, um Sidebars global abzuschalten.
SIDEBAR_ENABLED = True


def sidebar_active() -> bool:
    """Gibt zurück, ob Sidebar-Positionen (links/rechts) aktiv sein dürfen."""
    return SIDEBAR_ENABLED and is_dev()


def effective_edge(edge: str) -> str:
    """Gibt die wirksame Kante zurück; fällt bei inaktiver Sidebar auf 'bottom' zurück."""
    if edge in ("left", "right"):
        return edge if sidebar_active() else "bottom"
    return "top" if edge == "top" else "bottom"


def taskbar_positions() -> dict[str, str]:
    """Auswählbare Taskbar-Positionen."""
    base = {"bottom": "Unten", "top": "Oben"}
    if sidebar_active():
        base["left"] = "Links (Sidebar · Dev)"
        base["right"] = "Rechts (Sidebar · Dev)"
    return base
# --- [Antigravity Ende: Sidebar Support] ---
