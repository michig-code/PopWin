"""Öffnen- und Speichern-Fenster der PopWin-Apps. In Beta einschaltbar, sonst das normale von Windows."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog

PLACES = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")


def _dialog(window, title: str, filters: str, mode: QFileDialog.FileMode, accept: QFileDialog.AcceptMode) -> QFileDialog:
    dialog = QFileDialog(window, title, "", filters)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog)  # trägt dadurch das Theme von PopWin statt des Windows-Fensters
    dialog.setFileMode(mode)
    dialog.setAcceptMode(accept)
    places = [Path.home() / name for name in PLACES]
    dialog.setSidebarUrls([QUrl.fromLocalFile(str(place)) for place in places if place.exists()])
    return dialog


def own_dialog(window) -> bool:
    return window.ctx.config.get("popwin_file_dialog")


def pick_files(window, title: str, filters: str) -> list[str]:
    if not own_dialog(window):
        return QFileDialog.getOpenFileNames(window, title, "", filters)[0]
    dialog = _dialog(window, title, filters, QFileDialog.FileMode.ExistingFiles, QFileDialog.AcceptMode.AcceptOpen)
    return dialog.selectedFiles() if dialog.exec() else []


def pick_save(window, title: str, filters: str) -> str:
    if not own_dialog(window):
        return QFileDialog.getSaveFileName(window, title, "", filters)[0]
    dialog = _dialog(window, title, filters, QFileDialog.FileMode.AnyFile, QFileDialog.AcceptMode.AcceptSave)
    return dialog.selectedFiles()[0] if dialog.exec() and dialog.selectedFiles() else ""
