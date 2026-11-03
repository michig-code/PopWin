"""Angeheftete Apps bearbeiten: mit Icons, Verschieben per Maus und Suche mit KI zum Hinzufügen."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from core import win_api, windows_pins
from core.app_index import App
from core.gemini_client import GeminiWorker
from ui.base import glyph_icon, icon_for_target
from ui.taskbar import merge_pins, pin_for_path
from ui.topbar import TOP_ITEMS, TOP_NEEDS, complete_order

MAX_RESULTS = 30


def match_ai_answer(answer: str, apps: list[App]) -> list[App]:
    """Die KI nennt Namen, jede Zeile einer; was nicht in der Liste steht, ist erfunden."""
    by_name = {app.name.lower(): app for app in apps}
    found = []
    for line in answer.splitlines():
        app = by_name.get(line.strip(" -*•\t").lower())
        if app and app not in found:
            found.append(app)
    return found


class WindowsPinEditor(QWidget):
    """Zeigt die Pins der echten Windows-Taskleiste. Lösen wirkt in Windows selbst."""
    UNPIN_WAIT_MS = 1200

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.items = QListWidget()
        self.items.setFixedHeight(180)
        self.items.setIconSize(QSize(24, 24))
        layout.addWidget(self.items)
        buttons = QHBoxLayout()
        unpin = QPushButton("Aus Windows lösen", objectName="Secondary")
        unpin.clicked.connect(self._unpin)
        add = QPushButton("In Windows anheften", objectName="Secondary")
        add.setToolTip("Öffnet den App-Ordner von Windows. Dort: Rechtsklick auf die App, dann „An Taskleiste anheften“.")
        add.clicked.connect(self._show_apps)
        reload = QPushButton("Aktualisieren", objectName="Secondary")
        reload.clicked.connect(self.load)
        for button in (unpin, add, reload):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel(objectName="Muted")
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.load()

    def load(self) -> None:
        self.items.clear()
        shortcuts, store_ids = windows_pins.pinned_paths()
        for pin in [pin_for_path(path) for path in shortcuts] + [windows_pins.store_pin(app) for app in store_ids]:
            item = QListWidgetItem(icon_for_target(pin["target"]), pin["name"])
            item.setData(Qt.ItemDataRole.UserRole, pin["target"])
            self.items.addItem(item)

    def _targets(self) -> list[str]:
        return [self.items.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.items.count())]

    def _show_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _show_apps(self) -> None:
        win_api.shell_open("shell:AppsFolder")
        self._show_status("Rechtsklick auf die App, dann „An Taskleiste anheften“, danach hier auf Aktualisieren.")

    def _unpin(self) -> None:
        item = self.items.currentItem()
        if item is None:
            return
        target, name = item.data(Qt.ItemDataRole.UserRole), item.text()
        answer = QMessageBox.question(self, "PopWin", f"{name} aus der Windows-Taskleiste lösen?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not windows_pins.unpin(target):
            self._show_status(f"Windows bietet das Lösen von {name} gerade nicht an.")
            return
        QTimer.singleShot(self.UNPIN_WAIT_MS, lambda: self._check_unpinned(target, name))

    def _check_unpinned(self, target: str, name: str) -> None:
        self.load()
        gone = target not in self._targets()
        self._show_status(f"{name} ist gelöst." if gone else f"Windows hat {name} nicht gelöst.")


class PinList(QListWidget):
    reordered = Signal()

    def startDrag(self, actions) -> None:
        super().startDrag(actions)
        self.reordered.emit()


class TopItemsEditor(PinList):
    """Die Symbole der Topbar: Haken zeigt oder versteckt, Ziehen ändert die Reihenfolge."""

    def __init__(self, ctx, key: str):
        super().__init__()
        self.ctx = ctx
        self.key = key
        state = ctx.config.monitor(key)
        color = "#FFFFFF" if ctx.theme.mode == "dark" else "#202020"
        self.setIconSize(QSize(18, 18))
        self.setFixedHeight(len(TOP_ITEMS) * 36 + 8)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for name in complete_order(state["top_order"]):
            field, label, glyph = TOP_ITEMS[name]
            item = QListWidgetItem(glyph_icon(glyph, 18, color), label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if state[field] else Qt.CheckState.Unchecked)
            self.addItem(item)
        self.itemChanged.connect(self._toggled)
        self.reordered.connect(self._store_order)
        ctx.status.updated.connect(self._state_changed)
        self.hide_missing()

    def _state_changed(self, _state) -> None:
        if isValid(self):  # die Einstellungsseite baut diese Liste öfter neu, die alte meldet sich sonst weiter
            self.hide_missing()

    def hide_missing(self) -> None:
        """Was dieser PC nicht hat, steht auch nicht zur Wahl."""
        for row in range(self.count()):
            item = self.item(row)
            need = TOP_NEEDS.get(item.data(Qt.ItemDataRole.UserRole))
            item.setHidden(bool(need) and not self.ctx.has_feature(need))
        self.setFixedHeight(sum(not self.item(row).isHidden() for row in range(self.count())) * 36 + 8)

    def _toggled(self, item: QListWidgetItem) -> None:
        field = TOP_ITEMS[item.data(Qt.ItemDataRole.UserRole)][0]
        checked = item.checkState() == Qt.CheckState.Checked
        if self.ctx.config.monitor(self.key)[field] != checked:
            self.ctx.config.set_monitor(self.key, field, checked)

    def _store_order(self) -> None:
        order = [self.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.count())]
        if order != complete_order(self.ctx.config.monitor(self.key)["top_order"]):
            self.ctx.config.set_monitor(self.key, "top_order", order)


class PinEditor(QWidget):
    def __init__(self, ctx, path: str, screen: str = ""):
        """screen wählt die eigene Liste eines Monitors statt der Liste unter path."""
        super().__init__()
        self.ctx = ctx
        self.path = path
        self.screen = screen
        self._answer = ""
        self._request_id = 0
        self._workers: set[GeminiWorker] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.items = PinList()
        self.items.setFixedHeight(180)
        self.items.setIconSize(QSize(24, 24))
        self.items.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.items.reordered.connect(self._store_order)
        layout.addWidget(self.items)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("Hinzufügen", objectName="Secondary")
        self.add_button.clicked.connect(self._toggle_search)
        remove = QPushButton("Entfernen", objectName="Secondary")
        remove.clicked.connect(self._remove)
        buttons.addWidget(self.add_button)
        buttons.addWidget(remove)
        layout.addLayout(buttons)

        self.search_area = QWidget()
        search = QVBoxLayout(self.search_area)
        search.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.search = QLineEdit(placeholderText="App oder Verknüpfung suchen, Enter fragt die KI")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._ask_ai)
        ask = QPushButton("KI fragen", objectName="Secondary")
        ask.clicked.connect(self._ask_ai)
        browse = QPushButton("Datei wählen", objectName="Secondary")
        browse.clicked.connect(self._browse)
        row.addWidget(self.search, 1)
        row.addWidget(ask)
        row.addWidget(browse)
        self.status = QLabel(objectName="Muted")
        self.status.setWordWrap(True)
        self.status.hide()
        self.results = QListWidget()
        self.results.setFixedHeight(200)
        self.results.setIconSize(QSize(24, 24))
        self.results.itemClicked.connect(lambda item: self._add(item.data(Qt.ItemDataRole.UserRole)))
        search.addLayout(row)
        search.addWidget(self.status)
        search.addWidget(self.results)
        self.search_area.hide()
        layout.addWidget(self.search_area)
        self._load()

    # --- Die Liste ----------------------------------------------------------
    def _read(self) -> list[dict]:
        config = self.ctx.config
        return list(config.get("taskbar.screen_pins").get(self.screen, []) if self.screen else config.get(self.path))

    def _write(self, pins: list[dict]) -> None:
        if self.screen:
            self.ctx.config.set_screen_pins(self.screen, pins)
        else:
            self.ctx.config.set(self.path, pins)

    def _load(self) -> None:
        self.items.clear()
        for pin in self._read():
            item = QListWidgetItem(icon_for_target(pin["target"]), pin["name"])
            item.setData(Qt.ItemDataRole.UserRole, pin)
            self.items.addItem(item)

    def _store_order(self) -> None:
        pins = [self.items.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.items.count())]
        if pins != self._read():
            self._write(pins)

    def _remove(self) -> None:
        row = self.items.currentRow()
        if row >= 0:
            pins = self._read()
            del pins[row]
            self._write(pins)
            self._load()

    def _add(self, app: App) -> None:
        self._write(merge_pins(self._read(), [{**pin_for_path(app.target), "name": app.name}]))
        self._load()
        self.items.scrollToBottom()
        self._show_status(f"{app.name} hinzugefügt.")

    # --- Suchen -------------------------------------------------------------
    def _toggle_search(self) -> None:
        opening = self.search_area.isHidden()
        self.search_area.setVisible(opening)
        self.add_button.setText("Suche schließen" if opening else "Hinzufügen")
        if opening:
            self._filter(self.search.text())
            self.search.setFocus()

    def _show_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _show_results(self, apps: list[App]) -> None:
        self.results.clear()
        for app in apps[:MAX_RESULTS]:
            item = QListWidgetItem(icon_for_target(app.target), app.name)
            item.setData(Qt.ItemDataRole.UserRole, app)
            self.results.addItem(item)

    def _filter(self, text: str) -> None:
        pool = self.ctx.apps.pickable()
        self._request_id += 1  # eine neue Eingabe überholt die laufende KI-Antwort
        self._show_status("")
        self._show_results(self.ctx.apps.search(text, MAX_RESULTS, pool) if text.strip() else pool)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "App oder Verknüpfung wählen", "",
                                              "Programme und Verknüpfungen (*.exe *.lnk);;Alle Dateien (*)")
        if path:
            self._add(App(pin_for_path(path)["name"], path))

    def _ask_ai(self) -> None:
        text = self.search.text().strip()
        key = self.ctx.config.api_key()
        if not text:
            return
        if not key:
            self._show_status("Kein Gemini API-Key hinterlegt. Trage ihn unter Setup ein.")
            return
        names = "\n".join(app.name for app in self.ctx.apps.pickable())
        prompt = (f'Jemand sucht auf dem PC eine App: "{text}". Das ist installiert:\n{names}\n\n'
                  "Nenne bis zu fünf passende Namen genau wie in der Liste, je Zeile einen, sonst nichts.")
        self._request_id += 1
        self._answer = ""
        self._show_status("Die KI sucht...")
        # Am Fenster statt am Editor: Der Editor wird beim Wechsel des Bildschirms ersetzt, der Thread läuft weiter.
        worker = GeminiWorker(self._request_id, key, self.ctx.config.models("search"), prompt, self.window())
        worker.chunk.connect(self._on_chunk)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda w=worker: (self._workers.discard(w), w.deleteLater()))
        self._workers.add(worker)
        worker.start()

    def _on_chunk(self, request_id: int, text: str) -> None:
        if request_id == self._request_id:
            self._answer += text

    def _on_done(self, request_id: int) -> None:
        if request_id != self._request_id:
            return
        found = match_ai_answer(self._answer, self.ctx.apps.pickable())
        self._show_status("Vorschläge der KI:" if found else "Die KI hat nichts Passendes gefunden.")
        if found:
            self._show_results(found)

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id == self._request_id:
            self._show_status(message)
