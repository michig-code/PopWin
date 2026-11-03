"""KI-Aufgaben: Bestätigung, Aufgabenleiste am Rand, Fehlerprotokoll und Abschluss-Popup."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from core import ai_actions
from core.gemini_client import RepairWorker
from ui.animation import WindowAnimator
from ui.base import PopupPanel, ThemedWindow

MAX_REPAIRS = 2
DONE_SECONDS = 5
STATUS = {"wait": "wartet", "run": "läuft …", "ok": "fertig", "fail": "fehlgeschlagen", "skip": "übersprungen"}


class CenteredPanel(PopupPanel):
    """Popup in der Bildschirmmitte, das nicht beim ersten Fokusverlust verschwindet."""
    STAYS = True

    def event(self, event) -> bool:
        return QWidget.event(self, event) if self.STAYS else super().event(event)

    def present(self, width: int = 460) -> None:
        self._take_over()
        self.setFixedWidth(width)
        self.apply_theme()
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - width // 2, area.top() + area.height() // 4)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.animator.enter(1.0, slide=-10)


class ActionConfirm(CenteredPanel):
    """Fragt vor jeder KI-Aktion nach. Bei riskanten Befehlen zählt nur der Klick."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.question = QLabel(objectName="PanelTitle")
        self.question.setWordWrap(True)
        self.warning = QLabel("Dieser Befehl kann Daten oder das System verändern. Prüfe ihn genau.", objectName="Danger")
        self.warning.setWordWrap(True)
        self.command = QLabel(objectName="Muted")
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row = QHBoxLayout()
        row.addStretch(1)
        self.no = QPushButton("Nein", objectName="Secondary")
        self.yes = QPushButton("Ja", objectName="Primary")
        self.no.clicked.connect(lambda: self._answer(False))
        self.yes.clicked.connect(lambda: self._answer(True))
        row.addWidget(self.no)
        row.addWidget(self.yes)
        for widget in (self.question, self.warning, self.command):
            self.body.addWidget(widget)
        self.body.addLayout(row)
        self._callback = None
        self._risky = False

    def ask(self, task: dict, callback) -> None:
        self._callback = callback
        self._risky = ai_actions.is_risky(task)
        self.question.setText(f"Ist es okay, wenn ich folgende Aktion ausführe: {task['summary']}?")
        shown = task["command"] or f"{task['setting']} = {task['value']}"
        self.command.setText(ai_actions.redact(shown))
        self.warning.setVisible(self._risky)
        self.present()

    def _answer(self, ok: bool) -> None:
        callback, self._callback = self._callback, None
        self.dismiss()
        if callback:
            callback(ok)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self._risky:
                self._answer(True)
        elif event.key() == Qt.Key.Key_Escape:
            self._answer(False)
        else:
            super().keyPressEvent(event)


