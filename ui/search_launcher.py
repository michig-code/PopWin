"""Werbefreier Launcher: lokale Apps, Systemaktionen, Win+R-Befehle, optional Gemini."""
from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from core import i18n, win_api
from core.ai_actions import settings_catalog
from core.gemini_client import GeminiWorker, PlanWorker
from core.replacements import launch
from core.paths import ROOT
from ui.animation import WindowAnimator
from ui.base import G, IconButton, ThemedWindow, glyph_icon, icon_for_target, set_prop
from ui.search_actions import VoiceInput, situation_actions, typed_actions

STUDIO_ICON = str(ROOT / "assets" / "apps" / "studio.ico")
# Was Win+R ohne PATH-Treffer trotzdem kennt.
RUN_COMMANDS = {
    "cmd": "Eingabeaufforderung", "powershell": "PowerShell", "regedit": "Registrierungs-Editor",
    "control": "Systemsteuerung", "mspaint": "Paint", "notepad": "Editor", "calc": "Rechner",
    "taskmgr": "Task-Manager", "explorer": "Explorer", "msconfig": "Systemkonfiguration",
    "services.msc": "Dienste", "devmgmt.msc": "Geräte-Manager", "diskmgmt.msc": "Datenträgerverwaltung",
    "compmgmt.msc": "Computerverwaltung", "eventvwr.msc": "Ereignisanzeige", "appwiz.cpl": "Programme und Features",
    "ncpa.cpl": "Netzwerkverbindungen", "sysdm.cpl": "Systemeigenschaften", "winver": "Windows-Version",
    "cleanmgr": "Datenträgerbereinigung", "snippingtool": "Snipping Tool",
}
STUDIO_WORDS = ("powershell ise", "powershell_ise", "powershell studio", "ps studio", "ise")


def wants_studio(query: str) -> bool:
    """Wer die ISE sucht, meint in PopWin das Studio. Auch schon beim Tippen, sonst flackert die Liste."""
    typed = " ".join(query.lower().split())
    return len(typed) >= 3 and any(word.startswith(typed) for word in STUDIO_WORDS)


_ON = ("aktivieren", "aktiviere", "einschalten", "anschalten", "an", "ein", "on")
_OFF = ("deaktivieren", "deaktiviere", "ausschalten", "aus", "off")
_LIGHT = ("whitemode", "lightmode", "hellmodus", "heller modus", "white mode", "light mode")
_DARK = ("darkmode", "dunkelmodus", "dunkler modus", "dark mode")


def parse_mode_command(text: str) -> str | None:
    """'whitemode aktivieren' -> 'light', 'whitemode deaktivieren' -> 'dark', sonst None."""
    lowered = " ".join(text.lower().split())
    words = lowered.split()
    wants_off = any(w in words for w in _OFF)
    wants_on = any(w in words for w in _ON)
    if wants_on == wants_off:  # weder noch, oder widersprüchlich
        return None
    if any(n in lowered for n in _LIGHT):
        return "light" if wants_on else "dark"
    if any(n in lowered for n in _DARK):
        return "dark" if wants_on else "light"
    return None


def is_run_target(text: str) -> bool:
    """Würde dieser Text in Win+R etwas öffnen?"""
    head = text.strip()
    if not head:
        return False
    if re.match(r"^(shell:|ms-settings:|https?://)", head, re.I) or os.path.exists(os.path.expandvars(head)):
        return True
    first = head.split()[0].lower()
    return first in RUN_COMMANDS or shutil.which(first) is not None


def run_command(text: str) -> bool:
    command = os.path.expandvars(text.strip())
    if os.path.exists(command) or re.match(r"^(shell:|ms-settings:|https?://)", command, re.I):
        return win_api.shell_open(command)
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if not parts:
        return False
    return win_api.shell_open(parts[0], " ".join(parts[1:]) or None)


def split_ai_prefix(text: str) -> str | None:
    """'? frage' oder 'ai: frage' -> 'frage'; alles andere -> None."""
    stripped = text.strip()
    match = re.match(r"^(?:\?|ai:)\s*(.*)$", stripped, re.I | re.S)
    return match.group(1).strip() if match else None


