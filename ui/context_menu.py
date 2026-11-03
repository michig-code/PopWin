"""Rechtsklickmenü für Desktop und Explorer: alles sofort sichtbar, kein "Weitere Optionen anzeigen"."""
from __future__ import annotations

import logging
import shutil

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget, QWidgetAction

from core import win_api
from ui.animation import WindowAnimator, fade_ghost
from ui.base import G, dress_menu, glyph_icon, style_menu

log = logging.getLogger(__name__)
# Diese Einträge bietet PopWin selbst an, aus dem Windows-Menü kämen sie doppelt.
OWN_LABELS = {"öffnen", "open", "ausschneiden", "cut", "kopieren", "copy", "einfügen", "paste", "löschen", "delete",
              "umbenennen", "rename", "als pfad kopieren", "copy as path", "zum schnellzugriff", "pin to quick access"}


def trim_separators(entries: list) -> list:
    """Keine Trennlinien am Rand oder doppelt, nachdem Einträge weggefallen sind."""
    kept = []
    for entry in entries:
        if entry.label or (kept and kept[-1].label):
            kept.append(entry)
    return kept[:-1] if kept and not kept[-1].label else kept


def unique_path(folder, base: str, suffix: str = ""):
    """'Neuer Ordner', 'Neuer Ordner (2)', ... - bestehende Dateien werden nie überschrieben."""
    candidate = folder / f"{base}{suffix}"
    number = 2
    while candidate.exists():
        candidate = folder / f"{base} ({number}){suffix}"
        number += 1
    return candidate


