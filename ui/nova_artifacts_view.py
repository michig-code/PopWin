"""Novas Artefakte im Panel: Karten wie kleine Sternbilder, ein Klick öffnet Checkliste, Notizen, Verlauf und Code.

Und der Hinweis, wenn eine Überwachung etwas Neues findet. Alles in Novas Farben.
"""
from __future__ import annotations

import math
import zlib
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from core import i18n, nova_artifacts, win_api
from core.notifications import format_age
from core.nova_tools import tool_problem
from ui.base import G, IconButton
from ui.notification_panel import plain_label
from ui.nova_visuals import NOVA_COLORS, fade, paint_orb

BACK_GLYPH = ""  # Pfeil nach links aus der Windows-Symbolschrift
KIND_GLYPHS = {"aufgabe": G.LIST, "notiz": G.NOTE, "ueberwachung": G.CLOCK, "werkzeug": G.TERMINAL}
CARD_STARS = 9
NOTICE_MS = 9000
NOTICE_SIZE = (360, 92)
NOTICE_GAP = 14


def age_text(stamp: str, now: datetime | None = None) -> str:
    if not stamp:
        return "noch nie geprüft"
    try:
        return format_age(datetime.fromisoformat(stamp).astimezone(), (now or datetime.now()).astimezone())
    except ValueError:
        return stamp


def card_facts(artifact: dict) -> list[str]:
    """Die kurze Zeile unter dem Titel."""
    facts = [nova_artifacts.KINDS.get(artifact["kind"], artifact["kind"])]
    done, total = nova_artifacts.progress(artifact)
    if total:
        facts.append(f"{done} von {total}")
    if artifact.get("state") == "pausiert":
        facts.append("pausiert")
    elif artifact.get("state") == "erledigt":
        facts.append("erledigt")
    if artifact["kind"] == "ueberwachung":
        facts.append(f"zuletzt: {age_text(artifact.get('last_check', ''))}")
    elif artifact.get("due"):
        facts.append(f"fällig {artifact['due']}")
    return [i18n.translate(fact) for fact in facts]  # einzeln, zusammengesetzt fände die Übersetzung sie nicht mehr


