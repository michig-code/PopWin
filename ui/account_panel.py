"""Benutzer-Bereich oben links in der Topbar: angemeldetes Konto, alle Konten, Sperren, Wechseln, Abmelden."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core import accounts, win_api
from ui.base import G, PopupPanel, set_prop

AVATAR = 48
SMALL_AVATAR = 26
SIGN_OUT_CONFIRM = "Wirklich abmelden?"


def avatar_pixmap(name: str, picture: str, size: int, ratio: float = 1.0) -> QPixmap:
    """Rundes Profilbild; ohne Foto ein farbiger Kreis mit den Anfangsbuchstaben."""
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(path)
    photo = QPixmap(picture) if picture else QPixmap()
    if not photo.isNull():
        photo = photo.scaled(round(size * ratio), round(size * ratio), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
        photo.setDevicePixelRatio(ratio)
        painter.drawPixmap(0, 0, photo)
    else:
        hue = sum(map(ord, name)) * 47 % 360  # jedes Konto behält seine Farbe
        painter.fillRect(QRectF(0, 0, size, size), QColor.fromHsv(hue, 120, 170))
        font = QFont()
        font.setPixelSize(max(9, size * 2 // 5))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, accounts.initials(name))
    painter.end()
    return pixmap


class AccountRow(QWidget):
    """Ein anderes Konto; ein Klick führt zum Anmeldebildschirm, die eigenen Apps laufen weiter."""

    def __init__(self, panel: "AccountPanel", account: accounts.Account, current: bool):
        super().__init__(objectName="WindowRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.panel = panel
        self.current = current
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(10)
        picture = QLabel()
        picture.setPixmap(avatar_pixmap(account.label, "", SMALL_AVATAR, self.devicePixelRatioF()))
        row.addWidget(picture)
        row.addWidget(QLabel(account.label), 1)
        if current:
            row.addWidget(QLabel("Angemeldet", objectName="Muted"))
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"Zu {account.label} wechseln")
        set_prop(self, "focused", current)

    def mouseReleaseEvent(self, event) -> None:
        if not self.current:
            self.panel.dismiss()
            accounts.switch_user()


class AccountPanel(PopupPanel):
    def __init__(self, ctx, theme_name: str, show_windows=None):
        super().__init__(ctx, theme_name)
        self.setFixedWidth(340)
        self._show_windows = show_windows

        head = QHBoxLayout()
        head.setSpacing(12)
        self.picture = QLabel()
        head.addWidget(self.picture)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = QLabel(objectName="PanelTitle")
        self.login = QLabel(objectName="Muted")
        names.addWidget(self.name)
        names.addWidget(self.login)
        head.addLayout(names, 1)
        settings = QPushButton(G.SETTINGS, objectName="IconButton")
        settings.setToolTip("Konto-Einstellungen")
        settings.setCursor(Qt.CursorShape.PointingHandCursor)
        settings.clicked.connect(lambda: self._run(lambda: win_api.shell_open("ms-settings:yourinfo")))
        head.addWidget(settings, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(head)

        self.body.addWidget(QLabel("Benutzer auf diesem PC", objectName="Muted"))
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(2)
        self.body.addLayout(self.list_box)
        manage = QPushButton("Benutzer verwalten", objectName="Secondary")
        manage.clicked.connect(lambda: self._run(lambda: win_api.shell_open("ms-settings:otherusers")))
        self.body.addWidget(manage)

        grid = QGridLayout()
        grid.setSpacing(6)
        lock = QPushButton("Sperren", objectName="Secondary")
        lock.clicked.connect(lambda: self._run(win_api.lock_workstation))
        switch = QPushButton("Benutzer wechseln", objectName="Secondary")
        switch.clicked.connect(lambda: self._run(accounts.switch_user))
        self.sign_out = QPushButton("Abmelden", objectName="Secondary")
        self.sign_out.clicked.connect(self._sign_out)
        windows = QPushButton("Offene Fenster", objectName="Secondary")
        windows.clicked.connect(self._open_windows)
        for button in (lock, switch, self.sign_out, windows):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        grid.addWidget(lock, 0, 0)
        grid.addWidget(switch, 0, 1)
        grid.addWidget(windows, 1, 0)
        grid.addWidget(self.sign_out, 1, 1)
        self.body.addLayout(grid)

    def _run(self, action) -> None:
        self.dismiss()
        action()

    def _sign_out(self) -> None:
        """Abmelden schließt alle Apps; deshalb erst beim zweiten Klick."""
        if self.sign_out.text() != SIGN_OUT_CONFIRM:
            self.sign_out.setText(SIGN_OUT_CONFIRM)
            set_prop(self.sign_out, "alert", True)
            return
        self._run(win_api.sign_out)

    def _open_windows(self) -> None:
        self.dismiss()
        if self._show_windows:
            self._show_windows()

    def _prepare(self) -> None:
        self.sign_out.setText("Abmelden")
        set_prop(self.sign_out, "alert", False)
        full = accounts.display_name()
        login = accounts.login_name()
        self.name.setText(full)
        self.login.setText(login if login.lower() != full.lower() else "Angemeldet")
        self.picture.setPixmap(avatar_pixmap(full, accounts.account_picture(), AVATAR, self.devicePixelRatioF()))
        while self.list_box.count():
            widget = self.list_box.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        for account in accounts.local_accounts():
            self.list_box.addWidget(AccountRow(self, account, account.name.lower() == login.lower()))

    def open_at(self, anchor: QWidget) -> None:
        self.toggle_at(anchor, self._prepare)
