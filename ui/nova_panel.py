"""Der Chat mit Nova am Bildschirmrand. Er bleibt offen, solange man mit ihr arbeitet."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QTextOption
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from core import win_api
from core.monitor_manager import native_rect, screen_key
from core.nova_controller import is_key_error
from core.theme_manager import NOVA_CLASSICS
from ui.base import G, IconButton, PopupPanel, set_prop
from ui.notification_panel import EDGE_GAP, plain_label
from ui.nova_artifacts_view import ArtifactsView, ConnectionForm, NovaNotice
from ui.nova_visuals import NovaOrb, NovaSpawn, classic_backdrop
from ui.search_actions import VoiceInput

STAGES = {"verbindet": "verbindet …", "hört zu": "hört zu", "denkt nach": "denkt nach", "spricht": "spricht"}
TAB_STYLE = """
QPushButton#NovaTab {{ background: transparent; border: none; border-bottom: 2px solid transparent; padding: 4px 10px;
                       color: rgba(255, 255, 255, 0.6); font-weight: 600; }}
QPushButton#NovaTab:hover {{ color: rgba(255, 255, 255, 0.9); }}
QPushButton#NovaTab:checked {{ color: #ffffff; border-bottom: 2px solid {accent}; }}
"""

KEY_PAGE = "https://aistudio.google.com/apikey"
SLIDE_MS = 420
SPACE_MESSAGE = 0x8000 + 0x51  # WM_APP; Nova reagiert auf nichts, Windows verlangt nur eine Nummer
PLACE_SETTINGS = ("nova.side", "nova.screen", "nova.width", "nova.reserve_space", "nova.keep_open")
RENDER_MS = 60  # Beim Streamen nicht jedes Stück einzeln neu setzen
BOTTOM_TOLERANCE = 24
USER_WIDTH = 0.82
PANEL_PADDING = 48  # Abstände von Panel, Liste und Rollbalken
CARD_PADDING = 20
MAX_LINES = 6
FIELD_CHROME = 14  # Rand und Innenabstand des Eingabefelds, in allen Themes höchstens so viel
GREETING = "Na, was steht an?"


class ChatText(QTextBrowser):
    """Text, der so hoch wird wie sein Inhalt. Auswählbar, und lange Pfade brechen um, statt den Chat zu sprengen."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; }")
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    @staticmethod
    def _open_link(url) -> None:
        if url.scheme() in ("http", "https"):
            win_api.shell_open(url.toString())

    def _fit(self) -> None:
        self.setFixedHeight(math.ceil(self.document().size().height()))

    def wheelEvent(self, event) -> None:
        event.ignore()  # gehört dem Chat, nicht dem einzelnen Text

    def ideal_width(self, limit: int) -> int:
        lines = self.toPlainText().splitlines() or [""]
        return min(limit, max(self.fontMetrics().horizontalAdvance(line) for line in lines) + 6)


class Composer(QPlainTextEdit):
    """Eingabe: Enter schickt ab, Umschalt+Enter macht eine neue Zeile. Sie wächst mit dem Text."""
    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Schreib Nova")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)  # sonst fehlen unten vier Pixel und der Text wird abgeschnitten
        self.document().documentLayout().documentSizeChanged.connect(lambda _size: self._fit())
        self._fit()

    def _fit(self) -> None:
        count = round(self.document().size().height())
        lines = max(1, min(MAX_LINES, count))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if count > MAX_LINES
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(lines * self.fontMetrics().lineSpacing() + FIELD_CHROME)

    def keyPressEvent(self, event) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)