RESULTS_PADDING = 12  # Innenabstand der Ergebnisliste in den Themes


@dataclass
class Result:
    label: str
    action: Callable[[], None]
    target: str = ""
    glyph: str = ""
    app_name: str = ""  # [Antigravity: App-Name für Nutzungstracking]


class SearchLauncher(ThemedWindow):
    def __init__(self, ctx):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(ctx, flags)
        self.animator = WindowAnimator(ctx, self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QWidget(self, objectName="Panel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outer.addWidget(self.panel)
        body = QVBoxLayout(self.panel)
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(6)

        self.input = QLineEdit(objectName="SearchInput")
        self.input.textChanged.connect(self._update_results)
        self.input.returnPressed.connect(self._accept)
        self.input.installEventFilter(self)
        self.voice = VoiceInput(ctx)
        self.voice.state_changed.connect(self._on_voice_state)
        self.voice.text_ready.connect(self._on_voice_text)
        self.voice.failed.connect(self._show_answer)
        self.mic = IconButton(G.MIC, "Spracheingabe")
        self.mic.clicked.connect(self._toggle_voice)
        self._voice_timer = QTimer(self, interval=250)
        self._voice_timer.timeout.connect(self.voice.check_limit)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.input, 1)
        top.addWidget(self.mic)
        self.results = QListWidget(objectName="Results")
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(lambda item: self._run(item))
        self.answer = QLabel(objectName="Answer")
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer.hide()
        self.model_label = QLabel(objectName="Muted")
        self.model_label.hide()
        body.addLayout(top)
        body.addWidget(self.results)
        body.addWidget(self.answer)
        body.addWidget(self.model_label)

        self._request_id = 0
        self._workers: set = set()
        self._answer_text = ""
        self._opening = False
        self._update_results("")

    # --- Sichtbarkeit -------------------------------------------------------
    def toggle(self) -> None:
        if self.isVisible() and not self.animator.closing:
            self.dismiss()
        else:
            self.open()

    def dismiss(self) -> None:
        self.animator.leave()

    def apply_theme(self) -> None:
        config = self.ctx.config.get("launcher")
        self.theme_name = config["theme"]
        super().apply_theme()
        self.setFixedWidth(config["width"])
        self.setWindowOpacity(config["opacity"])
        self.input.setPlaceholderText(config["placeholder"])
        self.input.setStyleSheet(f"#SearchInput {{ font-size: {config['input_font']}px; }}")
        self.results.setStyleSheet(f"#Results {{ font-size: {config['result_font']}px; }}")

    def open(self) -> None:
        self.apply_theme()
        self.input.clear()
        self._opening = True
        self._update_results("")
        self._opening = False
        config = self.ctx.config.get("launcher")
        screen = QApplication.primaryScreen()
        if config["screen"] == "cursor":
            screen = QApplication.screenAt(self.cursor().pos()) or screen
        geometry = screen.availableGeometry()
        top = {"top": 24, "center": geometry.height() // 2 - 120}.get(config["position"], geometry.height() // 5)
        self.move(geometry.center().x() - config["width"] // 2, geometry.top() + top)
        self.show()
        self._size_results()  # das Design wirkt erst im sichtbaren Fenster, vorher sind die Zeilen zu niedrig gemessen
        win_api.force_foreground(int(self.winId()))
        self.activateWindow()
        self.input.setFocus()
        self.animator.enter(config["opacity"], slide=-10)

    def event(self, event) -> bool:
        # Mit Text bleibt die Suche offen: Sonst wäre das Getippte nach einem Klick daneben verloren.
        if event.type() == QEvent.Type.WindowDeactivate and not self.input.text().strip():
            QTimer.singleShot(0, self.dismiss)
        return super().event(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.results.count():
                step = 1 if key == Qt.Key.Key_Down else -1
                self.results.setCurrentRow((self.results.currentRow() + step) % self.results.count())
                return True
        return super().eventFilter(watched, event)

    # --- Ergebnisse ---------------------------------------------------------
    def _collect(self, text: str) -> list[Result]:
        query = text.strip()
        config = self.ctx.config.get("launcher")
        if not query:
            if config["situation_actions"] and self._opening:
                return [Result(s.label, s.action) for s in situation_actions(self.ctx)]
            return []
        if split_ai_prefix(query) is not None:
            return []
        results: list[Result] = []
        if config["live_actions"]:
            results += [Result(s.label, s.action, glyph=s.glyph) for s in typed_actions(self.ctx, query)]
        mode = parse_mode_command(query)
        if mode:
            label = "Hellen Modus aktivieren" if mode == "light" else "Dunklen Modus aktivieren"
            results.append(Result(label, lambda: self.ctx.set_dark_mode(mode)))
        if wants_studio(query):
            results.append(Result("PowerShell Studio", lambda: self.ctx.open_app("studio"), STUDIO_ICON))
        results += [Result(a.name, lambda t=a.target, n=a.name: self._run_app(n, t), a.target, app_name=a.name)  # [Antigravity]
                    for a in self.ctx.apps.search(query, config["max_results"])]
        if config["show_commands"]:
            lowered = query.lower()
            results += [Result(f"{i18n.translate(desc)}  ({cmd})", lambda c=cmd: run_command(c))  # zusammengesetzt fände i18n sie nicht
                        for cmd, desc in RUN_COMMANDS.items()
                        if cmd.startswith(lowered) or lowered in desc.lower()][:3]
            if is_run_target(query):
                results.append(Result(f"Ausführen: {query}", lambda: run_command(query)))
        return results

    def _fit_height(self) -> None:
        """Innen versteckte Teile zählen erst nach neuer Berechnung; sonst bliebe das Fenster groß und leer."""
        for layout in (self.panel.layout(), self.layout()):
            layout.invalidate()
            layout.activate()
        self.adjustSize()

    def _update_results(self, text: str) -> None:
        self._clear_answer()
        self.results.clear()
        config = self.ctx.config.get("launcher")
        for result in self._collect(text):
            item = QListWidgetItem(result.label)
            item.setData(Qt.ItemDataRole.UserRole, result)
            if result.glyph and config["show_icons"]:
                item.setIcon(glyph_icon(result.glyph, 20, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"))
            elif result.target and config["show_icons"]:
                item.setIcon(icon_for_target(result.target))
            self.results.addItem(item)
        count = self.results.count()
        if count:
            self.results.setCurrentRow(0)
        self.results.setVisible(count > 0)
        self._size_results()

    def _size_results(self) -> None:
        config = self.ctx.config.get("launcher")
        row = max(self.results.sizeHintForRow(0), config["result_font"] + 26)  # Themes polstern unterschiedlich
        visible = min(self.results.count(), config["max_results"])
        self.results.setFixedHeight(visible * row + 2 * self.results.frameWidth() + RESULTS_PADDING)
        self._fit_height()

    # --- [Antigravity Start: App-Start mit Nutzungstracking (Rest: Claude Code)] ---
    def _run_app(self, name: str, target: str) -> None:
        if hasattr(self.ctx, "config") and hasattr(self.ctx.config, "record_app_launch"):
            self.ctx.config.record_app_launch(name, target)
        launch(self.ctx, target)
    # --- [Antigravity Ende: App-Start mit Nutzungstracking] ---

    def _run(self, item: QListWidgetItem) -> None:
        result: Result = item.data(Qt.ItemDataRole.UserRole)
        self.dismiss()
        if getattr(result, "app_name", ""):  # [Antigravity]
            if hasattr(self.ctx, "config") and hasattr(self.ctx.config, "record_app_launch"):
                self.ctx.config.record_app_launch(result.app_name, result.target)
        result.action()

    def _accept(self) -> None:
        text = self.input.text().strip()
        prompt = split_ai_prefix(text)
        if prompt is not None:
            if prompt:
                self._ask(prompt)
        elif self.results.currentItem():
            self._run(self.results.currentItem())
        elif text and self.ctx.config.get("launcher.ai_fallback"):
            if self.ctx.config.get("launcher.ai_actions"):
                self.plan(text)
            else:
                self._ask(text)

    # --- Spracheingabe ----------------------------------------------------------
    def _toggle_voice(self) -> None:
        self.voice.toggle()

    def _on_voice_state(self, recording: bool) -> None:
        set_prop(self.mic, "active", recording)
        self.mic.setToolTip("Aufnahme beenden" if recording else "Spracheingabe")
        if recording:
            self._voice_timer.start()
            self._show_answer("Ich höre zu … Klick auf das Mikrofon beendet die Aufnahme.")
        else:
            self._voice_timer.stop()
            self._show_answer("Wird erkannt …")

    def _on_voice_text(self, text: str) -> None:
        self._clear_answer()
        if text:
            self.input.setText(text)
            self.input.setFocus()

    # --- Gemini -------------------------------------------------------------
    def plan(self, prompt: str) -> None:
        """Lässt die KI Aktionen vorschlagen; ausgeführt wird erst nach Bestätigung."""
        key = self.ctx.config.api_key()
        if not key:
            self._show_answer("Kein Gemini API-Key hinterlegt. Trage ihn unter Einstellungen → Setup ein.")
            return
        self._clear_answer()
        self._show_answer("Denke nach …")
        worker = PlanWorker(self._request_id, key, self.ctx.config.models("search"), prompt,
                            settings_catalog(self.ctx), self)
        worker.planned.connect(self._on_plan)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda w=worker: (self._workers.discard(w), w.deleteLater()))
        self._workers.add(worker)
        worker.start()

    def _on_plan(self, request_id: int, plan: dict, model: str) -> None:
        if request_id != self._request_id:
            return
        self._show_model(model)
        if plan["tasks"]:
            self.dismiss()
            self.ctx.tasks.propose(plan["tasks"])
        else:
            self._show_answer(plan["answer"] or "Dazu habe ich keine passende Aktion gefunden.")

    def _show_model(self, model: str) -> None:
        first = (self.ctx.config.models("search") or [""])[0]
        self.model_label.setText(f"Antwort von {model}" + ("" if model == first else " (Ersatzmodell)"))
        self.model_label.setVisible(bool(model))
        self._fit_height()

    def _clear_answer(self) -> None:
        self._request_id += 1  # Antworten auf frühere Anfragen werden damit ignoriert
        self._answer_text = ""
        self.answer.clear()
        self.answer.hide()
        self.model_label.hide()
        self._fit_height()

    def _show_answer(self, text: str) -> None:
        self.answer.setText(text)
        self.answer.show()
        self._fit_height()

    def _ask(self, prompt: str) -> None:
        if self.ctx.config.get("nova.from_search") and self.ctx.nova.installed:
            self.dismiss()
            self.ctx.nova.ask(prompt)
            return
        key = self.ctx.config.api_key()
        if not key:
            self._show_answer("Kein Gemini API-Key hinterlegt. Trage ihn unter Einstellungen → Setup ein.")
            return
        self._clear_answer()
        self._show_answer("Denke nach …")
        worker = GeminiWorker(self._request_id, key, self.ctx.config.models("search"), prompt, self)
        worker.chunk.connect(self._on_chunk)
        worker.failed.connect(self._on_failed)
        worker.model_used.connect(lambda rid, model: rid == self._request_id and self._show_model(model))
        worker.finished.connect(lambda w=worker: (self._workers.discard(w), w.deleteLater()))
        self._workers.add(worker)
        worker.start()

    def _on_chunk(self, request_id: int, text: str) -> None:
        if request_id == self._request_id:
            self._answer_text += text
            self._show_answer(self._answer_text)

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id == self._request_id:
            self._show_answer(message)

    def shutdown(self) -> None:
        for worker in list(self._workers):
            worker.wait(1500)
