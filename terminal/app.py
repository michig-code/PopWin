"""PopWin Terminal: Tabs, geteilte Ansicht, alle Shells des PCs und auf Wunsch ein KI-Vorschlag für den Befehl."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from core.ai_actions import is_risky  # noqa: E402
from core.gemini_client import GeminiWorker  # noqa: E402
from terminal.widget import TerminalWidget, profiles  # noqa: E402
from ui.app_shell import run_app  # noqa: E402
from ui.base import G, IconButton, themed_menu  # noqa: E402
from ui.window_frame import NativeWindow  # noqa: E402

COMMAND_PROMPT = ('Schreibe genau einen {shell}-Befehl für folgende Aufgabe. Antworte nur mit dem Befehl, '
                  'ohne Erklärung und ohne Codeblock. Aufgabe: "{task}"')


def clean_command(answer: str) -> str:
    """Die KI liefert Befehle gern im Codeblock; in die Shell gehört nur eine Zeile."""
    text = re.sub(r"```\w*", "", answer).strip().strip("`").strip()
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


class TerminalTab(QSplitter):
    """Ein Tab; geteilt liegen mehrere Shells darin nebeneinander oder untereinander."""

    def __init__(self, window: "TerminalWindow", command: list[str], cwd: str):
        super().__init__(Qt.Orientation.Horizontal)
        self.window_ref = window
        self.cwd = cwd
        self.add_shell(command)

    def add_shell(self, command: list[str]) -> TerminalWidget:
        shell = TerminalWidget(command, self.cwd)
        shell.closed.connect(lambda: self.window_ref.shell_ended(self, shell))
        shell.title_changed.connect(lambda title: self.window_ref.retitle(self, title))
        self.addWidget(shell)
        QTimer.singleShot(0, shell.start)
        shell.setFocus()
        return shell

    def shells(self) -> list[TerminalWidget]:
        return [self.widget(index) for index in range(self.count())]

    def active(self) -> TerminalWidget | None:
        return next((shell for shell in self.shells() if shell.hasFocus()), self.shells()[0] if self.count() else None)


class TerminalWindow(NativeWindow):
    def __init__(self, ctx, arguments: list[str] | None = None):
        super().__init__(ctx, "PopWin Terminal", "apps/terminal")
        self.resize(1040, 640)
        arguments = arguments or []
        self.start_folder = next((a for a in arguments if os.path.isdir(a)), os.path.expanduser("~"))
        self.profiles = profiles()
        self.pages: list[TerminalTab] = []
        self._requests = 0
        self._answer = ""
        self._workers: set = set()
        self.tabbar = QTabBar()
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.currentChanged.connect(self._show_tab)
        self.tabbar.tabMoved.connect(lambda old, new: self.pages.insert(new, self.pages.pop(old)))
        add = IconButton(G.NEW, "Neuer Tab (Strg + Umschalt + T)")
        add.clicked.connect(lambda: self.new_tab())
        choose = IconButton(G.CHEVRON_UP, "Shell wählen")
        choose.clicked.connect(self._profile_menu)
        split = IconButton("", "Teilen (Strg + Umschalt + D)")
        split.clicked.connect(self.split)
        ask = IconButton("", "Befehl von der KI vorschlagen lassen (Strg + K)")
        ask.clicked.connect(self.toggle_ask)
        self.titlebar.middle.addWidget(self.tabbar, 1)
        for button in (add, choose, split, ask):
            self.titlebar.middle.addWidget(button)

        box = QVBoxLayout(self.content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.stack = QStackedWidget()
        box.addWidget(self.stack, 1)
        self.ask_bar = QWidget()
        row = QHBoxLayout(self.ask_bar)
        row.setContentsMargins(8, 4, 8, 4)
        self.ask_input = QLineEdit(placeholderText="Was soll passieren? Enter: Vorschlag, der Befehl läuft erst nach deinem Enter")
        self.ask_input.returnPressed.connect(self.ask)
        self.ask_status = QLabel(objectName="Muted")
        row.addWidget(self.ask_input, 1)
        row.addWidget(self.ask_status)
        self.ask_bar.hide()
        box.addWidget(self.ask_bar)

        for sequence, handler in (
            ("Ctrl+Shift+T", lambda: self.new_tab()), ("Ctrl+Shift+W", lambda: self.close_tab(self.tabbar.currentIndex())),
            ("Ctrl+Shift+D", self.split), ("Ctrl+K", self.toggle_ask),
            ("Ctrl+Tab", lambda: self._step(1)), ("Ctrl+Shift+Tab", lambda: self._step(-1)),
            ("Ctrl+=", lambda: self._shell() and self._shell().zoom(1)),
            ("Ctrl+-", lambda: self._shell() and self._shell().zoom(-1)),
            ("Ctrl+Shift+L", lambda: self._shell() and self._shell().clear()),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self, activated=handler)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.new_tab()
        self.apply_theme()

    def _shell(self) -> TerminalWidget | None:
        page = self._page()
        return page.active() if page else None

    def _page(self) -> TerminalTab | None:
        index = self.tabbar.currentIndex()
        return self.pages[index] if 0 <= index < len(self.pages) else None

    def new_tab(self, name: str = "") -> None:
        name = name if name in self.profiles else next(iter(self.profiles))
        page = TerminalTab(self, self.profiles[name], self.start_folder)
        page.shell_name = name
        self.pages.append(page)
        self.stack.addWidget(page)
        index = self.tabbar.addTab(name)
        self.tabbar.setCurrentIndex(index)
        self._show_tab(index)

    def _show_tab(self, index: int) -> None:
        if 0 <= index < len(self.pages):
            self.stack.setCurrentWidget(self.pages[index])
            active = self.pages[index].active()
            if active:
                active.setFocus()

    def _step(self, step: int) -> None:
        if self.tabbar.count():
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + step) % self.tabbar.count())

    def mouse_step(self, step: int) -> None:
        self._step(step)

    def _profile_menu(self) -> None:
        menu: QMenu = themed_menu(self.ctx, self)
        for name in self.profiles:
            menu.addAction(name, lambda _=False, n=name: self.new_tab(n))
        button = self.sender()
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def split(self) -> None:
        page = self._page()
        if page:
            page.add_shell(self.profiles[page.shell_name])

    def retitle(self, page: TerminalTab, title: str) -> None:
        if page in self.pages:
            short = os.path.basename(title.rstrip("\\/")) or title
            self.tabbar.setTabText(self.pages.index(page), short[:28])
            self.tabbar.setTabToolTip(self.pages.index(page), title)

    def shell_ended(self, page: TerminalTab, shell: TerminalWidget) -> None:
        """exit in der Shell schließt sie wie in Windows Terminal."""
        if page not in self.pages:
            return
        shell.setParent(None)
        shell.deleteLater()
        if not page.count():
            self._remove(page)

    def close_tab(self, index: int) -> None:
        if 0 <= index < len(self.pages):
            page = self.pages[index]
            for shell in page.shells():
                shell.stop()
            self._remove(page)

    def _remove(self, page: TerminalTab) -> None:
        index = self.pages.index(page)
        self.pages.pop(index)
        self.tabbar.removeTab(index)
        self.stack.removeWidget(page)
        page.deleteLater()
        if not self.pages:
            self.close()

    def closeEvent(self, event) -> None:
        for page in self.pages:
            for shell in page.shells():
                shell.stop()
        self.pages = []
        super().closeEvent(event)

    # --- Befehl von der KI ----------------------------------------------------------------
    def toggle_ask(self) -> None:
        self.ask_bar.setVisible(not self.ask_bar.isVisible())
        if self.ask_bar.isVisible():
            self.ask_input.setFocus()
            self.ask_input.selectAll()
        elif self._shell():
            self._shell().setFocus()

    def ask(self) -> None:
        task = self.ask_input.text().strip()
        key = self.ctx.config.api_key()
        if not task:
            return
        if not key:
            self.ask_status.setText("Kein Gemini API-Key hinterlegt")
            return
        page = self._page()
        self._requests += 1
        self._answer = ""
        self.ask_status.setText("Die KI überlegt...")
        prompt = COMMAND_PROMPT.format(shell=page.shell_name if page else "PowerShell", task=task)
        worker = GeminiWorker(self._requests, key, self.ctx.config.models("search"), prompt, self)
        worker.chunk.connect(self._on_chunk)
        worker.done.connect(self._on_done)
        worker.failed.connect(lambda _rid, message: self.ask_status.setText(message))
        worker.finished.connect(lambda w=worker: (self._workers.discard(w), w.deleteLater()))
        self._workers.add(worker)
        worker.start()

    def _on_chunk(self, request: int, text: str) -> None:
        if request == self._requests:
            self._answer += text

    def _on_done(self, request: int) -> None:
        if request != self._requests:
            return
        command = clean_command(self._answer)
        shell = self._shell()
        if not command or shell is None:
            self.ask_status.setText("Keine Antwort")
            return
        # Nur eintippen, nie ausführen: Enter drückt immer der Mensch.
        shell.write(command)
        risky = is_risky({"kind": "powershell", "command": command})
        self.ask_status.setText("Achtung: Dieser Befehl kann Daten verändern." if risky else "Prüfen, dann Enter drücken.")
        shell.setFocus()


def main() -> int:
    return run_app("Terminal", "PopWin Terminal", lambda ctx, arguments: TerminalWindow(ctx, arguments))


if __name__ == "__main__":
    sys.exit(main())