class ApprovalCard(QWidget):
    """Nova fragt, bevor sie handelt. Zu lesen ist der echte Befehl, nicht ihre Zusammenfassung."""

    def __init__(self, panel: "NovaPanel", number: int, title: str, detail: str, dangerous: bool, for_chat: bool):
        super().__init__(objectName="NovaCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.panel = panel
        self.number = number
        self.title = title
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 10)
        box.setSpacing(6)
        box.addWidget(plain_label(f"Nova möchte: {title}", "MediaTitle"))
        if dangerous:
            box.addWidget(plain_label("Das kann Daten verändern oder löschen.", "Muted"))
        text = ChatText()
        text.setPlainText(detail)
        box.addWidget(text)
        row = QHBoxLayout()
        row.setSpacing(6)
        allow = QPushButton("Erlauben", objectName="Primary")
        allow.clicked.connect(lambda: self.answer(True))
        row.addWidget(allow)
        if for_chat:
            always = QPushButton("In diesem Chat immer", objectName="Secondary")
            always.clicked.connect(lambda: self.answer(True, True))
            row.addWidget(always)
        deny = QPushButton("Ablehnen", objectName="Secondary")
        deny.clicked.connect(lambda: self.answer(False))
        row.addWidget(deny)
        row.addStretch(1)
        box.addLayout(row)

    def answer(self, allowed: bool, for_chat: bool = False) -> None:
        self.panel.ctx.nova.resolve(self.number, allowed, for_chat)
        self.panel.settle_card(self, "Erlaubt" if allowed else "Abgelehnt")


