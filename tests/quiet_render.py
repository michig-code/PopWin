"""Rendert alle Oberflächen, ohne dass etwas auf dem Bildschirm erscheint oder den Fokus nimmt.

Prüft den QSS-Inhalt pro Theme und Modus; keine AppBars, keine Hooks, kein Eingriff in Windows.
Start: python tests/quiet_render.py <Ausgabeordner>
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

import main as popwin_main
from config.config_manager import ConfigManager
from core.theme_manager import PRESETS
from ui.base import set_prop
from ui.quick_panel import QuickPanel
from ui.taskbar import Dock
from ui.topbar import TopBar

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
errors: list[str] = []
sys.excepthook = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))


def save(widget, name: str, backdrop="#3B4A63") -> None:
    pixmap = widget.grab()
    image = QImage(pixmap.size(), QImage.Format.Format_RGB32)
    image.fill(QColor(backdrop))
    painter = QPainter(image)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    image.save(str(out / f"{name}.png"))


def main() -> int:
    app = popwin_main.create_application([])
    folder = tempfile.mkdtemp()
    config = ConfigManager(Path(folder) / "settings.json")
    config.data["first_run"] = False
    ctx = popwin_main.PopWinApp(config)
    ctx.status.state.update(volume=58, net_kind="ethernet", net_name="Ethernet", online=True, bluetooth=True,
                            cloud=["OneDrive"], mic=["opera.exe"])
    ctx.apps.run()  # synchron, ohne eigenen Thread
    screen = QGuiApplication.primaryScreen()
    media = {"title": "Beispieltitel mit einem recht langen Namen", "artist": "Interpret", "app": "x",
             "playing": True, "thumbnail": b""}
    ctx.last_media = media
    top, dock = TopBar(ctx, screen, "top"), Dock(ctx, screen, "bottom")
    for bar in (top, dock):
        bar.resize(1500, 56)
    for theme in PRESETS:
        ctx.theme.set_theme(theme)
        for mode in ("dark", "light"):
            ctx.theme.set_mode(mode)
            for bar, name in ((top, "top"), (dock, "dock")):
                bar.apply_theme()
                bar.resize(1500, int(bar.meta()[bar.SIZE_KEY]))
                dock.media.update_media(media)
                # Drei Zustände nebeneinander: läuft nicht, minimiert, sichtbar, aktiv
                states = ((False, False, False), (True, False, False), (True, True, False), (True, True, True))
                for button, (running, shown, focused) in zip(dock._buttons.values(), states):
                    set_prop(button, "running", running)
                    set_prop(button, "shown", shown)
                    set_prop(button, "focused", focused)
                save(bar, f"q_{theme}_{mode}_{name}")

            quick = QuickPanel(ctx, "")
            quick.refresh()
            quick.apply_theme()
            quick.adjustSize()
            save(quick, f"q_{theme}_{mode}_quick")

            launcher = ctx.launcher
            launcher.apply_theme()
            launcher.input.setText("reg")
            save(launcher, f"q_{theme}_{mode}_launcher")
            ctx.context_menu.build(True)
            ctx.context_menu.adjustSize()
            save(ctx.context_menu, f"q_{theme}_{mode}_menu")
            ctx.settings.apply_theme()
            ctx.settings.tabs.setCurrentIndex(ctx.settings.setup_tab)
            save(ctx.settings, f"q_{theme}_{mode}_settings")
    ctx.theme.set_theme("pop_basic")
    ctx.theme.set_mode("dark")
    ctx.settings.apply_theme()
    ctx.settings.resize(760, 900)
    for index in (1, 4):
        ctx.settings.tabs.setCurrentIndex(index)
        save(ctx.settings, f"q_settings_tab{index}")
    from ui.widgets.media import MediaWidget
    window = MediaWidget(ctx, {"id": "x", "type": "media", "x": 0, "y": 0, "data": {}})
    window.apply_theme()
    window.box.update_media(media)
    window._sync(media)
    save(window, "q_widget_media")
    ctx._closed = True
    print("Fehler:", len(errors))
    for error in errors[:6]:
        print(error)
    import os
    os._exit(1 if errors else 0)


if __name__ == "__main__":
    main()