class DonePopup(CenteredPanel):
    """Meldet das Ende der Aufgaben; OK oder 5 Sekunden warten."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.text = QLabel(objectName="PanelTitle")
        self.text.setWordWrap(True)
        self.ok = QPushButton(objectName="Primary")
        self.ok.clicked.connect(self.dismiss)
        self.body.addWidget(self.text)
        self.body.addWidget(self.ok)
        self._left = 0
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._tick)

    def announce(self, done: int, total: int) -> None:
        self.text.setText(f"Alle Aufgaben erledigt ({done} von {total})." if done == total
                          else f"Fertig: {done} von {total} Aufgaben erledigt.")
        self._left = DONE_SECONDS
        self.ok.setText(f"OK ({self._left})")
        self._timer.start()
        self.present(340)

    def _tick(self) -> None:
        self._left -= 1
        self.ok.setText(f"OK ({self._left})")
        if self._left <= 0:
            self.dismiss()

    def dismiss(self) -> None:
        self._timer.stop()
        super().dismiss()


class ErrorLog(CenteredPanel):
    """Fehlerprotokoll mit Ursache, kopierbarem Prompt und Reparatur durch Gemini."""

    def __init__(self, ctx, controller: "TaskController"):
        super().__init__(ctx)
        self.controller = controller
        self.title = QLabel("Aufgabe fehlgeschlagen", objectName="PanelTitle")
        self.cause = QLabel(objectName="MediaTitle")
        self.cause.setWordWrap(True)
        self.details = QLabel(objectName="Muted")
        self.details.setWordWrap(True)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFixedHeight(120)
        self.prompt_label = QLabel("Prompt zum Beheben", objectName="Muted")
        self.prompt = QPlainTextEdit(readOnly=True)
        self.prompt.setFixedHeight(100)
        for widget in (self.title, self.cause, self.details, self.log, self.prompt_label, self.prompt):
            self.body.addWidget(widget)
        row = QHBoxLayout()
        self.copy = QPushButton("Prompt kopieren", objectName="Secondary")
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.prompt.toPlainText()))
        self.fix = QPushButton("Von Gemini beheben lassen", objectName="Primary")
        self.fix.clicked.connect(controller.repair)
        skip = QPushButton("Überspringen", objectName="Secondary")
        skip.clicked.connect(controller.skip)
        stop = QPushButton("Abbrechen", objectName="Danger")
        stop.clicked.connect(controller.abort)
        for button in (self.copy, self.fix, skip, stop):
            row.addWidget(button)
        self.body.addLayout(row)

    def show_failure(self, task: dict, output: str, analysis: dict, can_repair: bool) -> None:
        command = task["command"] or task["setting"]
        cause = analysis.get("ursache") or "Die Ursache konnte nicht ermittelt werden."
        self.cause.setText(ai_actions.redact(str(cause)))
        self.details.setText(ai_actions.redact(str(analysis.get("erklaerung") or "")))
        self.log.setPlainText(ai_actions.redact(f"Befehl: {command}\n\n{output or '(keine Ausgabe)'}"))
        self.prompt.setPlainText(ai_actions.redact(
            f"Dieser Windows-Befehl ist in PopWin fehlgeschlagen. Finde die Ursache und nenne die Lösung.\n\n"
            f"Aufgabe: {task['summary']}\nBefehl: {command}\nAusgabe:\n{output}\nVermutete Ursache: {cause}"))
        self.fix.setEnabled(can_repair and bool(analysis.get("command")))
        self.present(620)


class TaskRow(QWidget):
    def __init__(self, panel: "TaskPanel", task: dict):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        self.check = QCheckBox()
        self.check.setChecked(True)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.summary = QLabel(task["summary"], objectName="MediaTitle")
        self.summary.setWordWrap(True)
        self.status = QLabel(STATUS["wait"], objectName="Muted")
        text.addWidget(self.summary)
        text.addWidget(self.status)
        row.addWidget(self.check)
        row.addLayout(text, 1)

    def set_status(self, key: str) -> None:
        self.status.setText(STATUS[key])
        self.check.setEnabled(False)


class TaskPanel(ThemedWindow):
    """Aufgabenleiste am rechten oder linken Bildschirmrand."""
    WIDTH = 320

    def __init__(self, ctx, controller: "TaskController"):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(ctx, flags)
        self.controller = controller
        self.animator = WindowAnimator(ctx, self)
        self.setFixedWidth(self.WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QWidget(self, objectName="Panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(panel)
        self.body = QVBoxLayout(panel)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(6)
        self.body.addWidget(QLabel("KI-Aufgaben", objectName="PanelTitle"))
        self.hint = QLabel("Abgewählte Aufgaben werden übersprungen.", objectName="Muted")
        self.body.addWidget(self.hint)
        self.list = QVBoxLayout()
        self.body.addLayout(self.list)
        row = QHBoxLayout()
        self.start = QPushButton("Ausführen", objectName="Primary")
        self.start.clicked.connect(controller.begin)
        self.cancel = QPushButton("Abbrechen", objectName="Secondary")
        self.cancel.clicked.connect(controller.abort)
        row.addWidget(self.cancel)
        row.addWidget(self.start)
        self.body.addLayout(row)
        self.rows: list[TaskRow] = []

    def load(self, tasks: list[dict]) -> None:
        while self.list.count():
            self.list.takeAt(0).widget().deleteLater()
        self.rows = [TaskRow(self, task) for task in tasks]
        for row in self.rows:
            self.list.addWidget(row)
        self.start.setEnabled(True)
        self.hint.show()
        self.apply_theme()
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        left = self.ctx.config.get("launcher.task_side") == "left"
        self.move(area.left() + 12 if left else area.right() - self.WIDTH - 12, area.top() + 60)
        self.show()
        self.animator.enter(1.0, slide=0)


class TaskController(QObject):
    """Arbeitet Aufgaben strikt nacheinander ab: Der nächste Task startet erst nach dem Ende des laufenden."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.confirm = ActionConfirm(ctx)
        self.panel = TaskPanel(ctx, self)
        self.done_popup = DonePopup(ctx)
        self.error_log = ErrorLog(ctx, self)
        self.tasks: list[dict] = []
        self.index = 0
        self.done = 0
        self.runner: ai_actions.TaskRunner | None = None
        self._analysis: dict = {}
        self._output = ""
        self._repairs = 0
        self._worker = None
        self._batch = False

    # --- Einstieg -----------------------------------------------------------------
    def propose(self, tasks: list[dict]) -> None:
        self.tasks = tasks
        self.index = self.done = self._repairs = 0
        self._batch = len(tasks) > 1
        if self._batch:
            self.panel.load(tasks)
            return
        self.confirm.ask(tasks[0], lambda ok: self._run_current() if ok else None)

    def begin(self) -> None:
        self.panel.start.setEnabled(False)
        self.panel.hint.hide()
        self._next()

    def abort(self) -> None:
        if self.runner:
            self.runner.cancel()
        self.error_log.dismiss()
        self.panel.animator.leave()
        self.tasks = []

    def skip(self) -> None:
        self.error_log.dismiss()
        self._mark("skip")
        self.index += 1
        self._repairs = 0
        self._next()

    # --- Ablauf ---------------------------------------------------------------------
    def _mark(self, status: str) -> None:
        if self._batch and self.index < len(self.panel.rows):
            self.panel.rows[self.index].set_status(status)

    def _next(self) -> None:
        if self.index >= len(self.tasks):
            self._finish()
            return
        if self._batch and not self.panel.rows[self.index].check.isChecked():
            self._mark("skip")
            self.index += 1
            self._next()
            return
        if ai_actions.is_risky(self.tasks[self.index]) and self._batch:
            self.confirm.ask(self.tasks[self.index], self._risky_answer)
            return
        self._run_current()

    def _risky_answer(self, ok: bool) -> None:
        if ok:
            self._run_current()
        else:
            self.skip()

    def _run_current(self) -> None:
        task = self.tasks[self.index]
        self._mark("run")
        if task["kind"] == "setting":
            error = ai_actions.apply_setting(self.ctx, task["setting"], task["value"])
            QTimer.singleShot(0, lambda: self._finished(not error, error))
            return
        self.runner = ai_actions.TaskRunner(task, self)
        self.runner.finished_task.connect(self._finished)
        self.runner.start()

    def _finished(self, ok: bool, output: str) -> None:
        if not self.tasks:
            return
        self.runner = None
        if ok:
            self._mark("ok")
            self.done += 1
            self.index += 1
            self._repairs = 0
            self._next()
            return
        self._mark("fail")
        self._output = output
        self._diagnose()

    def _finish(self) -> None:
        total = len(self.tasks)
        if self._batch:
            self.panel.animator.leave()
        self.done_popup.announce(self.done, total)

    # --- Fehler -----------------------------------------------------------------------
    def _diagnose(self) -> None:
        task = self.tasks[self.index]
        key = self.ctx.config.api_key()
        if not key:
            self._on_analysis({"ursache": "Kein API-Key hinterlegt, deshalb keine Analyse."}, "")
            return
        report = ai_actions.redact(f"Aufgabe: {task['summary']}\nBefehl ({task['kind']}): {task['command']}\n"
                                   f"Ausgabe:\n{self._output}")
        self._worker = RepairWorker(key, self.ctx.config.models("repair"), report, self)
        self._worker.analysed.connect(self._on_analysis)
        self._worker.start()

    def _on_analysis(self, analysis: dict, _model: str) -> None:
        self._analysis = analysis
        self.error_log.show_failure(self.tasks[self.index], self._output, analysis, self._repairs < MAX_REPAIRS)

    def repair(self) -> None:
        analysis = self._analysis
        task = {"summary": analysis.get("action_summary") or "Korrektur ausführen",
                "kind": analysis.get("kind") if analysis.get("kind") in ("powershell", "cmd", "uri") else "powershell",
                "command": str(analysis.get("command") or ""), "setting": "", "value": None}
        self._repairs += 1
        self.error_log.dismiss()
        self.confirm.ask(task, lambda ok: self._apply_repair(task) if ok else self.abort())

    def _apply_repair(self, task: dict) -> None:
        self.tasks[self.index] = task
        self._run_current()