class NovaPanel(PopupPanel):
    def __init__(self, ctx):
        # Nur der Inhalt rutscht, das Fenster steht schon am Ziel: Es würde sonst über den Nachbarmonitor ziehen.
        super().__init__(ctx, shift=self._slide)
        self.animator.length = SLIDE_MS
        self.nova = ctx.nova
        self.reply: ChatText | None = None
        self.reply_text = ""
        self.greeting: QLabel | None = None
        self.cards: list[ApprovalCard] = []
        self._loaded = False
        self._stick = True
        self._built_width = 0
        self._built_look = ""
        self._backdrop = None
        self._reserved = False
        self._render_timer = QTimer(self, singleShot=True, interval=RENDER_MS)
        self._render_timer.timeout.connect(self._render)
        self.voice = VoiceInput(ctx, self.nova.api_key, self._listen_options)
        self._voice_timer = QTimer(self, interval=150)
        self._voice_timer.timeout.connect(self.voice.check_limit)
        self.heard: ChatText | None = None  # was Nova im Gespräch gerade vom Host hört
        self.heard_text = ""
        self.notice = NovaNotice(self.colors)
        self.notice.clicked.connect(self.show_artifact)

        self.body.addWidget(self._header())
        self.body.addWidget(self._tab_row())
        self.spawn = NovaSpawn(self.panel, self.orb)
        self.panel.installEventFilter(self)
        self.content = QWidget()
        self.list = QVBoxLayout(self.content)
        self.list.setContentsMargins(0, 0, 8, 0)
        self.list.setSpacing(10)
        self.list.addStretch(1)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.area.setWidget(self.content)
        bar = self.area.verticalScrollBar()
        bar.rangeChanged.connect(self._follow)
        bar.valueChanged.connect(self._track)
        self.artifacts = ArtifactsView(self.nova, self.colors)
        self.artifacts.count_changed.connect(self._count_artifacts)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.area)
        self.pages.addWidget(self.artifacts)
        self.body.addWidget(self.pages, 1)
        self.key_card = self._key_card()
        self.body.addWidget(self.key_card)
        self.body.addLayout(self._input_row())

        self.nova.text.connect(self._on_text)
        self.nova.tool.connect(self._on_tool)
        self.nova.approval.connect(self._on_approval)
        self.nova.failed.connect(self._on_failed)
        self.nova.user_message.connect(self._on_user)
        self.nova.busy_changed.connect(self._on_busy)
        self.nova.cleared.connect(self._on_cleared)
        self.voice.state_changed.connect(self._on_recording)
        self.voice.text_ready.connect(self._dictated)
        self.voice.partial.connect(self._on_partial)
        self.voice.failed.connect(self._add_note)
        self.nova.heard.connect(self._on_heard)
        self.nova.stage.connect(self._on_stage)
        self.nova.listening.connect(self._on_listening)
        self.nova.notice.connect(self._on_notice)
        self.nova.connection_wanted.connect(self._ask_connection)
        ctx.config.changed.connect(self._on_config)

    def _header(self) -> QWidget:
        head = QWidget(objectName="NovaHead")
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.head_box = header = QHBoxLayout(head)
        header.setContentsMargins(0, 0, 0, 0)
        self.orb = NovaOrb()
        header.addWidget(self.orb)
        self.title = QLabel("Nova", objectName="PanelTitle")
        header.addWidget(self.title)
        self.status = QLabel(objectName="Muted")
        header.addWidget(self.status)
        header.addStretch(1)
        fresh = IconButton(G.ADD, "Neuer Chat")
        fresh.clicked.connect(self.nova.new_chat)
        settings = IconButton(G.SETTINGS, "Einstellungen")
        settings.clicked.connect(lambda: self.ctx.open_settings_at("Nova"))
        close = IconButton(G.CLOSE, "Schließen")
        close.clicked.connect(self.dismiss)
        for button in (fresh, settings, close):
            header.addWidget(button)
        return head

    def _tab_row(self) -> QWidget:
        """Chat und Artefakte: zwei Seiten im selben Panel."""
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.tabs = []
        for index, label in enumerate(("Chat", "Artefakte")):
            tab = QPushButton(label, objectName="NovaTab")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _on, index=index: self.show_page(index))
            row.addWidget(tab)
            self.tabs.append(tab)
        row.addStretch(1)
        self.tab_row = row_widget
        return row_widget

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for number, tab in enumerate(self.tabs):
            tab.setChecked(number == index)
        if index == 1:
            self.artifacts.rebuild()

    def show_artifact(self, artifact_id: str) -> None:
        """Vom Hinweis aus: Nova öffnen und gleich das Artefakt zeigen, das etwas gefunden hat."""
        self.reveal()
        self.show_page(1)
        self.artifacts.open_artifact(artifact_id)

    def _count_artifacts(self, count: int) -> None:
        self.tabs[1].setText(f"Artefakte · {count}" if count else "Artefakte")

    def _ask_connection(self, name: str) -> None:
        """Nova braucht Keys: Die Felder erscheinen im Chat, eingetippt wird hier und nicht bei ihr."""
        self._end_reply()
        form = ConnectionForm(self.nova.connections, name, compact=True)
        form.saved.connect(lambda saved: self.nova.connections.ready(saved) and self._add_note(f"Verbindung {saved} ist eingerichtet."))
        self._add(form)

    def _on_notice(self, title: str, text: str, artifact_id: str) -> None:
        if self.ctx.config.get("nova.artifacts_notify"):
            self.notice.show_notice(title, text, artifact_id)

    def _key_card(self) -> QWidget:
        card = QWidget(objectName="NovaCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 8, 10, 10)
        box.setSpacing(6)
        box.addWidget(plain_label("Mir fehlt noch ein Gemini API-Key", "MediaTitle"))
        self.key_hint = plain_label("Er ist kostenlos und bleibt auf diesem PC im Windows-Anmeldespeicher.", "Muted")
        box.addWidget(self.key_hint)
        self.key_field = QLineEdit()
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_field.setPlaceholderText("API-Key einfügen")
        self.key_field.returnPressed.connect(self._save_key)
        box.addWidget(self.key_field)
        row = QHBoxLayout()
        save = QPushButton("Speichern", objectName="Primary")
        save.clicked.connect(self._save_key)
        fetch = QPushButton("Key holen", objectName="Secondary")
        fetch.clicked.connect(lambda: win_api.shell_open(KEY_PAGE))
        row.addWidget(save)
        row.addWidget(fetch)
        row.addStretch(1)
        box.addLayout(row)
        card.hide()
        return card

    def _input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.composer = Composer()
        self.composer.submitted.connect(self._send)
        self.mic = IconButton(G.MIC, "Sprechen")
        self.mic.setProperty("kind", "mic")
        self.mic.clicked.connect(self._mic_clicked)
        self.send_button = IconButton(G.SEND, "Senden")
        self.send_button.setProperty("kind", "send")
        self.send_button.clicked.connect(self._send_or_stop)
        row.addWidget(self.composer, 1)
        row.addWidget(self.mic, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        return row

    # --- Aussehen --------------------------------------------------------------
    def look(self) -> str:
        """Der Name der Classic-Farben, leer heißt: Nova folgt dem PopWin-Theme."""
        look = self.ctx.config.get("nova.look")
        return look if look in NOVA_CLASSICS else ""

    def colors(self) -> dict:
        look = self.look()
        if look:
            return NOVA_CLASSICS[look]
        meta = self.ctx.theme.meta()
        light = "_light" if self.ctx.theme.mode == "light" else ""
        return {"accent": meta[f"ind{light}"], "b2": meta[f"ind_active{light}"], "b3": meta[f"ind{light}"]}

    def apply_theme(self) -> None:
        super().apply_theme()
        look = self.look()
        if look:
            self.setStyleSheet(self.ctx.theme.nova_classic(look))
            win_api.set_blur(int(self.winId()), "none")
            win_api.set_rounded(int(self.winId()), False)
        self.title.setText("N O V A" if look else "Nova")
        self.head_box.setContentsMargins(*((10, 4, 8, 4) if look else (0, 0, 0, 0)))
        self.orb.colors = self.spawn.colors = self.colors()
        self._backdrop = classic_backdrop(self.size(), self.devicePixelRatio(), self.colors()) if look else None
        self.tab_row.setStyleSheet(TAB_STYLE.format(accent=self.colors()["accent"]))
        self.tab_row.setVisible(bool(self.ctx.config.get("nova.artifacts_tab")))
        if not self.tab_row.isVisible():
            self.show_page(0)
        self.panel.update()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.panel and event.type() == QEvent.Type.Paint and self._backdrop is not None:
            QPainter(self.panel).drawPixmap(0, 0, self._backdrop)  # liegt auf dem Panel, damit er mitrutscht
        return super().eventFilter(watched, event)

    def _on_config(self, section: str) -> None:
        if section.startswith("nova") and self.isVisible():
            if section in PLACE_SETTINGS:
                self._place()
            self.apply_theme()
            if self._built_look != self.look():
                self._rebuild()

    def _slide(self, offset: int) -> None:
        self.panel.move(offset, 0)

    # --- Öffnen ----------------------------------------------------------------
    def event(self, event) -> bool:
        if event.type() == event.Type.WindowDeactivate and self.ctx.config.get("nova.keep_open"):
            return super(PopupPanel, self).event(event)  # Nova soll zusehen können, während man woanders arbeitet
        return super().event(event)

    def toggle(self) -> None:
        if self.isVisible() and not self.animator.closing and self.isActiveWindow():
            self.dismiss()
        else:
            self.reveal()

    def reveal(self) -> None:
        if not self.isVisible() or self.animator.closing:
            self._open()
        win_api.force_foreground(int(self.winId()))
        self.activateWindow()
        self.composer.setFocus()

    def _open(self) -> None:
        settings = self.ctx.config.get("nova")
        if not settings["keep_open"]:
            self._take_over()
        left = self._place()
        self.apply_theme()
        if not self._loaded or self._built_width != settings["width"] or self._built_look != self.look():
            self._rebuild()
        self.key_card.setVisible(not self.nova.api_key())
        self.show()
        self.spawn.setGeometry(self.rect())
        self.spawn.raise_()
        self._arrive(-self.width() - EDGE_GAP if left else self.width() + EDGE_GAP)

    def target_screen(self):
        choice = self.ctx.config.get("nova.screen")
        if choice == "primary":
            return QGuiApplication.primaryScreen()
        chosen = next((screen for screen in QGuiApplication.screens() if screen_key(screen) == choice), None)
        return chosen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _place(self) -> bool:
        """Setzt Nova an ihren Rand und liefert, ob das links ist. Erst danach schafft Windows ihr dort Platz."""
        settings = self.ctx.config.get("nova")
        self._release_space()
        screen = self.target_screen()
        area = screen.availableGeometry()
        self.setFixedSize(settings["width"], area.height() - 2 * EDGE_GAP)
        left = settings["side"] == "left"
        x = area.left() + EDGE_GAP if left else area.right() - settings["width"] - EDGE_GAP + 1
        self.move(x, area.top() + EDGE_GAP)
        # Über einem Spiel oder Video keinen Platz reservieren: Windows passt sonst maximierte Vollbild-Fenster
        # an den kleineren Arbeitsbereich an, und das Spiel rutscht unter die Topbar.
        if settings["reserve_space"] and settings["keep_open"] and not win_api.fullscreen_on(native_rect(screen)):
            hwnd = int(self.winId())
            win_api.appbar_register(hwnd, SPACE_MESSAGE)
            self._reserved = True
            thickness = round((settings["width"] + 2 * EDGE_GAP) * screen.devicePixelRatio())
            win_api.appbar_set_pos(hwnd, "left" if left else "right", native_rect(screen), thickness)
        return left

    def _release_space(self) -> None:
        if self._reserved:
            self._reserved = False
            win_api.appbar_remove(int(self.winId()))

    def hideEvent(self, event) -> None:
        self._release_space()  # Fenster dürfen wieder bis zum Rand
        super().hideEvent(event)

    def _arrive(self, distance: int) -> None:
        self.animator.enter(1.0, slide=distance)
        if self.animator.duration():
            self.spawn.start()

    def _rebuild(self) -> None:
        """Der gespeicherte Chat entsteht erst beim Öffnen; PopWin startet dadurch nicht langsamer."""
        self._clear_rows()
        self._loaded = True
        self._built_width = self.ctx.config.get("nova.width")
        self._built_look = self.look()
        for message in self.nova.log.messages:
            if message["role"] == "user":
                self._add_user(message["text"])
            else:
                self._add_nova(message["text"])
        if not self.nova.log.messages:
            self.greeting = plain_label(GREETING, "Muted")
            self._add(self.greeting)

    def _clear_rows(self) -> None:
        self.reply = self.greeting = None
        self.cards = []
        while self.list.count() > 1:
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    # --- Inhalt ----------------------------------------------------------------
    def _add(self, widget: QWidget) -> None:
        self.list.insertWidget(self.list.count() - 1, widget)

    def _add_note(self, text: str) -> None:
        self._add(plain_label(text, "Muted"))

    def _add_nova(self, markdown: str) -> ChatText:
        text = ChatText()
        text.setMarkdown(markdown)
        bubble = QWidget(objectName="NovaReply")  # nur Classic gibt ihr einen Hintergrund
        bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        box = QVBoxLayout(bubble)
        box.setContentsMargins(*((12, 8, 12, 8) if self.look() else (0, 0, 0, 0)))
        box.addWidget(text)
        self._add(bubble)
        return text

    def _add_user(self, message: str) -> None:
        limit = round((self._built_width - PANEL_PADDING) * USER_WIDTH)
        text = ChatText()
        text.setPlainText(message)
        card = QWidget(objectName="NovaCard")
        card.setProperty("who", "user")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 6, 10, 6)
        box.addWidget(text)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addStretch(1)
        line.addWidget(card)
        self._add(row)
        text.ensurePolished()  # erst im Panel hat der Text seine echte Schrift, davor wäre die Blase zu schmal
        card.setFixedWidth(text.ideal_width(limit - CARD_PADDING) + CARD_PADDING)
        self._last_user_text = text

    def _follow(self, _low: int, high: int) -> None:
        if self._stick:
            self.area.verticalScrollBar().setValue(high)

    def _track(self, value: int) -> None:
        self._stick = value >= self.area.verticalScrollBar().maximum() - BOTTOM_TOLERANCE

    def _on_user(self, text: str) -> None:
        self._stick = True
        if self.greeting is not None:
            self.greeting.hide()
            self.greeting.deleteLater()
            self.greeting = None
        self._add_user(text)

    def _on_text(self, chunk: str) -> None:
        if self.reply is None:
            self.reply_text = ""
            self.reply = self._add_nova("")
        self.reply_text += chunk
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        if self.reply is not None:
            self.reply.setMarkdown(self.reply_text)

    def _end_reply(self) -> None:
        self._render_timer.stop()
        self._render()
        self.reply = None

    def _on_tool(self, label: str) -> None:
        self._end_reply()
        self._add_note(label)

    def _on_approval(self, number: int, title: str, detail: str, dangerous: bool, for_chat: bool) -> None:
        self._end_reply()
        card = ApprovalCard(self, number, title, detail, dangerous, for_chat)
        self.cards.append(card)
        self._add(card)
        self.reveal()

    def settle_card(self, card: ApprovalCard, verdict: str) -> None:
        """Die Entscheidung bleibt als kurze Zeile im Chat stehen."""
        self.cards.remove(card)
        self.list.replaceWidget(card, plain_label(f"{verdict}: {card.title}", "Muted"))
        card.hide()
        card.deleteLater()

    def _on_failed(self, message: str) -> None:
        self._end_reply()
        self._add_note(message)
        if is_key_error(message):
            self.key_hint.setText("Dieser Key funktioniert nicht. Trag bitte einen gültigen ein.")
            self.key_card.show()

    def _on_busy(self, busy: bool) -> None:
        self.send_button.setText(G.STOP if busy else G.SEND)
        self.send_button.setToolTip("Stoppen" if busy else "Senden")
        self.status.setText("denkt nach" if busy else "")
        self.orb.busy = busy
        if not busy:
            self.heard = None  # das Gesprochene steht jetzt fertig im Chat
            self._end_reply()
            for card in list(self.cards):  # eine abgebrochene Antwort wartet auf nichts mehr
                self.settle_card(card, "Abgebrochen")

    def _on_cleared(self) -> None:
        self._rebuild()

    def _on_recording(self, recording: bool) -> None:
        self._on_listening(recording)
        self.status.setText("hört zu" if recording else "")
        (self._voice_timer.start if recording else self._voice_timer.stop)()
        if recording:
            self.composer.clear()
            self.composer.setPlaceholderText("Sprich einfach, ich höre zu …")
        else:
            self.composer.setPlaceholderText("Schreib Nova")

    # --- Sprechen --------------------------------------------------------------
    def _listen_options(self) -> dict:
        settings = self.ctx.config.get("nova")
        return {"auto_stop": settings["listen_auto_stop"], "silence": settings["listen_silence"], "live": settings["listen_live"]}

    def _mic_clicked(self) -> None:
        """Mit Stimme spricht man mit Nova über Gemini Live, sonst diktiert man ins Feld."""
        if self.voice.recording or not self.nova.live_wanted():
            self.voice.toggle()
            return
        if not self.nova.api_key():
            self.ask_for_key("")
            return
        self.nova.talk()

    def _on_listening(self, listening: bool) -> None:
        self.mic.setText(G.STOP if listening else G.MIC)
        self.mic.setToolTip("Fertig gesprochen" if listening else "Sprechen")
        set_prop(self.mic, "active", listening)

    def _on_partial(self, text: str) -> None:
        """Beim Diktat: Was bisher verstanden wurde, steht schon im Feld."""
        self.composer.setPlainText(text)

    def _dictated(self, text: str) -> None:
        self.composer.clear()
        self.nova.send(text)

    def _on_stage(self, stage: str) -> None:
        self.status.setText(STAGES.get(stage, stage))

    def _on_heard(self, chunk: str) -> None:
        """Im Gespräch erscheint das Gesagte live als eigene Blase und wächst mit."""
        if self.heard is None:
            self._end_reply()
            self._on_user("")
            self.heard = self._last_user_text
            self.heard_text = ""
        self.heard_text += chunk
        self.heard.setPlainText(self.heard_text.strip())
        limit = round((self._built_width - PANEL_PADDING) * USER_WIDTH)
        card = self.heard.parentWidget()
        card.setFixedWidth(self.heard.ideal_width(limit - CARD_PADDING) + CARD_PADDING)

    # --- Eingaben --------------------------------------------------------------
    def _send(self) -> None:
        text = self.composer.toPlainText().strip()
        if text and self.nova.api_key():
            self.composer.clear()
            self.nova.send(text)
        elif text:
            self.ask_for_key(text)

    def _send_or_stop(self) -> None:
        if self.nova.busy:
            self.nova.cancel()
        else:
            self._send()

    def ask_for_key(self, text: str) -> None:
        """Die Nachricht bleibt im Feld und geht raus, sobald der Key da ist."""
        self.composer.setPlainText(text)
        self.key_hint.setText("Er ist kostenlos und bleibt auf diesem PC im Windows-Anmeldespeicher.")
        self.key_card.show()
        self.key_field.setFocus()

    def _save_key(self) -> None:
        key = self.key_field.text().strip()
        if not key:
            return
        if not self.nova.save_key(key):
            self.key_hint.setText("Windows hat den Key nicht gespeichert. Trag ihn unter Einstellungen → Setup ein.")
            return
        self.key_field.clear()
        self.key_card.hide()
        self.composer.setFocus()
        self._send()