class ContextMenu(QMenu):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view_menu = self.submenu("Ansicht")
        self._extra_menus: list[QMenu] = []
        self.shell: win_api.ShellMenu | None = None
        self.aboutToHide.connect(self._release_shell)
        self.animator = WindowAnimator(ctx, self)
        self.aboutToHide.connect(lambda: fade_ghost(ctx, self))

    def submenu(self, title: str) -> QMenu:
        """Klappt seitlich auf und sieht aus wie das Hauptmenü."""
        menu = QMenu(title, self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.aboutToShow.connect(lambda: self._dress(menu))
        return menu

    def _release_shell(self) -> None:
        # Verzögert: Ein Klick löst erst nach aboutToHide aus und braucht das Windows-Menü noch.
        shell, self.shell = self.shell, None
        if shell is not None:
            QTimer.singleShot(0, shell.close)

    def _color(self) -> str:
        return "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"

    def _style(self, config: dict) -> None:
        style_menu(self.ctx, self)
        self._icon_px = config["icon_size"]

    def _add(self, label: str, glyph: str, handler):
        action = self.addAction(glyph_icon(glyph, self._icon_px, self._color()), label)
        action.triggered.connect(lambda _=False: handler())
        return action

    def add_item(self, label: str, glyph: str, handler, enabled: bool = True) -> None:
        self._add(label, glyph, handler).setEnabled(enabled)

    def add_shell_items(self, hwnd: int, paths: list[str]) -> None:
        """Was Windows und installierte Programme anbieten, kommt gesammelt in ein eigenes Untermenü."""
        try:
            self.shell = win_api.ShellMenu(hwnd, paths)
            entries = self.shell.entries()
        except Exception as exc:  # fremde Erweiterungen können alles werfen, das Menü muss trotzdem aufgehen
            log.warning("Windows-Menü nicht lesbar: %s", exc)
            return
        kept = trim_separators([entry for entry in entries if entry.label.lower() not in OWN_LABELS])
        if kept:
            more = self._child_menu("Weitere Optionen")
            more.setIcon(glyph_icon(G.APP, self._icon_px, self._color()))
            self.addMenu(more)
            self._add_entries(more, kept)

    def _child_menu(self, title: str) -> QMenu:
        child = self.submenu(title)
        style_menu(self.ctx, child)
        self._extra_menus.append(child)
        return child

    def _add_entries(self, menu: QMenu, entries: list) -> None:
        shell = self.shell
        for entry in entries:
            if not entry.label:
                menu.addSeparator()
            elif entry.children is not None:
                child = self._child_menu(entry.label)
                menu.addMenu(child).setEnabled(entry.enabled and bool(entry.children))
                self._add_entries(child, entry.children)
            else:
                action = menu.addAction(entry.label)
                action.setEnabled(entry.enabled)
                action.triggered.connect(lambda _=False, c=entry.command: self._run_shell(shell, c))

    def _run_shell(self, shell, command: int) -> None:
        try:
            shell.run(command)
        except Exception as exc:
            log.warning("Windows-Befehl fehlgeschlagen: %s", exc)

    def _start(self) -> None:
        self.clear()
        for menu in self._extra_menus:
            menu.deleteLater()
        self._extra_menus = []
        self._style(self.ctx.config.get("context_menu"))

    def _chip_row(self, glyph: str, label: str, items: list[tuple[str, object]]) -> None:
        """Eine ganze Unterauswahl in einer Zeile, statt eines Untermenüs."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 3, 8, 3)
        layout.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(glyph_icon(glyph, self._icon_px, self._color()).pixmap(QSize(self._icon_px, self._icon_px)))
        text = QLabel(label, objectName="MenuRowLabel")
        layout.addWidget(icon)
        layout.addWidget(text)
        layout.addStretch(1)
        for chip_label, handler in items:
            chip = QPushButton(chip_label, objectName="MenuChip")
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(lambda _=False, h=handler: (self.close(), h()))
            layout.addWidget(chip)
        action = QWidgetAction(self)
        action.setDefaultWidget(row)
        self.addAction(action)

    def _fill_view_menu(self) -> None:
        menu = self.view_menu
        menu.clear()
        menu.setIcon(glyph_icon(G.APP, self._icon_px, self._color()))
        menu.setProperty("popMode", self.ctx.theme.mode)
        menu.setWindowOpacity(self.ctx.config.get("context_menu.opacity"))
        state = win_api.desktop_icon_state()

        def add(label: str, command: int, checked: bool | None = None) -> None:
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False: win_api.desktop_command(command))
            if checked is not None:
                action.setCheckable(True)
                action.setChecked(checked)

        add("Große Symbole", win_api.DESKTOP_ICON_LARGE)
        add("Mittelgroße Symbole", win_api.DESKTOP_ICON_MEDIUM)
        add("Kleine Symbole", win_api.DESKTOP_ICON_SMALL)
        menu.addSeparator()
        add("Symbole automatisch anordnen", win_api.DESKTOP_AUTO_ARRANGE, state["arrange"])
        add("Am Raster ausrichten", win_api.DESKTOP_SNAP_TO_GRID, state["grid"])
        menu.addSeparator()
        add("Desktopsymbole anzeigen", win_api.DESKTOP_TOGGLE_ICONS, state["visible"])

    def add_sort_row(self, sort) -> None:
        """sort bekommt die Spalte: name, size, type oder date."""
        self._chip_row(G.DOC, "Sortieren", [(label, lambda k=key: sort(k)) for label, key in (
            ("Name", "name"), ("Größe", "size"), ("Typ", "type"), ("Datum", "date"))])

    def build(self, desktop: bool) -> None:
        self._start()
        if desktop:
            self._fill_view_menu()
            self.addMenu(self.view_menu)
            self._add("Aktualisieren", G.REFRESH, lambda: win_api.desktop_command(win_api.DESKTOP_REFRESH))
            self.addSeparator()
            self._add("Terminal hier öffnen", G.TERMINAL, self._open_terminal)
            self._add("Neuer Ordner", G.FOLDER, self._new_folder)
            self._add("Neue Textdatei", G.DOC, self._new_text_file)
            self.addSeparator()
            self._add("Anzeigeeinstellungen", G.DISPLAY, lambda: win_api.shell_open("ms-settings:display"))
            self._add("Personalisieren", G.PERSONALIZE, lambda: win_api.shell_open("ms-settings:personalization"))
        self._add("UI-Einstellungen", G.SETTINGS, self.ctx.open_settings)

    def show_at(self, position, desktop: bool) -> None:
        self.build(desktop)
        self.present(position)

    def show_custom(self, position, fill) -> None:
        """Dasselbe Menü mit anderem Inhalt, zum Beispiel im Explorer."""
        self._start()
        fill(self)
        self.present(position)

    def present(self, position) -> None:
        self.popup(position)
        self._dress(self)
        win_api.force_foreground(int(self.winId()))
        self.animator.enter(self.ctx.config.get("context_menu.opacity"), slide=-6)

    def _dress(self, menu: QMenu) -> None:
        dress_menu(self.ctx, menu)

    def _open_terminal(self) -> None:
        folder = str(win_api.desktop_path())
        if self.ctx.config.get("replacements.terminal"):
            self.ctx.open_app("terminal", folder)
        elif shutil.which("wt.exe"):
            win_api.shell_open("wt.exe", f'-d "{folder}"')
        else:
            win_api.shell_open("cmd.exe", None, folder)

    def _new_folder(self) -> None:
        unique_path(win_api.desktop_path(), "Neuer Ordner").mkdir()

    def _new_text_file(self) -> None:
        unique_path(win_api.desktop_path(), "Neue Textdatei", ".txt").touch()
