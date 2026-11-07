"""Benachrichtigungen am Bildschirmrand, wie die Mitteilungszentrale von Windows, aber frei einstellbar."""
from __future__ import annotations

from datetime import datetime
from itertools import groupby

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core import quick_settings, win_api
from core.notifications import Notice, app_name, format_age, visible_notices
from ui.base import G, IconButton, PopupPanel, glyph_icon, icon_for_target

EDGE_GAP = 6
ICON_SIZE = 20
CARD_MARGIN = 56  # Abstände von Panel, Liste, Karte und Rollbalken
PICTURE_HEIGHT = 220


def plain_label(text: str, object_name: str = "") -> QLabel:
    """Nur Klartext: Inhalte von Benachrichtigungen dürfen nichts formatieren oder nachladen."""
    label = QLabel(text, objectName=object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class NoticeCard(QWidget):
    def __init__(self, panel: "NotificationPanel", notice: Notice, now: datetime):
        super().__init__(objectName="PreviewCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.panel = panel
        self.notice = notice
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 6, 4, 8)
        box.setSpacing(2)
        head = QHBoxLayout()
        head.addWidget(plain_label(notice.title, "MediaTitle"), 1)
        head.addWidget(QLabel(format_age(notice.arrival, now), objectName="Muted"))
        close = IconButton(G.CLOSE, "Entfernen")
        close.clicked.connect(lambda: panel.hide_notices([notice]))
        head.addWidget(close)
        box.addLayout(head)
        if notice.body:
            box.addWidget(plain_label(notice.body, "Muted"))
        picture = panel.picture(notice.image)
        if picture is not None:
            image = QLabel()
            image.setPixmap(picture)
            box.addSpacing(4)
            box.addWidget(image)

    def mouseReleaseEvent(self, event) -> None:
        self.panel.dismiss()
        win_api.shell_open(self.notice.target)


class NotificationPanel(PopupPanel):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._pictures: dict[tuple, QPixmap | None] = {}
        header = QHBoxLayout()
        header.addWidget(QLabel("Benachrichtigungen", objectName="PanelTitle"))
        self.count = QLabel(objectName="Muted")
        header.addWidget(self.count)
        header.addStretch(1)
        self.clear = QPushButton("Alle löschen", objectName="Secondary")
        self.clear.clicked.connect(lambda: self.hide_notices(self._visible()))
        settings = IconButton(G.SETTINGS, "Einstellungen")
        settings.clicked.connect(lambda: (self.dismiss(), ctx.open_settings_at("Benachrichtigungen")))
        header.addWidget(self.clear)
        header.addWidget(settings)
        self.body.addLayout(header)

        self.content = QWidget()
        self.list = QVBoxLayout(self.content)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(6)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        area.setWidget(self.content)
        self.body.addWidget(area, 1)
        # Ganz unten: „Nicht stören“ direkt hier umschalten, ohne erst die Schnelleinstellungen zu öffnen
        self.quiet = QPushButton(objectName="Secondary")
        self.quiet.setCheckable(True)
        self.quiet.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quiet.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.quiet.clicked.connect(self._set_quiet)
        self.body.addWidget(self.quiet)
        ctx.notifications.updated.connect(self._on_update)

    def _show_quiet(self, on: bool) -> None:
        self.quiet.setChecked(on)
        self.quiet.setText("Nicht stören ist an" if on else "Nicht stören")
        self.quiet.setIcon(glyph_icon(G.QUIET, 16, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"))

    def _set_quiet(self, on: bool) -> None:
        if not quick_settings.set_do_not_disturb(on):
            on = not on  # Windows hat abgelehnt: Knopf zeigt weiter den echten Zustand
        self._show_quiet(on)

    def _visible(self) -> list[Notice]:
        return visible_notices(self.ctx.notifications.notices, self.ctx.config.get("notifications.hidden"))

    def hide_notices(self, notices: list[Notice]) -> None:
        """Windows lässt sich von außen nichts löschen; PopWin merkt sich, was weg soll."""
        known = {notice.id for notice in self.ctx.notifications.notices}
        kept = [i for i in self.ctx.config.get("notifications.hidden") if i in known]
        self.ctx.config.set("notifications.hidden", kept + [notice.id for notice in notices])
        self.rebuild()

    def _on_update(self) -> None:
        if self.isVisible():
            self.rebuild()

    def picture(self, path: str) -> QPixmap | None:
        """Passt in die Breite der Karte. Das Ergebnis bleibt gemerkt: Große Bilder zu laden dauert spürbar."""
        if not path:
            return None
        width = self.ctx.config.get("notifications.width") - CARD_MARGIN
        ratio = self.devicePixelRatioF()
        key = (path, width, ratio)
        if key not in self._pictures:
            image = QPixmap(path)
            if not image.isNull():
                image = image.scaled(QSize(round(width * ratio), round(PICTURE_HEIGHT * ratio)),
                                     Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                image.setDevicePixelRatio(ratio)
            self._pictures[key] = None if image.isNull() else image
        return self._pictures[key]

    def _app_icon(self, notice: Notice) -> QIcon:
        icon = icon_for_target(notice.target)
        if icon.isNull() and notice.logo:
            icon = QIcon(notice.logo)
        if not icon.isNull():
            return icon
        return glyph_icon(G.BELL, ICON_SIZE, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020")

    def _group_header(self, notices: list[Notice]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 6, 0, 0)
        icon = QLabel()
        icon.setPixmap(self._app_icon(notices[0]).pixmap(QSize(ICON_SIZE, ICON_SIZE)))
        layout.addWidget(icon)
        layout.addWidget(plain_label(app_name(notices[0].app_id, self.ctx.apps.apps), "MediaTitle"), 1)
        if len(notices) > 1:
            close = IconButton(G.CLOSE, "Alle dieser App entfernen")
            close.clicked.connect(lambda: self.hide_notices(notices))
            layout.addWidget(close)
        return row

    def rebuild(self) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        notices = self._visible()
        config = self.ctx.config.get("notifications")
        now = datetime.now().astimezone()
        self.count.setText(str(len(notices)) if notices else "")
        self._show_quiet(quick_settings.do_not_disturb())
        self.clear.setVisible(bool(notices))
        if config["group"]:
            order = {}
            for notice in notices:  # die App mit der neuesten Nachricht steht oben
                order.setdefault(notice.app_id, []).append(notice)
            groups = list(order.values())
        else:
            groups = [list(group) for _app, group in groupby(notices, key=lambda n: n.app_id)]
        for group in groups:
            self.list.addWidget(self._group_header(group))
            for notice in group:
                self.list.addWidget(NoticeCard(self, notice, now))
        if not notices:
            text = ("Keine neuen Benachrichtigungen." if self.ctx.notifications.available
                    else "Windows gibt die Benachrichtigungen gerade nicht heraus.")
            self.list.addWidget(plain_label(text, "Muted"))
        self.list.addStretch(1)

    def open_on(self, screen) -> None:
        if self.isVisible() and not self.animator.closing:
            self.dismiss()
            return
        self._take_over()
        self.apply_theme()
        self.rebuild()
        config = self.ctx.config.get("notifications")
        area = screen.availableGeometry()
        self.setFixedSize(config["width"], area.height() - 2 * EDGE_GAP)
        left = config["side"] == "left"
        x = area.left() + EDGE_GAP if left else area.right() - config["width"] - EDGE_GAP + 1
        self.move(x, area.top() + EDGE_GAP)
        self.show()
        win_api.force_foreground(int(self.winId()))
        self.activateWindow()
        self.setFocus()
        self.animator.enter(1.0, slide=-8)
