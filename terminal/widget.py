"""Ein echtes Terminal in Qt: Windows' ConPTY führt die Shell aus, pyte versteht ihre Ausgabe, wir zeichnen sie."""
from __future__ import annotations

import logging
import os
import re
import shutil

import pyte
from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from core import win_api

log = logging.getLogger(__name__)
SCROLLBACK = 5000
FLUSH_MS = 16
DEFAULT_FONT_SIZE = 11
PADDING = 8
MIN_FONT, MAX_FONT = 7, 36
URL = re.compile(r"https?://[^\s\"'<>]+")
BACKGROUND, FOREGROUND = "#0C0C0C", "#CCCCCC"
PALETTE = {  # Campbell, das Farbschema von Windows Terminal
    "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "brown": "#C19C00", "blue": "#0037DA",
    "magenta": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC", "brightblack": "#767676", "brightred": "#E74856",
    "brightgreen": "#16C60C", "brightbrown": "#F9F1A5", "brightyellow": "#F9F1A5", "brightblue": "#3B78FF",
    "brightmagenta": "#B4009E", "brightcyan": "#61D6D6", "brightwhite": "#F2F2F2",
}
SPECIAL_KEYS = {
    Qt.Key.Key_Return: "\r", Qt.Key.Key_Enter: "\r", Qt.Key.Key_Backspace: "\x7f", Qt.Key.Key_Tab: "\t",
    Qt.Key.Key_Backtab: "\x1b[Z", Qt.Key.Key_Escape: "\x1b", Qt.Key.Key_Up: "\x1b[A", Qt.Key.Key_Down: "\x1b[B",
    Qt.Key.Key_Right: "\x1b[C", Qt.Key.Key_Left: "\x1b[D", Qt.Key.Key_Home: "\x1b[H", Qt.Key.Key_End: "\x1b[F",
    Qt.Key.Key_Insert: "\x1b[2~", Qt.Key.Key_Delete: "\x1b[3~", Qt.Key.Key_PageUp: "\x1b[5~",
    Qt.Key.Key_PageDown: "\x1b[6~", Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ", Qt.Key.Key_F3: "\x1bOR",
    Qt.Key.Key_F4: "\x1bOS", Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~", Qt.Key.Key_F7: "\x1b[18~",
    Qt.Key.Key_F8: "\x1b[19~", Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~", Qt.Key.Key_F11: "\x1b[23~",
    Qt.Key.Key_F12: "\x1b[24~",
}


def profiles() -> dict[str, list[str]]:
    """Die Shells, die es auf diesem PC wirklich gibt."""
    found = {"PowerShell": ["powershell.exe", "-NoLogo"], "Eingabeaufforderung": ["cmd.exe"]}
    if shutil.which("pwsh"):
        found["PowerShell 7"] = ["pwsh.exe", "-NoLogo"]
    if shutil.which("wsl"):
        found["WSL"] = ["wsl.exe"]
    bash = os.path.join(os.environ.get("ProgramFiles", ""), "Git", "bin", "bash.exe")
    if os.path.exists(bash):
        found["Git Bash"] = [bash, "--login", "-i"]
    return found


def color_of(name: str, default: str, bold: bool = False) -> QColor:
    if name == "default":
        return QColor(default)
    if bold and f"bright{name}" in PALETTE:
        return QColor(PALETTE[f"bright{name}"])
    return QColor(PALETTE.get(name, f"#{name}"))


def to_key_text(key: int, text: str, ctrl: bool, alt: bool, shift: bool) -> str:
    """Die Zeichen, die eine Taste an die Shell schickt."""
    if key in SPECIAL_KEYS and not ctrl:
        return ("\x1b" if alt else "") + SPECIAL_KEYS[key]
    if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(key - Qt.Key.Key_A + 1)
    if ctrl and key == Qt.Key.Key_Left:
        return "\x1b[1;5D"
    if ctrl and key == Qt.Key.Key_Right:
        return "\x1b[1;5C"
    if ctrl and key == Qt.Key.Key_Backspace:
        return "\x17"
    return ("\x1b" if alt else "") + text


class Reader(QThread):
    output = Signal(str)
    ended = Signal()

    def __init__(self, process, parent=None):
        super().__init__(parent)
        self.process = process

    def run(self) -> None:
        while True:
            try:
                data = self.process.read(65536)
            except (EOFError, OSError, ValueError):
                break
            if data:
                self.output.emit(data)
        self.ended.emit()


class TitleScreen(pyte.HistoryScreen):
    """pyte stolpert über Parameter, die moderne Shells mitschicken; die sind fürs Bild egal."""

    def select_graphic_rendition(self, *attrs, **kwargs) -> None:
        kwargs.pop("private", None)
        super().select_graphic_rendition(*attrs)

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        """pyte wirft beim Verkleinern immer oben Zeilen weg, auch wenn unten alles leer ist; Text und Cursor passen dann
        nicht mehr zusammen. Oben geht nur etwas verloren, wenn der Cursor sonst aus dem Bild fiele."""
        lines = lines or self.lines
        if lines < self.lines:
            lost = max(0, self.cursor.y + 1 - lines)
            if lost:
                self.save_cursor()
                self.cursor_position(0, 0)
                self.delete_lines(lost)
                self.restore_cursor()
                self.cursor.y -= lost
            for row in [row for row in self.buffer if row >= lines]:
                del self.buffer[row]
            self.lines = lines
        super().resize(lines, columns)
        self.set_margins()


class TerminalWidget(QWidget):
    title_changed = Signal(str)
    closed = Signal()

    def __init__(self, command: list[str], cwd: str = ""):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        # Eigene Schrift statt setFont: Das Theme überschreibt die Schrift jedes Widgets. Gerechnet würde dann in
        # Monospace-Zellen, gezeichnet aber schmale Oberflächenschrift, und der Cursor stünde weit weg vom Text.
        self.text_font = QFont()
        self.text_font.setFamilies(["Cascadia Mono", "Consolas"])
        self.text_font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_font.setFixedPitch(True)
        self.text_font.setPointSize(DEFAULT_FONT_SIZE)
        self._measure()
        self.command, self.cwd = command, cwd
        self.rows, self.columns = 30, 100
        self.screen_data = TitleScreen(self.columns, self.rows, history=SCROLLBACK, ratio=0.5)
        self.stream = pyte.Stream(self.screen_data)
        self.process = None
        self.reader: Reader | None = None
        self._pending: list[str] = []
        self._title = ""
        self._selection: tuple[QPoint, QPoint] | None = None
        self._flush = QTimer(self, singleShot=True, interval=FLUSH_MS)
        self._flush.timeout.connect(self._apply_output)

    def start(self) -> None:
        from winpty import PtyProcess
        self.process = PtyProcess.spawn(self.command, cwd=self.cwd or None, dimensions=(self.rows, self.columns))
        self.reader = Reader(self.process, self)
        self.reader.output.connect(self._queue)
        self.reader.ended.connect(self.closed)
        self.reader.start()

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate(force=True)
            except Exception as exc:
                log.debug("Shell schon weg: %s", exc)
        if self.reader is not None:
            self.reader.wait(1500)

    def write(self, text: str) -> None:
        if self.process is not None and self.process.isalive():
            self.process.write(text)

    def run_line(self, text: str) -> None:
        self.write(text + "\r")

    # --- Ausgabe ------------------------------------------------------------------
    def _queue(self, data: str) -> None:
        self._pending.append(data)
        if not self._flush.isActive():
            self._flush.start()

    def _apply_output(self) -> None:
        data, self._pending = "".join(self._pending), []
        self._to_bottom()
        self.stream.feed(data)
        title = self.screen_data.title
        if title and title != self._title:
            self._title = title
            self.title_changed.emit(title)
        self.update()

    def _to_bottom(self) -> None:
        history = self.screen_data.history
        while history.position < history.size and history.bottom:
            self.screen_data.next_page()

    # --- Größe und Schrift ------------------------------------------------------------
    def _measure(self) -> None:
        metrics = QFontMetricsF(self.text_font)
        self.cell_width = metrics.horizontalAdvance("M")
        self.cell_height = round(metrics.height())
        self.ascent = round(metrics.ascent())

    def zoom(self, step: int) -> None:
        self.text_font.setPointSize(min(MAX_FONT, max(MIN_FONT, self.text_font.pointSize() + step)))
        self._measure()
        self._fit()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        columns = max(20, int((self.width() - 2 * PADDING) / self.cell_width))
        rows = max(5, (self.height() - 2 * PADDING) // self.cell_height)
        if (rows, columns) == (self.rows, self.columns):
            return
        self.rows, self.columns = rows, columns
        self.screen_data.resize(rows, columns)
        if self.process is not None:
            self.process.setwinsize(rows, columns)

    # --- Zeichnen ---------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.text_font)
        painter.fillRect(self.rect(), QColor(BACKGROUND))
        painter.translate(PADDING, PADDING)
        screen = self.screen_data
        for row in range(min(screen.lines, self.rows)):
            self._paint_row(painter, row, screen.buffer[row])
        self._paint_selection(painter)
        history = screen.history
        at_bottom = not history.bottom or history.position >= history.size
        if at_bottom and not screen.cursor.hidden and self.hasFocus():
            x, y = round(screen.cursor.x * self.cell_width), screen.cursor.y * self.cell_height
            painter.fillRect(QRect(x, y + self.cell_height - 3, round(self.cell_width), 3), QColor(FOREGROUND))
        elif at_bottom and not screen.cursor.hidden:
            painter.setPen(QColor(FOREGROUND))
            x, y = round(screen.cursor.x * self.cell_width), screen.cursor.y * self.cell_height
            painter.drawRect(x, y, round(self.cell_width) - 1, self.cell_height - 1)

    def _paint_row(self, painter: QPainter, row: int, line) -> None:
        column = 0
        while column < self.columns:
            char = line[column]
            end = column + 1
            run = [char.data]
            while end < self.columns and self._same_style(line[end], char):
                run.append(line[end].data)
                end += 1
            self._paint_run(painter, row, column, end, "".join(run), char)
            column = end

    @staticmethod
    def _same_style(a, b) -> bool:
        return a.fg == b.fg and a.bg == b.bg and a.bold == b.bold and a.reverse == b.reverse and a.underscore == b.underscore

    def _paint_run(self, painter: QPainter, row: int, start: int, end: int, text: str, char) -> None:
        foreground = color_of(char.fg, FOREGROUND, char.bold)
        background = color_of(char.bg, BACKGROUND)
        if char.reverse:
            foreground, background = background, foreground
        x = round(start * self.cell_width)
        y = row * self.cell_height
        width = round((end - start) * self.cell_width)
        if background != QColor(BACKGROUND):
            painter.fillRect(QRect(x, y, width, self.cell_height), background)
        if text.strip():
            font = painter.font()
            font.setBold(char.bold)
            font.setUnderline(char.underscore)
            painter.setFont(font)
            painter.setPen(foreground)
            painter.drawText(x, y + self.ascent, text)

    # --- Markieren und Kopieren ----------------------------------------------------------
    def _cell(self, position) -> QPoint:
        return QPoint(max(0, min(self.columns - 1, int((position.x() - PADDING) / self.cell_width))),
                      max(0, min(self.rows - 1, int((position.y() - PADDING) // self.cell_height))))

    def _selected_rows(self) -> list[tuple[int, int, int]]:
        if self._selection is None:
            return []
        start, end = self._selection
        if (start.y(), start.x()) > (end.y(), end.x()):
            start, end = end, start
        rows = []
        for row in range(start.y(), end.y() + 1):
            first = start.x() if row == start.y() else 0
            last = end.x() if row == end.y() else self.columns - 1
            rows.append((row, first, last))
        return rows

    def selected_text(self) -> str:
        lines = []
        for row, first, last in self._selected_rows():
            line = self.screen_data.buffer[row]
            lines.append("".join(line[column].data for column in range(first, last + 1)).rstrip())
        return "\n".join(lines)

    def _paint_selection(self, painter: QPainter) -> None:
        for row, first, last in self._selected_rows():
            painter.fillRect(QRect(round(first * self.cell_width), row * self.cell_height,
                                   round((last - first + 1) * self.cell_width), self.cell_height),
                             QColor(120, 160, 220, 110))

    def url_at(self, position) -> str:
        cell = self._cell(position)
        text = "".join(self.screen_data.buffer[cell.y()][column].data for column in range(self.columns))
        return next((m.group() for m in URL.finditer(text) if m.start() <= cell.x() < m.end()), "")

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                url = self.url_at(event.position())
                if url:
                    win_api.shell_open(url)
                    return
            cell = self._cell(event.position())
            self._selection = (cell, cell)
            self._selecting = True
        elif event.button() == Qt.MouseButton.RightButton:
            # Wie in der Windows-Konsole: mit Markierung kopieren, ohne einfügen.
            if self._selection and self.selected_text():
                self.copy()
            else:
                self.paste()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._selection and event.buttons() & Qt.MouseButton.LeftButton:
            self._selection = (self._selection[0], self._cell(event.position()))
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._selection and self._selection[0] == self._selection[1]:
            self._selection = None
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        cell = self._cell(event.position())
        line = self.screen_data.buffer[cell.y()]
        text = "".join(line[column].data for column in range(self.columns))
        match = next((m for m in re.finditer(r"\S+", text) if m.start() <= cell.x() < m.end()), None)
        if match:
            self._selection = (QPoint(match.start(), cell.y()), QPoint(match.end() - 1, cell.y()))
            self.update()

    def copy(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
        self._selection = None
        self.update()

    def paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text:
            self.write(text.replace("\r\n", "\r").replace("\n", "\r"))

    def clear(self) -> None:
        self.screen_data.history.top.clear()
        self.screen_data.history.bottom.clear()
        self.write("\x0c")

    # --- Eingabe -----------------------------------------------------------------------
    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
            return
        for _ in range(3):
            (self.screen_data.prev_page if event.angleDelta().y() > 0 else self.screen_data.next_page)()
        self.update()

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False  # Tab gehört der Shell, nicht der Fensternavigation

    def focusInEvent(self, event) -> None:
        self.update()

    def focusOutEvent(self, event) -> None:
        self.update()

    def keyPressEvent(self, event) -> None:
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if ctrl and event.key() == Qt.Key.Key_C and (shift or self._selection):
            self.copy()  # Strg + C ohne Markierung bleibt Abbrechen
        elif ctrl and event.key() == Qt.Key.Key_V:
            self.paste()
        else:
            data = to_key_text(event.key(), event.text(), ctrl, alt, shift)
            if data:
                self._selection = None
                self.write(data)
