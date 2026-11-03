"""Hinweis auf eine neue Version: Änderungen lesen, dann aktualisieren, später oder diese Version überspringen."""
from __future__ import annotations

import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser

from core import updater, win_api
from core.paths import FROZEN, PORTABLE
from core.version import VERSION
from ui.task_panel import CenteredPanel


class _Relay(QObject):
    """Bringt Fortschritt und Ergebnis des Downloads zurück in den Qt-Thread."""
    progress = Signal(int, int)
    done = Signal(str, str)  # Pfad, Fehler


class UpdateDialog(CenteredPanel):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.release: dict = {}
        self.title = QLabel(objectName="PanelTitle")
        self.subtitle = QLabel(objectName="Muted")
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(220)
        self.notes.setMaximumHeight(360)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        self.error = QLabel(objectName="Danger", wordWrap=True)
        self.error.hide()
        row = QHBoxLayout()
        self.skip = QPushButton("Diese Version überspringen", objectName="Secondary")
        self.later = QPushButton("Später", objectName="Secondary")
        self.install = QPushButton("Jetzt aktualisieren", objectName="Primary")
        self.skip.clicked.connect(self._skip)
        self.later.clicked.connect(self.dismiss)
        self.install.clicked.connect(self._install)
        row.addWidget(self.skip)
        row.addStretch(1)
        row.addWidget(self.later)
        row.addWidget(self.install)
        for widget in (self.title, self.subtitle, self.notes, self.bar, self.error):
            self.body.addWidget(widget)
        self.body.addLayout(row)
        self.relay = _Relay(self)
        self.relay.progress.connect(lambda done, total: self.bar.setValue(round(done * 100 / total)) if total else None)
        self.relay.done.connect(self._downloaded)

    def offer(self, release: dict) -> None:
        self.release = release
        self.title.setText(f"PopWin {release['version']} ist da")
        self.subtitle.setText(f"Du hast Version {VERSION}. Das hat sich geändert:")
        self.notes.setMarkdown(release["notes"] or "Keine Beschreibung.")
        self.error.hide()
        self.bar.hide()
        # Aus dem Quellordner (Entwicklung) oder als Portable gibt es kein Setup zum Starten: dann die Seite öffnen.
        self.install.setText("Jetzt aktualisieren" if self._can_install() else "Download-Seite öffnen")
        self.install.setEnabled(True)
        self.present(560)

    def _can_install(self) -> bool:
        return FROZEN and not PORTABLE and bool(self.release.get("setup"))

    def _skip(self) -> None:
        self.ctx.config.set("updates.skipped", self.release.get("version", ""), notify=False)
        self.dismiss()

    def _install(self) -> None:
        if not self._can_install():
            win_api.shell_open(self.release.get("page", ""))
            self.dismiss()
            return
        self.install.setEnabled(False)
        self.install.setText("Wird geladen …")
        self.bar.setValue(0)
        self.bar.show()
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self) -> None:
        try:
            path = updater.download(self.release["setup"], self.relay.progress.emit)
        except Exception as exc:  # Netzfehler kommen als UpdateError mit lesbarer Meldung, die eigenen Prüfungen als OSError
            self.relay.done.emit("", exc.message if isinstance(exc, updater.UpdateError) else str(exc))
            return
        self.relay.done.emit(str(path), "")

    def _downloaded(self, path: str, error: str) -> None:
        if error:
            self.error.setText(f"Das Update konnte nicht geladen werden: {error}")
            self.error.show()
            self.install.setText("Nochmal versuchen")
            self.install.setEnabled(True)
            return
        # Das Setup beendet PopWin selbst, ersetzt die Dateien und startet es danach wieder.
        subprocess.Popen([path], close_fds=True)
        self.dismiss()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)