def paint_galaxy(painter: QPainter, rect: QRectF, colors: dict, seed: int, glow: float = 1.0) -> None:
    """Dunkler Grund, ein Hauch Nebel in Novas Farben, ein paar feste Sterne und ein Rand, der von Farbe zu Farbe läuft."""
    shape = QPainterPath()
    shape.addRoundedRect(rect, 12, 12)
    painter.fillPath(shape, QColor(12, 13, 30, 215))
    nebula = QLinearGradient(rect.topLeft(), rect.bottomRight())
    nebula.setColorAt(0, fade(colors["accent"], 0.20 * glow))
    nebula.setColorAt(0.6, fade(colors["b2"], 0.06 * glow))
    nebula.setColorAt(1, fade(colors["b3"], 0.16 * glow))
    painter.fillPath(shape, nebula)
    painter.setPen(Qt.PenStyle.NoPen)
    for index in range(CARD_STARS):
        value = zlib.crc32(f"{seed}:{index}".encode())
        x = rect.left() + 8 + (value % 1000) / 1000 * (rect.width() - 16)
        y = rect.top() + 6 + (value // 1000 % 1000) / 1000 * (rect.height() - 12)
        painter.setBrush(QColor(255, 255, 255, 40 + value % 90))
        painter.drawEllipse(QPointF(x, y), 0.6 + (value % 3) * 0.35, 0.6 + (value % 3) * 0.35)
    border = QLinearGradient(rect.topLeft(), rect.topRight())
    border.setColorAt(0, fade(colors["accent"], 0.75 * glow))
    border.setColorAt(0.5, fade(colors["b2"], 0.25))
    border.setColorAt(1, fade(colors["b3"], 0.55 * glow))
    painter.setPen(QPen(border, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shape)


class OrbitProgress(QWidget):
    """Fortschritt als Umlaufbahn: Der Bogen wächst mit jedem erledigten Schritt, ein Mond sitzt an seiner Spitze."""
    SIZE = 40

    def __init__(self, colors: dict, glyph: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.colors = colors
        self.glyph = glyph
        self.done, self.total = 0, 0

    def set_progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = QPointF(self.SIZE / 2, self.SIZE / 2)
        radius = self.SIZE / 2 - 4
        painter.setPen(QPen(QColor(255, 255, 255, 38), 2.5))
        painter.drawEllipse(middle, radius, radius)
        share = self.done / self.total if self.total else 0.0
        if share:
            arc = QLinearGradient(QPointF(0, 0), QPointF(self.SIZE, self.SIZE))
            arc.setColorAt(0, QColor(self.colors["b2"]))
            arc.setColorAt(1, QColor(self.colors["accent"]))
            pen = QPen(arc, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            box = QRectF(middle.x() - radius, middle.y() - radius, 2 * radius, 2 * radius)
            painter.drawArc(box, 90 * 16, -round(share * 360 * 16))
            angle = math.radians(90 - share * 360)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.colors["b3"] if share < 1 else "#ffe9a0"))
            painter.drawEllipse(QPointF(middle.x() + radius * math.cos(angle), middle.y() - radius * math.sin(angle)), 3, 3)
        font = QFont("Segoe Fluent Icons")
        font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        font.setPixelSize(14)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(QRectF(0, 0, self.SIZE, self.SIZE), Qt.AlignmentFlag.AlignCenter, self.glyph)
        painter.end()


class ArtifactCard(QWidget):
    opened = Signal(str)

    def __init__(self, artifact: dict, colors: dict, busy: bool):
        super().__init__(objectName="NovaArtifactCard")
        self.artifact_id = artifact["id"]
        self.colors = colors
        self.seed = zlib.crc32(artifact["id"].encode())
        self.hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        self.orbit = OrbitProgress(colors, KIND_GLYPHS.get(artifact["kind"], G.NOTE))
        self.orbit.set_progress(*nova_artifacts.progress(artifact))
        row.addWidget(self.orbit, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = plain_label(artifact["title"], "NovaArtifactTitle")
        text.addWidget(title)
        facts = " · ".join(card_facts(artifact) + ([i18n.translate("prüft gerade …")] if busy else []))
        text.addWidget(plain_label(facts, "NovaArtifactFacts"))
        row.addLayout(text, 1)
        unseen = artifact.get("unseen", 0)
        if unseen:
            badge = QLabel(str(unseen), objectName="NovaArtifactBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(22, 22)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        self.setToolTip(artifact["title"])

    def enterEvent(self, event) -> None:
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit(self.artifact_id)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_galaxy(painter, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.colors, self.seed, 1.5 if self.hovered else 1.0)
        painter.end()


class ArtifactDetail(QWidget):
    """Eine Seite pro Artefakt. Knöpfe, die etwas Endgültiges tun, wollen einen zweiten Klick."""
    back = Signal()

    def __init__(self, view: "ArtifactsView", artifact: dict):
        super().__init__()
        self.view = view
        self.nova = view.nova
        self.artifact = artifact
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(8)
        head = QHBoxLayout()
        back = IconButton(BACK_GLYPH, "Zurück")
        back.clicked.connect(self.back)
        head.addWidget(back)
        head.addWidget(plain_label(artifact["title"], "NovaArtifactHeading"), 1)
        box.addLayout(head)
        box.addWidget(plain_label(" · ".join(card_facts(artifact)), "NovaArtifactFacts"))  # Teile schon übersetzt
        if artifact.get("schedule_hours"):
            box.addWidget(plain_label(f"Prüft alle {artifact['schedule_hours']} Std., nächste Prüfung "
                                      f"{artifact.get('next_check', '')[:16].replace('T', ' ')}", "Muted"))
        box.addLayout(self._buttons())
        if artifact.get("steps"):
            box.addWidget(self._section("Checkliste"))
            for number, step in enumerate(artifact["steps"]):
                tick = QCheckBox(step["text"], objectName="NovaArtifactStep")
                tick.setChecked(step["done"])
                tick.toggled.connect(lambda on, number=number: self._tick(number, on))
                box.addWidget(tick)
        if artifact.get("notes"):
            box.addWidget(self._section("Notizen"))
            box.addWidget(plain_label(artifact["notes"], ""))
        if artifact["kind"] == "werkzeug":
            box.addWidget(self._section("Code" + (" · nur lesen" if artifact.get("read_only") else " · darf ändern")))
            code = QPlainTextEdit(objectName="NovaArtifactCode")
            code.setReadOnly(True)
            code.setPlainText(artifact.get("code", ""))
            code.setFixedHeight(min(320, 24 + 16 * max(3, artifact.get("code", "").count("\n") + 1)))
            box.addWidget(code)
        box.addWidget(self._section("Verlauf"))
        for entry in reversed(artifact.get("history", [])[-40:]):
            line = QLabel(objectName="NovaArtifactHistory")
            line.setTextFormat(Qt.TextFormat.PlainText)
            line.setWordWrap(True)
            line.setText(f"{entry['ts'][:16].replace('T', ' ')}  {entry['text']}")
            box.addWidget(line)
        box.addStretch(1)

    @staticmethod
    def _section(title: str) -> QLabel:
        return QLabel(title, objectName="NovaArtifactSection")

    def _buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        artifact = self.artifact
        busy = artifact["id"] in self.nova.watcher.running
        if artifact["kind"] in ("ueberwachung", "werkzeug"):
            self.check_button = QPushButton("Prüft …" if busy else "Jetzt prüfen", objectName="Primary")
            self.check_button.setEnabled(not busy)
            self.check_button.clicked.connect(self._check)
            row.addWidget(self.check_button)
        change = QPushButton("Bearbeiten", objectName="Secondary")
        change.clicked.connect(lambda: self.view.edit(artifact["id"]))
        row.addWidget(change)
        paused = artifact.get("state") == "pausiert"
        pause = QPushButton("Fortsetzen" if paused else "Pausieren", objectName="Secondary")
        pause.clicked.connect(self._pause)
        row.addWidget(pause)
        self.delete_button = QPushButton("Löschen", objectName="Secondary")
        self.delete_button.clicked.connect(self._delete)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        return row

    def _needs_ok(self) -> bool:
        """Ein Werkzeug läuft vom Knopf aus nur nach einem zweiten Klick, außer es liest nur und genau dieser Code ist frei."""
        artifact = self.artifact
        tool_id = artifact["id"] if artifact["kind"] == "werkzeug" else artifact.get("tool", "")
        return bool(tool_id) and not self.nova.toolbox.preapproved("artifact_run_tool", {"id": tool_id})

    def _check(self) -> None:
        button = self.check_button
        if self._needs_ok() and not button.property("armed"):
            button.setProperty("armed", True)
            button.setText("Code oben gelesen? Ja, ausführen" if self.artifact["kind"] == "werkzeug" else "Werkzeug-Code freigeben und prüfen")
            return
        button.setEnabled(False)
        button.setText("Prüft …")
        if self.artifact["kind"] == "werkzeug":
            self.nova.watcher.run_tool(self.artifact["id"])
        else:
            self.nova.watcher.check(self.artifact["id"], confirmed=bool(button.property("armed")))

    def _pause(self) -> None:
        artifact = self.artifact
        if artifact.get("state") == "pausiert":
            artifact["state"] = "aktiv" if artifact["kind"] == "ueberwachung" else "offen"
            nova_artifacts.add_history(artifact, "Fortgesetzt")
        else:
            artifact["state"] = "pausiert"
            nova_artifacts.add_history(artifact, "Pausiert")
        self.view.save(artifact)

    def _delete(self) -> None:
        if not self.delete_button.property("armed"):
            self.delete_button.setProperty("armed", True)
            self.delete_button.setText("Wirklich löschen?")
            return
        self.nova.artifacts.delete(self.artifact["id"])
        self.view.current = ""
        self.nova.artifacts_changed.emit()

    def _tick(self, number: int, on: bool) -> None:
        step = self.artifact["steps"][number]
        step["done"] = on
        nova_artifacts.add_history(self.artifact, f"{'Erledigt' if on else 'Wieder offen'} (von dir): {step['text']}")
        self.view.save(self.artifact)


class ArtifactEditor(QWidget):
    """Artefakte selbst anlegen oder ändern, ohne Nova zu fragen. Dieselben Felder, die Nova auch hat."""
    done = Signal(str)  # Kennung des gespeicherten Artefakts, leer bei Abbrechen

    def __init__(self, view: "ArtifactsView", artifact: dict | None = None):
        super().__init__()
        self.view = view
        self.nova = view.nova
        self.artifact = artifact
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(6)
        box.addWidget(QLabel("Artefakt bearbeiten" if artifact else "Neues Artefakt", objectName="NovaArtifactHeading"))
        self.title = QLineEdit(artifact["title"] if artifact else "")
        self.title.setPlaceholderText("Titel")
        box.addWidget(self.title)
        self.kind = QComboBox()
        for value, label in nova_artifacts.KINDS.items():
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(artifact["kind"] if artifact else "aufgabe")))
        self.kind.setEnabled(artifact is None)  # aus einer Notiz wird kein Werkzeug: Verlauf und Freigaben passten nicht mehr
        box.addWidget(self.kind)
        self.text = QPlainTextEdit((artifact.get("code") if artifact and artifact["kind"] == "werkzeug" else
                                    artifact.get("notes", "")) if artifact else "")
        self.text.setFixedHeight(110)
        box.addWidget(self.text)
        self.steps = QPlainTextEdit("\n".join(step["text"] for step in artifact.get("steps", [])) if artifact else "")
        self.steps.setPlaceholderText("Checkliste: ein Schritt pro Zeile")
        self.steps.setFixedHeight(80)
        box.addWidget(self.steps)
        self.due = QLineEdit(artifact.get("due", "") if artifact else "")
        self.due.setPlaceholderText("Fällig am (JJJJ-MM-TT), optional")
        box.addWidget(self.due)
        self.read_only = QCheckBox("Werkzeug liest nur (darf nach einmaliger Freigabe ohne Nachfrage laufen)")
        self.read_only.setChecked(bool(artifact and artifact.get("read_only")))
        box.addWidget(self.read_only)
        self.connection = QComboBox()
        self.connection.addItem("Keine Verbindung", "")
        for name in self.nova.connections.names():
            self.connection.addItem(f"Verbindung: {name}", name)
        self.connection.setCurrentIndex(max(0, self.connection.findData(artifact.get("connection", "") if artifact else "")))
        box.addWidget(self.connection)
        self.check = QComboBox()
        self.check.addItem("Keine Prüfung", "")
        for value, label in nova_artifacts.CHECKS.items():
            if value != "ovh_domains" or self.nova.toolbox.has_ovh():
                self.check.addItem(label, value)
        for item in self.nova.artifacts.all():
            if item["kind"] == "werkzeug":
                self.check.addItem(f"Werkzeug: {item['title']}", "tool:" + item["id"])
        current = (artifact.get("check") or ("tool:" + artifact["tool"] if artifact.get("tool") else "")) if artifact else ""
        self.check.setCurrentIndex(max(0, self.check.findData(current)))
        box.addWidget(self.check)
        self.hours = QSpinBox()
        self.hours.setRange(0, nova_artifacts.MAX_HOURS)
        self.hours.setSuffix(i18n.translate(" Std. zwischen den Prüfungen"))
        self.hours.setSpecialValueText(i18n.translate("Nur von Hand prüfen"))
        self.hours.setValue(artifact.get("schedule_hours", 0) if artifact else 6)
        box.addWidget(self.hours)
        self.problem = plain_label("", "Muted")
        box.addWidget(self.problem)
        row = QHBoxLayout()
        save = QPushButton("Speichern", objectName="Primary")
        save.clicked.connect(self._save)
        cancel = QPushButton("Abbrechen", objectName="Secondary")
        cancel.clicked.connect(lambda: self.done.emit(artifact["id"] if artifact else ""))
        row.addWidget(save)
        row.addWidget(cancel)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)
        self.kind.currentIndexChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        """Nur die Felder, die zur Art passen."""
        kind = self.kind.currentData()
        self.text.setPlaceholderText("Python-Code; was er mit print ausgibt, ist sein Ergebnis" if kind == "werkzeug" else "Notizen")
        self.steps.setVisible(kind in ("aufgabe", "ueberwachung"))
        self.due.setVisible(kind == "aufgabe")
        self.read_only.setVisible(kind == "werkzeug")
        self.connection.setVisible(kind == "werkzeug" and self.connection.count() > 1)
        self.check.setVisible(kind == "ueberwachung")
        self.hours.setVisible(kind == "ueberwachung")

    def _save(self) -> None:
        kind = self.kind.currentData()
        try:
            if self.artifact is None:
                artifact = nova_artifacts.new_artifact(self.title.text(), kind, self.text.toPlainText(),
                                                       self.steps.toPlainText().splitlines())
                artifact["history"][-1]["text"] += " (von dir)"
            else:
                artifact = self.artifact
                if not self.title.text().strip():
                    raise ValueError("Ein Artefakt braucht einen Titel.")
                artifact["title"] = self.title.text().strip()[:120]
                old = {step["text"]: step["done"] for step in artifact["steps"]}
                artifact["steps"] = [{"text": line.strip(), "done": old.get(line.strip(), False)}
                                     for line in self.steps.toPlainText().splitlines() if line.strip()]
                if kind == "werkzeug":
                    if self.text.toPlainText() != artifact.get("code", ""):
                        artifact["code"], artifact["approved"] = self.text.toPlainText(), ""
                else:
                    artifact["notes"] = self.text.toPlainText().strip()
                nova_artifacts.add_history(artifact, "Von dir bearbeitet")
            if kind == "werkzeug":
                if not artifact["code"].strip():
                    raise ValueError("Ein Werkzeug braucht Code.")
                problem = tool_problem(artifact["code"])
                if problem:
                    raise ValueError(problem)
                if bool(artifact.get("read_only")) != self.read_only.isChecked():
                    artifact["approved"] = ""
                artifact["read_only"] = self.read_only.isChecked()
                artifact["connection"] = self.connection.currentData() or ""
            if kind == "aufgabe":
                artifact["due"] = self.due.text().strip()
            if kind == "ueberwachung":
                choice = self.check.currentData() or ""
                artifact["check"], artifact["tool"] = ("", choice[5:]) if choice.startswith("tool:") else (choice, "")
                nova_artifacts.schedule(artifact, self.hours.value())
        except ValueError as exc:
            self.problem.setText(str(exc))
            return
        self.view.save(artifact)
        self.done.emit(artifact["id"])


class ConnectionForm(QWidget):
    """Hier tippt der Host die Keys einer Verbindung ein. Sie gehen direkt in den Windows-Anmeldespeicher, nie zu Nova."""
    saved = Signal(str)

    def __init__(self, connections, name: str, compact: bool = False):
        super().__init__(objectName="NovaCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.connections = connections
        self.name = name
        entry = connections.get(name)
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 10)
        box.setSpacing(6)
        box.addWidget(plain_label(f"Verbindung „{name}“", "MediaTitle"))
        if entry.get("note") and not compact:
            box.addWidget(plain_label(entry["note"], "Muted"))
        self.fields: dict[str, QLineEdit] = {}
        for field in entry["fields"]:
            line = QLineEdit()
            line.setEchoMode(QLineEdit.EchoMode.Password)
            line.setPlaceholderText(f"{field} · gesetzt, leer lassen zum Behalten" if connections.is_set(name, field) else field)
            line.returnPressed.connect(self._save)
            box.addWidget(line)
            self.fields[field] = line
        self.state = plain_label("Bleibt im Windows-Anmeldespeicher dieses PCs. Nova sieht die Keys nie.", "Muted")
        box.addWidget(self.state)
        row = QHBoxLayout()
        save = QPushButton("Speichern", objectName="Primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        if entry.get("help"):
            fetch = QPushButton("Keys holen", objectName="Secondary")
            fetch.setToolTip(entry["help"])
            fetch.clicked.connect(lambda: win_api.shell_open(entry["help"]))
            row.addWidget(fetch)
        if not compact:
            forget = QPushButton("Trennen", objectName="Secondary")
            forget.clicked.connect(self._forget)
            row.addWidget(forget)
        row.addStretch(1)
        box.addLayout(row)

    def _save(self) -> None:
        values = {field: line.text() for field, line in self.fields.items()}
        if not any(value.strip() for value in values.values()):
            self.state.setText("Trag zuerst etwas ein.")
            return
        if not self.connections.save(self.name, values):
            self.state.setText("Windows hat die Keys nicht gespeichert.")
            return
        for field, line in self.fields.items():
            line.clear()
            line.setPlaceholderText(f"{field} · gesetzt, leer lassen zum Behalten" if self.connections.is_set(self.name, field) else field)
        self.state.setText("Gespeichert." if self.connections.ready(self.name) else "Gespeichert. Es fehlen noch Felder.")
        self.saved.emit(self.name)

    def _forget(self) -> None:
        self.connections.forget(self.name)
        for field, line in self.fields.items():
            line.setPlaceholderText(field)
        self.state.setText("Getrennt: Die Keys sind aus dem Windows-Anmeldespeicher gelöscht.")


class ArtifactsView(QWidget):
    """Die Liste aller Artefakte und, nach einem Klick, eines davon im Detail."""
    count_changed = Signal(int)

    def __init__(self, nova, colors):
        super().__init__()
        self.nova = nova
        self.colors_source = colors  # () -> dict, Nova kann ihr Aussehen jederzeit wechseln
        self.current = ""
        self.editing = ""
        self.editor: ArtifactEditor | None = None
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(8)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.area.setWidget(self.content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.area)
        self.setStyleSheet(STYLE)
        self._pending = QTimer(self, singleShot=True, interval=80)  # viele Änderungen hintereinander: einmal neu aufbauen
        self._pending.timeout.connect(self.rebuild)
        nova.artifacts_changed.connect(self._pending.start)
        nova.watcher.busy_changed.connect(lambda *_args: self._pending.start())

    def colors(self) -> dict:
        return {**NOVA_COLORS, **self.colors_source()}

    def save(self, artifact: dict) -> None:
        self.nova.artifacts.save(artifact)
        self.nova.artifacts_changed.emit()

    def open_artifact(self, artifact_id: str) -> None:
        self.current = artifact_id
        try:
            artifact = self.nova.artifacts.get(artifact_id)
        except KeyError:
            self.current = ""
        else:
            if artifact.get("unseen"):  # gesehen ist gesehen
                artifact["unseen"] = 0
                self.nova.artifacts.save(artifact)
        self.rebuild()

    def _clear(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def edit(self, artifact_id: str = "") -> None:
        """Leer heißt: ein neues Artefakt."""
        self.editing = artifact_id or "neu"
        self.rebuild()

    def _edited(self, artifact_id: str) -> None:
        self.editing = ""
        self.open_artifact(artifact_id)

    def rebuild(self) -> None:
        self._clear()
        store = self.nova.artifacts
        items = store.all()
        self.count_changed.emit(sum(1 for item in items if nova_artifacts.is_open(item)))
        self.editor = None
        if self.editing:
            self.editor = ArtifactEditor(self, store.items.get(self.editing))
            self.editor.done.connect(self._edited)
            self.rows.addWidget(self.editor)
            return
        if self.current and self.current in store.items:
            detail = ArtifactDetail(self, store.items[self.current])
            detail.back.connect(lambda: self.open_artifact(""))
            self.rows.addWidget(detail)
            return
        self.current = ""
        head = QHBoxLayout()
        create = QPushButton("Neues Artefakt", objectName="Primary")
        create.clicked.connect(lambda: self.edit(""))
        head.addWidget(create)
        head.addStretch(1)
        self.rows.addLayout(head)
        if not items:
            empty = plain_label("Noch keine Artefakte. Leg selbst eins an oder gib Nova eine längere Aufgabe, "
                                "dann legt sie hier ihre Checkliste an.", "Muted")
            self.rows.addWidget(empty)
        colors = self.colors()
        for artifact in items:
            card = ArtifactCard(artifact, colors, artifact["id"] in self.nova.watcher.running)
            card.opened.connect(self.open_artifact)
            self.rows.addWidget(card)
        self.rows.addStretch(1)


STYLE = """
#NovaArtifactTitle { color: #ffffff; font-weight: 600; font-size: 13px; background: transparent; }
#NovaArtifactFacts { color: rgba(220, 225, 255, 0.72); font-size: 11px; background: transparent; }
#NovaArtifactHeading { font-size: 15px; font-weight: 600; }
#NovaArtifactSection { font-size: 11px; font-weight: 600; letter-spacing: 1px; color: rgba(160, 170, 255, 0.95); padding-top: 6px; }
#NovaArtifactHistory { font-size: 11px; color: rgba(200, 205, 230, 0.8); }
#NovaArtifactBadge { background: #ff3fa4; color: white; border-radius: 11px; font-size: 11px; font-weight: 700; }
#NovaArtifactCode { font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; background: rgba(8, 10, 24, 0.85);
                    color: #d8dcff; border: 1px solid rgba(139, 108, 255, 0.45); border-radius: 8px; }
"""


class NovaNotice(QWidget):
    """Der Hinweis unten am Bildschirm, wenn eine Überwachung etwas Neues findet. Ein Klick führt zum Artefakt."""
    clicked = Signal(str)

    def __init__(self, colors):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(*NOTICE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.colors_source = colors
        self.artifact_id = ""
        self.angle = 0.0
        self.waiting = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(58, 12, 14, 12)
        box.setSpacing(2)
        self.title = QLabel(objectName="NovaNoticeTitle")
        self.text = QLabel(objectName="NovaNoticeText")
        self.text.setWordWrap(True)
        for label in (self.title, self.text):
            label.setTextFormat(Qt.TextFormat.PlainText)
            box.addWidget(label)
        box.addStretch(1)
        self.setStyleSheet("#NovaNoticeTitle { color: white; font-weight: 600; font-size: 12px; }"
                           "#NovaNoticeText { color: rgba(225, 228, 255, 0.85); font-size: 12px; }")
        self.timer = QTimer(self, singleShot=True, interval=NOTICE_MS)
        self.timer.timeout.connect(self.hide)
        self.spin = QTimer(self, interval=50)
        self.spin.timeout.connect(self._turn)

    def show_notice(self, title: str, text: str, artifact_id: str) -> None:
        self.waiting = self.waiting + 1 if self.isVisible() else 0
        self.artifact_id = artifact_id
        self.title.setText(f"Nova · {title}")
        self.text.setText(text if not self.waiting else f"{text}\n+{self.waiting} weitere")
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - NOTICE_GAP, area.bottom() - self.height() - NOTICE_GAP)
        self.show()
        self.raise_()
        self.timer.start()
        self.spin.start()

    def hideEvent(self, event) -> None:
        self.spin.stop()
        self.waiting = 0
        super().hideEvent(event)

    def _turn(self) -> None:
        self.angle = (self.angle + 6) % 360
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self.hide()
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.artifact_id)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {**NOVA_COLORS, **self.colors_source()}
        paint_galaxy(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1), colors, 7, 1.6)
        painter.translate(30, self.height() / 2)
        paint_orb(painter, colors, self.angle)
        painter.end()
