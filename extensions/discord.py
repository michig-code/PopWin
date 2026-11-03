"""Erweiterung Discord: zeigt die gewählten Monitore gleichzeitig als Kamera oder als teilbares Fenster."""
from __future__ import annotations

import subprocess

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton

from core.paths import ROOT, app_command
from extensions import Extension


class DiscordStream(Extension):
    key = "discord"
    title = "Discord: mehrere Monitore streamen"
    description = "Setzt die gewählten Monitore zu einem Bild zusammen, das Discord als Kamera oder Fenster zeigt."
    AUTO_START = False  # Ein Stream zeigt den ganzen Bildschirm: Er startet nur auf ausdrücklichen Klick.

    def __init__(self, ctx):
        super().__init__(ctx)
        self.process: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def option(self, name: str):
        return self.ctx.config.get(f"extensions.discord.{name}")

    def set_option(self, name: str, value) -> None:
        self.ctx.config.set(f"extensions.discord.{name}", value)
        if self.running:  # der Stream liest seine Einstellungen beim Start
            self.stop()
            self.start()

    def start(self) -> None:
        if not self.running:
            self.process = subprocess.Popen(app_command("stream"), cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()

    def settings(self, form, window) -> None:
        chosen = self.option("monitors")
        form.addRow(QLabel("Diese Monitore werden gezeigt:", objectName="Muted"))
        entries = window._monitor_entries()

        def store() -> None:
            self.set_option("monitors", [key for key, box in boxes.items() if box.isChecked()])

        boxes = {}
        for entry in entries:
            box = QCheckBox(entry.title)
            box.setChecked(not chosen or entry.key in chosen)  # noch nichts gewählt: alle
            box.toggled.connect(lambda _on: store())
            boxes[entry.key] = box
            form.addRow(box)

        def choice(field: str, label: str, options: dict) -> None:
            combo = QComboBox()
            for value, text in options.items():
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(self.option(field))))
            combo.currentIndexChanged.connect(lambda _i: self.set_option(field, combo.currentData()))
            form.addRow(label, combo)

        choice("layout", "Anordnung", {"windows": "Wie in Windows", "row": "Nebeneinander"})
        choice("size", "Bildgröße", {1080: "1920 × 1080", 720: "1280 × 720"})
        choice("fps", "Bilder pro Sekunde", {15: "15 (schont den PC)", 30: "30"})
        button = QPushButton("Stream stoppen" if self.running else "Stream starten", objectName="Primary")

        def toggle() -> None:
            self.stop() if self.running else self.start()
            button.setText("Stream stoppen" if self.running else "Stream starten")

        button.clicked.connect(toggle)
        form.addRow(button)
        form.addRow(QLabel("In Discord: Kamera „OBS Virtual Camera“ wählen. Oder das Fenster „PopWin Stream“ "
                           "über Bildschirm teilen senden. Zuschauer sehen alles auf den gewählten Monitoren, "
                           "auch Benachrichtigungen und Passwörter.", objectName="Muted", wordWrap=True))
