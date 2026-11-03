"""Screenshots für das README, mit Beispieldaten statt persönlichen: englische Oberfläche, kein Benutzername, keine
echten Fenster, Benachrichtigungen oder Medien. Läuft auf dem unsichtbaren Desktop:

    python tools/quiet_tests.py LOG tools/readme_shots.py docs/screenshots
"""
from __future__ import annotations

import random
import sys
import tempfile
import time
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.test_logic as logic  # noqa: E402  (QApplication und alle Stubs für Fenster, AppBars und Zwischenablage)
from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as popwin_main  # noqa: E402
from config.config_manager import ConfigManager  # noqa: E402
from core import nova_artifacts  # noqa: E402
from core.nova_artifacts import ArtifactStore  # noqa: E402
from core.nova_brain import ChatLog, Memory  # noqa: E402
from ui.widgets import WIDGET_TYPES  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "screenshots")
OUT.mkdir(parents=True, exist_ok=True)


def settle(seconds: float = 0.4) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


class Demo(popwin_main.PopWinApp):
    """Ein PopWin mit Wegwerf-Einstellungen und Beispieldaten."""

    def __init__(self):
        folder = Path(tempfile.mkdtemp())
        config = ConfigManager(folder / "settings.json")
        keys: dict[str, str] = {}
        config.api_key = lambda user="gemini_api_key": keys.get(user, "demo")
        config.set_api_key = lambda key, user="gemini_api_key": True
        config.data["language"] = "en"
        config.data["animations"]["enabled"] = False
        for field, value in (("show_user", False), ("show_tray", False), ("show_cloud", False), ("clock_place", "top_right"),
                             ("show_media", False), ("show_notifications", True), ("show_network", False), ("show_mic", False),
                             ("clock_seconds", False)):
            config.data["monitor_shared"][field] = value
        config.data["taskbar"]["pinned"] = [  # Windows-eigene Apps, die es auf jedem PC gibt
            {"name": "Explorer", "target": "explorer.exe"},
            {"name": "Terminal", "target": config.data["taskbar"]["pinned"][1]["target"], "exe": "windowsterminal.exe"},
            {"name": "Rechner", "target": "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
            {"name": "Paint", "target": "shell:AppsFolder\\Microsoft.Paint_8wekyb3d8bbwe!App"},
            {"name": "Editor", "target": "shell:AppsFolder\\Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
        ]
        super().__init__(config)
        self.windows = lambda: []  # keine echten Fenster des Nutzers in der Taskbar
        self.nova.memory = Memory(folder / "memory.json")
        self.nova.toolbox.memory = self.nova.memory
        self.nova.log = ChatLog(folder / "chat.json")
        self.nova.use_artifacts(ArtifactStore(folder / "artifacts"))
        self.config.data["nova"]["reserve_space"] = False
        self.wallpaper.sync = lambda: None
        self.notifications.notices = []


def paint_backdrop(size) -> QPixmap:
    """Ein ruhiger Galaxie-Hintergrund, damit die durchscheinenden Leisten etwas zum Zeigen haben."""
    pixmap = QPixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, size.width(), size.height())
    sky.setColorAt(0, QColor("#0b0d24"))
    sky.setColorAt(0.55, QColor("#141a3c"))
    sky.setColorAt(1, QColor("#26123a"))
    painter.fillRect(pixmap.rect(), sky)
    for x, y, radius, color in ((0.28, 0.42, 0.45, "#6a4cff"), (0.72, 0.30, 0.38, "#00a8ff"), (0.55, 0.78, 0.40, "#ff3fa4")):
        glow = QRadialGradient(QPointF(size.width() * x, size.height() * y), size.width() * radius)
        tone = QColor(color)
        tone.setAlphaF(0.22)
        glow.setColorAt(0, tone)
        tone.setAlphaF(0.0)
        glow.setColorAt(1, tone)
        painter.fillRect(pixmap.rect(), glow)
    chance = random.Random(7)
    painter.setPen(Qt.PenStyle.NoPen)
    for _ in range(420):
        star = QColor(255, 255, 255, chance.randint(40, 210))
        painter.setBrush(star)
        radius = chance.choice((0.6, 0.8, 1.0, 1.4))
        painter.drawEllipse(QPointF(chance.random() * size.width(), chance.random() * size.height()), radius, radius)
    painter.end()
    return pixmap


def grab(window, name: str) -> QPixmap:
    shot = window.grab()
    shot.save(str(OUT / name))
    return shot


def on_backdrop(window, name: str, margin: int = 36) -> None:
    """Ein Fenster auf einem Stück Hintergrund, damit Ecken und Transparenz wie am echten Bildschirm wirken."""
    shot = window.grab()
    canvas = paint_backdrop(shot.deviceIndependentSize().toSize() + QRect(0, 0, 2 * margin, 2 * margin).size())
    painter = QPainter(canvas)
    painter.drawPixmap(margin, margin, shot)
    painter.end()
    canvas.save(str(OUT / name))


def demo_chat(ctx) -> None:
    log = ctx.nova.log
    log.add("user", "Set up my domains at OVH and keep an eye on them")
    log.add("nova", "On it! I created the task **Set up OVH** with five steps and a monitor that checks your domains, "
                    "DNS zones and expiry dates every 6 hours.\n\nYou'll get a notice as soon as something changes. "
                    "First step: create the API keys, I've put the fields for them right here.")
    log.add("user", "Speak a bit calmer, please")
    log.add("nova", "Sure, I switched my speaking style to *calm and a bit slower*. Better?")


def demo_artifacts(ctx) -> None:
    store = ctx.nova.artifacts
    monitor = nova_artifacts.new_artifact("Watch OVH domains", "ueberwachung", "Checks domains, expiry, name servers and DNS.")
    nova_artifacts.schedule(monitor, 6)
    nova_artifacts.record_check(monitor, [])
    nova_artifacts.record_check(monitor, ["DNS record of example.com changed: www A 1.2.3.4 → www A 5.6.7.8",
                                          "Domain example.com expires in 14 days"])
    store.save(monitor)
    task = nova_artifacts.new_artifact("Set up OVH", "aufgabe", "", ["Create API keys", "Enter keys in PopWin", "First check",
                                                                     "Review expiry dates", "Set up DNS records"])
    for step in task["steps"][:3]:
        step["done"] = True
    store.save(task)
    tool = nova_artifacts.new_artifact("Check certificates", "werkzeug", "import ssl, socket\nprint('ok')")
    tool["read_only"] = True
    store.save(tool)
    store.save(nova_artifacts.new_artifact("Website ideas", "notiz", "Blog, contact form, dark design."))


def demo_widgets(ctx, area) -> list:
    specs = [("clock", area.left() + 60, area.top() + 70, {"show_date": False}),
             ("sysmon", area.left() + 60, area.top() + 250, {}),
             ("notes", area.left() + 330, area.top() + 70, {}),
             ("stopwatch", area.left() + 330, area.top() + 330, {})]
    windows = []
    for kind, x, y, options in specs:
        if kind not in WIDGET_TYPES:
            continue
        spec = {"id": kind, "type": kind, "x": x, "y": y, "data": {"text": "Buy milk\nCall Anna at 5\nRelease PopWin 3.7"}
                if kind == "notes" else {}, "options": options}
        ctx.config.data["widgets"].append(spec)
        window = WIDGET_TYPES[kind](ctx, spec)
        ctx.widgets.windows[kind] = window
        window.show_desklet()
        windows.append(window)
    return windows


def compose(screen_rect: QRect, windows: list, name: str) -> None:
    canvas = paint_backdrop(screen_rect.size())
    painter = QPainter(canvas)
    for window in windows:
        if window is not None and window.isVisible():
            spot = window.frameGeometry().topLeft() - screen_rect.topLeft()
            painter.drawPixmap(spot, window.grab())
    painter.end()
    canvas.save(str(OUT / name))


def main() -> None:
    # Wie ein normaler Build: Dev-Teile (Claude-Knopf, Dev-Gruppen, OVH) gehören nicht in öffentliche Bilder.
    for target in ("core.dev.is_dev", "ui.taskbar.is_dev", "ui.settings_window.is_dev", "core.nova_tools.is_dev"):
        unittest.mock.patch(target, return_value=False).start()
    ctx = Demo()
    demo_chat(ctx)
    demo_artifacts(ctx)
    screen = QGuiApplication.primaryScreen()
    ctx.rebuild_bars()
    settle(1.0)
    bars = [bar for bar in ctx.bars if bar.screen_id == ctx.bars[0].screen_id] if ctx.bars else []
    widgets = demo_widgets(ctx, screen.availableGeometry())
    ctx.apps.start()  # das App-Verzeichnis für die Suche
    for cpu, ram, gpu in ((18, 46, 9), (27, 47, 12), (23, 48, 31), (35, 48, 22)):  # Beispielwerte statt echter Messungen
        ctx.status.state.update({"cpu": cpu, "ram": ram, "gpu": gpu})
        ctx.status.updated.emit(ctx.status.state)
        settle(0.3)
    settle(1.5)

    # 1. Der ganze Desktop: Leisten, Widgets und Nova
    panel = ctx.nova_panel
    panel.reveal()
    settle(1.8)
    compose(screen.geometry(), [*widgets, *bars, panel], "01_desktop.png")
    grab(panel, "02_nova_chat.png")
    panel.show_page(1)
    settle(0.6)
    grab(panel, "03_nova_artifacts.png")
    panel.artifacts.open_artifact(next(item["id"] for item in ctx.nova.artifacts.all() if item["kind"] == "aufgabe"))
    settle(0.6)
    grab(panel, "04_nova_task.png")
    panel.hide()

    # 2. Home-Menü und Suche
    dock = next((bar for bar in bars if bar.__class__.__name__ == "Dock"), None)
    ctx.start_menu.toggle(dock.start if dock else None)
    settle(0.8)
    on_backdrop(ctx.start_menu, "05_home_menu.png")
    ctx.start_menu.hide()
    ctx.launcher.open()
    ctx.apps.wait(5000)
    ctx.launcher.input.setText("calc")
    settle(0.8)
    on_backdrop(ctx.launcher, "06_search.png")
    ctx.launcher.hide()

    # 3. Einstellungen
    settings = ctx.settings
    settings.resize(1100, 760)
    settings.open_page()
    for number, (page, name) in enumerate((("Taskbar", "taskbar"), ("Uhr", "clock"), ("Nova", "nova")), 7):
        settings.open_named(page)  # deutsche Namen: open_named übersetzt sie selbst
        settle(0.6)
        grab(settings, f"{number:02}_settings_{name}.png")
    settings.hide()
    print("fertig:", sorted(path.name for path in OUT.glob("*.png")))
    logic.release_contexts()


if __name__ == "__main__":
    main()
