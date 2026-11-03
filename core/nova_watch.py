"""Prüft Novas Überwachungen nach Zeitplan und erinnert an fällige Aufgaben. Gemeldet wird nur, was neu ist.

Von selbst läuft nur, was keine Nachfrage braucht: eingebaute Prüfungen und Werkzeuge, die nur lesen und für genau ihren
Code schon einmal erlaubt wurden. Alles andere wartet, bis der Host es im Panel anstößt.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from PySide6.QtCore import QObject, QTimer, Signal

from core import ai_actions, nova_artifacts, nova_tools

log = logging.getLogger(__name__)

TICK_MS = 60_000
FIRST_TICK_MS = 15_000  # erst wenn PopWin steht
RESULT_PREVIEW = 300


def due_reminder(artifact: dict, today: date) -> str:
    """Eine Erinnerung pro Fälligkeit, nicht bei jedem Blick."""
    due = str(artifact.get("due", ""))[:10]
    if artifact.get("kind") != "aufgabe" or not nova_artifacts.is_open(artifact) or not due or artifact.get("reminded") == due:
        return ""
    try:
        when = date.fromisoformat(due)
    except ValueError:
        return ""
    if when > today:
        return ""
    return f"Aufgabe fällig: {artifact['title']}" if when == today else f"Aufgabe überfällig seit {when:%d.%m.}: {artifact['title']}"


def run_check(nova, artifact: dict, confirmed: bool = False, today: date | None = None) -> dict:
    """Läuft im Hintergrund. Liefert, was sich am Artefakt ändern soll; geschrieben wird erst im Hauptthread."""
    config = nova.ctx.config
    try:
        if artifact.get("check") == "ovh_domains":
            ovh = nova_tools.ovh_module()
            data = ovh.snapshot(ovh.client(config))
            findings, warned = ovh.changes(artifact.get("snapshot"), data, today or date.today(), artifact.get("warned", []))
            return {"findings": findings, "snapshot": data, "warned": warned, "failed": ""}
        if artifact.get("tool"):
            helper = nova.artifacts.get(artifact["tool"])
            if not (confirmed or nova.toolbox.preapproved("artifact_run_tool", {"id": helper["id"]})):
                return {"findings": [], "failed": f"Das Werkzeug „{helper['title']}“ wartet auf deine Freigabe (Jetzt prüfen)."}
            output = nova.toolbox.run_artifact_tool(helper)
            before = artifact.get("snapshot")
            findings = [] if before is None or before == output else [f"Neues Ergebnis von {helper['title']}: {output[:RESULT_PREVIEW]}"]
            return {"findings": findings, "snapshot": output, "failed": ""}
        return {"findings": [], "failed": "Für diese Überwachung ist keine Prüfung eingestellt."}
    except Exception as exc:  # Netz weg, Keys falsch: steht im Verlauf, PopWin läuft weiter
        message = ai_actions.redact(str(exc))
        for secret in nova_tools.ovh_secrets(config) if artifact.get("check") == "ovh_domains" else ():
            message = message.replace(secret, "***")
        log.info("Prüfung von %s fehlgeschlagen: %s", artifact.get("id"), type(exc).__name__)
        return {"findings": [], "failed": message[:300] or type(exc).__name__}


class ArtifactWatcher(QObject):
    checked = Signal(str, object)  # Kennung, Ergebnis; kommt aus dem Prüf-Thread in den Hauptthread
    tool_done = Signal(str)
    busy_changed = Signal(str, bool)

    def __init__(self, nova):
        super().__init__(nova)
        self.nova = nova
        self.running: set[str] = set()
        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.checked.connect(self._done)
        self.tool_done.connect(self._tool_done)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(FIRST_TICK_MS, self.tick)

    def stop(self) -> None:
        self.timer.stop()

    def tick(self, moment: datetime | None = None) -> None:
        moment = moment or datetime.now()
        store = self.nova.artifacts
        for artifact in list(store.items.values()):
            reminder = due_reminder(artifact, moment.date())
            if reminder:
                artifact["reminded"] = str(artifact["due"])[:10]
                nova_artifacts.add_history(artifact, reminder, moment)
                store.save(artifact, moment)
                self.nova.notice.emit(artifact["title"], reminder, artifact["id"])
                self.nova.artifacts_changed.emit()
        for artifact in store.due(moment):
            if artifact["kind"] == "ueberwachung":
                self.check(artifact["id"])

    def check(self, artifact_id: str, confirmed: bool = False) -> bool:
        """Prüft jetzt. `confirmed` heißt: Der Host hat den Code eben selbst gesehen und Ja gesagt."""
        if artifact_id in self.running:
            return False
        artifact = self.nova.artifacts.get(artifact_id)
        self.running.add(artifact_id)
        self.busy_changed.emit(artifact_id, True)
        copy = dict(artifact)
        threading.Thread(target=lambda: self.checked.emit(artifact_id, run_check(self.nova, copy, confirmed)),
                         daemon=True, name="nova-watch").start()
        return True

    def run_tool(self, artifact_id: str) -> bool:
        """Ein Werkzeug vom Knopf im Panel aus. Der Host hat den Code dort gesehen und bestätigt."""
        if artifact_id in self.running:
            return False
        artifact = self.nova.artifacts.get(artifact_id)
        self.running.add(artifact_id)
        self.busy_changed.emit(artifact_id, True)

        def work() -> None:
            try:
                self.nova.toolbox.run_artifact_tool(artifact)
            except Exception as exc:
                nova_artifacts.add_history(artifact, f"Fehlgeschlagen: {ai_actions.redact(str(exc))[:300]}")
                self.nova.artifacts.save(artifact)
            self.tool_done.emit(artifact_id)
        threading.Thread(target=work, daemon=True, name="nova-tool").start()
        return True

    def _tool_done(self, artifact_id: str) -> None:
        self.running.discard(artifact_id)
        self.busy_changed.emit(artifact_id, False)
        self.nova.artifacts_changed.emit()

    def _done(self, artifact_id: str, result: dict) -> None:
        self.running.discard(artifact_id)
        self.busy_changed.emit(artifact_id, False)
        store = self.nova.artifacts
        try:
            artifact = store.get(artifact_id)
        except KeyError:  # inzwischen gelöscht
            return
        if "snapshot" in result:
            artifact["snapshot"] = result["snapshot"]
        if "warned" in result:
            artifact["warned"] = result["warned"]
        nova_artifacts.record_check(artifact, result["findings"], failed=result["failed"])
        store.save(artifact)
        for finding in result["findings"]:
            self.nova.notice.emit(artifact["title"], finding, artifact_id)
        self.nova.artifacts_changed.emit()
