"""PopWin Editor: Notepad mit Tabs, Farben, Suchen und Ersetzen. Ungespeichertes übersteht jeden Neustart."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import QMenu, QMessageBox, QPushButton  # noqa: E402

from editor.widget import EditorPanel  # noqa: E402
from ui.file_dialog import pick_files, pick_save  # noqa: E402
from ui.app_shell import run_app  # noqa: E402
from ui.base import themed_menu  # noqa: E402
from ui.window_frame import NativeWindow  # noqa: E402

FILE_TYPES = ("Textdateien (*.txt *.md *.log *.ini *.json *.csv *.py *.ps1 *.bat *.xml *.html *.css *.js);;"
              "Bilder (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.ico);;Alle Dateien (*)")


class EditorWindow(NativeWindow):
    def __init__(self, ctx, files: list[str] | None = None):
        super().__init__(ctx, "PopWin Editor", "apps/editor")
        self.resize(1000, 680)
        self.panel = EditorPanel(ctx)
        self.panel.title_changed.connect(lambda name: self.titlebar.title.setText(f"{Path(name).name} · PopWin Editor"))
        for label, handler in (("Datei", self._file_menu), ("Bearbeiten", self._edit_menu), ("Ansicht", self._view_menu)):
            button = QPushButton(label, objectName="Secondary")
            button.clicked.connect(handler)
            self.titlebar.middle.addWidget(button)
        self.titlebar.middle.addStretch(1)
        from PySide6.QtWidgets import QVBoxLayout
        box = QVBoxLayout(self.content)
        box.setContentsMargins(6, 2, 6, 4)
        box.addWidget(self.panel)
        self.panel.bind_shortcuts(self)
        for sequence, handler in (("Ctrl+N", self.panel.new_tab), ("Ctrl+O", self.open_dialog), ("Ctrl+S", self.save),
                                  ("Ctrl+Shift+S", self.save_as), ("Ctrl+W", self._close_tab), ("F5", self.insert_date)):
            QShortcut(QKeySequence(sequence), self, activated=handler)
        self.panel.restore_session()
        for file in files or []:
            self.panel.open_file(file)
        self.apply_theme()

    def _popup(self, fill) -> None:
        menu: QMenu = themed_menu(self.ctx, self)
        fill(menu)
        button = self.sender()
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _file_menu(self) -> None:
        def fill(menu: QMenu) -> None:
            menu.addAction("Neu (Strg + N)", lambda: self.panel.new_tab())
            menu.addAction("Öffnen (Strg + O)", self.open_dialog)
            menu.addAction("Speichern (Strg + S)", self.save)
            menu.addAction("Speichern unter (Strg + Umschalt + S)", self.save_as)
            recent = self.panel.recent_files()
            if recent:
                menu.addSeparator()
                for path in recent[:8]:
                    menu.addAction(Path(path).name, lambda _=False, p=path: self.panel.open_file(p))
        self._popup(fill)

    def _edit_menu(self) -> None:
        panel = self.panel

        def fill(menu: QMenu) -> None:
            menu.addAction("Suchen und Ersetzen (Strg + F)", panel.show_find)
            menu.addAction("Gehe zu Zeile (Strg + G)", panel.ask_line)
            menu.addSeparator()
            menu.addAction("Zeile verdoppeln (Strg + D)", lambda: panel.editor().duplicate_line())
            menu.addAction("Zeilen sortieren", lambda: panel.editor().sort_lines())
            menu.addAction("In Großbuchstaben (Strg + Umschalt + U)", lambda: panel.editor().transform_selection(str.upper))
            menu.addAction("In Kleinbuchstaben (Strg + U)", lambda: panel.editor().transform_selection(str.lower))
            menu.addAction("Datum einfügen (F5)", self.insert_date)
        self._popup(fill)

    def _view_menu(self) -> None:
        panel = self.panel

        def fill(menu: QMenu) -> None:
            wrap = menu.addAction("Zeilenumbruch (Alt + Z)", panel.toggle_wrap)
            wrap.setCheckable(True)
            wrap.setChecked(panel.wrap)
            saving = menu.addAction("Automatisch speichern", self.toggle_autosave)
            saving.setCheckable(True)
            saving.setChecked(panel.autosave)
            menu.addAction("Größer (Strg + Plus)", lambda: panel.editor().zoom(1))
            menu.addAction("Kleiner (Strg + Minus)", lambda: panel.editor().zoom(-1))
        self._popup(fill)

    def toggle_autosave(self) -> None:
        self.panel.autosave = not self.panel.autosave
        self.panel.save_session()

    def mouse_step(self, step: int) -> None:
        self.panel.step_tab(step)

    def open_dialog(self) -> None:
        for path in pick_files(self, "Öffnen", FILE_TYPES):
            self.panel.open_file(path)

    def save(self) -> None:
        tab = self.panel.current()
        if tab and tab.path:
            self.panel.save(tab)
        else:
            self.save_as()

    def save_as(self) -> None:
        path = pick_save(self, "Speichern unter", FILE_TYPES)
        if path:
            self.panel.save(path=path)

    def _close_tab(self) -> None:
        self.panel.close_tab(self.panel.bar.currentIndex())

    def insert_date(self) -> None:
        editor = self.panel.editor()
        if editor:
            editor.insertPlainText(time.strftime("%H:%M %d.%m.%Y"))

    def ask_about_unsaved(self) -> bool:
        """False bricht das Schließen ab. Mit automatischem Speichern gibt es nichts zu fragen."""
        unsaved = self.panel.unsaved_tabs()
        if self.panel.autosave:
            self.panel._autosave()
            unsaved = [tab for tab in unsaved if not tab.path]
        if not unsaved:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "PopWin Editor",
                          f"{len(unsaved)} Tab(s) sind nicht gespeichert.", parent=self)
        save = box.addButton("Speichern", QMessageBox.ButtonRole.AcceptRole)
        draft = box.addButton("Als Entwurf behalten", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is draft:
            return True
        if box.clickedButton() is not save:
            return False
        for tab in unsaved:
            self.panel.bar.setCurrentIndex(self.panel.tabs.index(tab))
            self.save()
            if tab.modified:  # im Speichern-Dialog abgebrochen
                return False
        return True

    def closeEvent(self, event) -> None:
        if not self.ask_about_unsaved():
            event.ignore()
            return
        self.panel.save_session()  # Entwürfe sind beim nächsten Start wieder da
        super().closeEvent(event)


def main() -> int:
    return run_app("Editor", "PopWin Editor", lambda ctx, arguments: EditorWindow(ctx, arguments))


if __name__ == "__main__":
    sys.exit(main())
