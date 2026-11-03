"""Das Herz des Editors: Textfeld mit Zeilennummern, Farben, Suchen und Tabs. Auch das PowerShell Studio nutzt es."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPixmap, QPolygon, QShortcut, QSyntaxHighlighter,
    QTextBlockUserData, QTextCharFormat, QTextCursor,
    QTextDocument, QTextFormat, QTextOption,
)
from PySide6.QtWidgets import (
    QCheckBox, QCompleter, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTabBar,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.paths import DATA_DIR

TAB_WIDTH = 4
MARK_WIDTH = 18
STOP_COLOR = "#F2C94C"
BREAK_COLOR = "#E5484D"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
DEFAULT_FONT_SIZE = 11
MIN_FONT, MAX_FONT = 7, 40
AUTOSAVE_MS = 1500
SESSION_FILE = "editor_session.json"
LANGUAGES = {
    ".py": "Python", ".ps1": "PowerShell", ".psm1": "PowerShell", ".psd1": "PowerShell",
    ".json": "JSON", ".js": "JavaScript", ".ts": "JavaScript", ".java": "C-Stil", ".c": "C-Stil", ".cpp": "C-Stil",
    ".cs": "C-Stil", ".css": "CSS", ".qss": "CSS", ".html": "HTML", ".xml": "HTML", ".md": "Markdown",
    ".ini": "INI", ".toml": "INI", ".cfg": "INI", ".bat": "Batch", ".cmd": "Batch",
}
KEYWORDS = {
    "Python": "False None True and as assert async await break class continue def del elif else except finally for "
              "from global if import in is lambda nonlocal not or pass raise return try while with yield",
    "PowerShell": "begin break catch class continue data do dynamicparam else elseif end enum exit filter finally for "
                  "foreach from function if in param process return switch throw trap try until using var while",
    "JavaScript": "async await break case catch class const continue default delete do else export extends false "
                  "finally for from function if import in instanceof let new null return super switch this throw "
                  "true try typeof undefined var void while yield",
    "C-Stil": "abstract bool break case catch char class const continue default double else enum extends false final "
              "float for if implements import int interface long namespace new null package private protected public "
              "return short static string struct super switch this throw true try using var void while",
    "Batch": "call cd echo else exit for goto if in pause rem set setlocal",
}
COMMENTS = {"Python": "#", "PowerShell": "#", "INI": "[#;]", "Batch": "(?i:rem |::)", "JavaScript": "//", "C-Stil": "//"}
COLORS = {"dark": {"keyword": "#569CD6", "string": "#CE9178", "comment": "#6A9955", "number": "#B5CEA8", "name": "#DCDCAA",
                   "variable": "#9CDCFE", "gutter": "#858585", "line": "#2A2D2E", "match": "#515C6A"},
          "light": {"keyword": "#0000FF", "string": "#A31515", "comment": "#008000", "number": "#098658", "name": "#795E26",
                    "variable": "#001080", "gutter": "#8A8A8A", "line": "#EEF1F4", "match": "#FFE58A"}}


def language_of(path: str) -> str:
    return LANGUAGES.get(os.path.splitext(path)[1].lower(), "Text")


def decode(data: bytes) -> tuple[str, str]:
    """(Text, Kodierung). Windows-Dateien kommen ohne Angabe in alten Kodierungen, deshalb die Reihenfolge."""
    for mark, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(mark):
            return data.decode(encoding, "replace"), encoding
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), "cp1252"


def line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def highlight_rules(language: str) -> list[tuple[re.Pattern, str]]:
    """Muster und Farbe; spätere überschreiben frühere, Strings und Kommentare stehen deshalb hinten."""
    rules: list[tuple[str, str]] = [(r"\b-?\d+(\.\d+)?\b", "number")]
    if language in KEYWORDS:
        flags = "(?i)" if language in ("PowerShell", "Batch") else ""
        rules.append((flags + r"\b(" + KEYWORDS[language].replace(" ", "|") + r")\b", "keyword"))
    if language == "Python":
        rules += [(r"\b(?<=def )\w+|(?<=class )\w+", "name"), (r"^\s*@\w+", "name")]
    if language == "PowerShell":
        rules += [(r"\$[\w:]+", "variable"), (r"\b[A-Z][a-z]+-[A-Z]\w+", "name"), (r"\s-[A-Za-z]\w*", "name")]
    if language == "JSON":
        rules += [(r"\b(true|false|null)\b", "keyword")]
    if language == "Markdown":
        return [(re.compile(p), c) for p, c in ((r"^#{1,6} .*", "keyword"), (r"`[^`]+`", "string"), (r"\*\*[^*]+\*\*", "name"),
                                                (r"\[[^\]]+\]\([^)]+\)", "variable"), (r"^\s*[-*+] ", "number"))]
    if language == "INI":
        rules += [(r"^\s*\[.*\]", "keyword"), (r"^\s*[\w.-]+(?=\s*=)", "variable")]
    if language in ("HTML",):
        rules += [(r"</?[\w:-]+", "keyword"), (r"\s[\w:-]+(?==)", "variable")]
    if language == "CSS":
        rules += [(r"[\w-]+(?=\s*:)", "variable"), (r"^[^{\n]+(?=\{)", "name"), (r"#[0-9A-Fa-f]{3,8}\b", "number")]
    rules += [(r'"(\\.|[^"\\])*"', "string"), (r"'(\\.|[^'\\])*'", "string")]
    if language in ("JSON",):
        rules.append((r'"(\\.|[^"\\])*"(?=\s*:)', "variable"))
    if language in COMMENTS:
        rules.append((COMMENTS[language] + ".*", "comment"))
    return [(re.compile(pattern), color) for pattern, color in rules]


class Highlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, mode: str):
        super().__init__(document)
        self.formats = {}
        self.set_colors(COLORS[mode])
        self.rules: list = []

    def set_colors(self, colors: dict) -> None:
        for name, value in colors.items():
            form = QTextCharFormat()
            form.setForeground(QColor(value))
            self.formats[name] = form

    def set_language(self, language: str) -> None:
        self.rules = highlight_rules(language)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for pattern, color in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self.formats[color])


class BreakMark(QTextBlockUserData):
    """Hängt an der Textzeile und wandert deshalb beim Tippen von selbst mit."""

    def __init__(self, condition: str = "", enabled: bool = True, message: str = ""):
        super().__init__()
        self.condition = condition
        self.enabled = enabled
        self.message = message


class LineNumbers(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def mousePressEvent(self, event) -> None:
        self.editor.gutter_pressed(event)

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    zoomed = Signal(int)
    gutter_clicked = Signal(int)
    gutter_menu = Signal(int, QPoint)

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode
        self.families = ["Cascadia Code", "Cascadia Mono", "Consolas"]
        self.text_size = DEFAULT_FONT_SIZE
        self.look = ""
        self._style_text()
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.gutter = LineNumbers(self)
        self.highlighter = Highlighter(self.document(), mode)
        self.completer: QCompleter | None = None
        self.picture: QPixmap | None = None
        self.preview: QLabel | None = None
        self.colors = dict(COLORS[mode])
        self.marks_on = False
        self.numbers_on = True
        self.line_on = True
        self.stop_line = -1
        self.problem_lines: list[int] = []
        self.blockCountChanged.connect(self._fit_gutter)
        self.updateRequest.connect(self._scroll_gutter)
        self.cursorPositionChanged.connect(self._mark_line)
        self._fit_gutter()
        self._mark_line()
        self._apply_tab_stop()

    def _style_text(self) -> None:
        """Über das Stylesheet: Das Theme überschreibt jedes setFont, der Code stünde sonst in Oberflächenschrift."""
        families = ", ".join(f'"{family}"' for family in self.families)
        self.setStyleSheet(f"QPlainTextEdit {{ font-family: {families}; font-size: {self.text_size}pt; {self.look} }}")
        self.ensurePolished()

    def show_image(self, path: str) -> bool:
        """Bilddateien zeigt der Editor als Vorschau; das Textfeld darunter bleibt leer und schreibgeschützt."""
        picture = QPixmap(path)
        if picture.isNull():
            return False
        self.setReadOnly(True)
        self.setViewportMargins(0, 0, 0, 0)
        self.gutter.hide()
        self.blockCountChanged.disconnect(self._fit_gutter)
        self.picture = picture
        self.preview = QLabel(self.viewport(), alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.show()
        self._fit_picture()
        return True

    def _fit_picture(self) -> None:
        if self.preview is None:
            return
        self.preview.setGeometry(self.viewport().rect())
        size = self.viewport().size()
        shown = self.picture if self.picture.width() <= size.width() and self.picture.height() <= size.height() else \
            self.picture.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(shown)

    def _apply_tab_stop(self) -> None:
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * TAB_WIDTH)

    # --- Zeilennummern ------------------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        numbers = 16 + self.fontMetrics().horizontalAdvance("9") * digits if self.numbers_on else 4
        return numbers + (MARK_WIDTH if self.marks_on else 0)

    def enable_marks(self) -> None:
        self.marks_on = True
        self._fit_gutter()
        self.resizeEvent_gutter()

    def resizeEvent_gutter(self) -> None:
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))

    def apply_look(self, family: str, size: int, colors: dict, tab_width: int, numbers: bool, line: bool) -> None:
        """Für Programme wie das Studio, in denen sich Schrift und Farben frei wählen lassen."""
        self.families = [family, "Cascadia Mono", "Consolas"]
        self.text_size = size
        self.look = f"background: {colors['background']}; color: {colors['text']}; border: none;"
        self._style_text()
        self.colors = colors
        self.numbers_on, self.line_on = numbers, line
        self.highlighter.set_colors(colors)
        self.highlighter.rehighlight()
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * tab_width)
        self._fit_gutter()
        self.resizeEvent_gutter()
        self._mark_line()

    def marked_blocks(self) -> list:
        blocks, block = [], self.document().begin()
        while block.isValid():
            if isinstance(block.userData(), BreakMark):
                blocks.append(block)
            block = block.next()
        return blocks

    def replace_lines(self, lines: list[str | None]) -> int:
        """Ersetzt nur Zeilen, die sich ändern, und als ein Schritt fürs Rückgängigmachen. So bleiben Haltepunkte, wo sie sind.
        None löscht die Zeile. Gibt zurück, wie viele Zeilen sich geändert haben."""
        line, column = self.textCursor().blockNumber(), self.textCursor().positionInBlock()
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        changed = 0
        for number in range(len(lines) - 1, -1, -1):  # von hinten, damit die Nummern davor stimmen bleiben
            block = self.document().findBlockByNumber(number)
            if lines[number] is None:
                cursor.setPosition(block.position())
                if block.next().isValid():
                    cursor.setPosition(block.next().position(), QTextCursor.MoveMode.KeepAnchor)
                else:
                    cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
                    cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                changed += 1
            elif lines[number] != block.text():
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(lines[number])
                changed += 1
        cursor.endEditBlock()
        block = self.document().findBlockByNumber(min(line, self.blockCount() - 1))
        place = QTextCursor(block)
        place.setPosition(block.position() + min(column, block.length() - 1))
        self.setTextCursor(place)
        return changed

    def show_stop(self, line: int) -> None:
        self.stop_line = line
        if line >= 0:
            self.setTextCursor(QTextCursor(self.document().findBlockByNumber(line)))
            self.centerCursor()
        self._mark_line()

    def show_problems(self, lines: list[int]) -> None:
        self.problem_lines = lines
        self._mark_line()

    def gutter_pressed(self, event) -> None:
        if not self.marks_on or event.position().x() > MARK_WIDTH:
            return
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid() and top + self.blockBoundingRect(block).height() <= event.position().y():
            top += self.blockBoundingRect(block).height()
            block = block.next()
        if not block.isValid():
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.gutter_menu.emit(block.blockNumber(), event.globalPosition().toPoint())
        else:
            self.gutter_clicked.emit(block.blockNumber())

    def _fit_gutter(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))
        self._fit_picture()

    def paint_gutter(self, event) -> None:
        painter = QPainter(self.gutter)
        if "background" in self.colors:
            painter.fillRect(event.rect(), QColor(self.colors["background"]))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                bright = block.blockNumber() == current
                if self.numbers_on:
                    painter.setPen(QColor(self.colors.get("text", "")) if bright and "text" in self.colors
                                   else self.palette().text().color() if bright else QColor(self.colors["gutter"]))
                    painter.drawText(0, top, self.gutter.width() - 8, self.fontMetrics().height(),
                                     Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
                if self.marks_on:
                    self._paint_mark(painter, block, top)
            block = block.next()
            top = bottom

    def _paint_mark(self, painter: QPainter, block, top: int) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = top + self.fontMetrics().height() // 2
        mark = block.userData()
        if isinstance(mark, BreakMark):
            painter.setPen(QColor(BREAK_COLOR))
            painter.setBrush(QColor(BREAK_COLOR) if mark.enabled else Qt.BrushStyle.NoBrush)
            if mark.message:  # Logpunkt: Raute wie in VS Code
                painter.drawPolygon(QPolygon([QPoint(9, middle - 6), QPoint(15, middle), QPoint(9, middle + 6), QPoint(3, middle)]))
            else:
                painter.drawEllipse(QPoint(9, middle), 6, 6)
            if mark.condition:
                painter.setPen(QColor("#FFFFFF"))
                painter.drawLine(6, middle, 12, middle)
        if block.blockNumber() == self.stop_line:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(STOP_COLOR))
            painter.drawPolygon(QPolygon([QPoint(3, middle - 5), QPoint(10, middle - 5), QPoint(16, middle), QPoint(10, middle + 5),
                                          QPoint(3, middle + 5)]))
        painter.restore()

    def _line_selection(self, cursor: QTextCursor, color: str) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(color))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = cursor
        selection.cursor.clearSelection()
        return selection

    def _mark_line(self) -> None:
        selections = [self._line_selection(self.textCursor(), self.colors["line"])] if self.line_on else []
        if self.stop_line >= 0:
            selections.append(self._line_selection(QTextCursor(self.document().findBlockByNumber(self.stop_line)),
                                                   self.colors.get("stop", "#5A4B00")))
        for line in self.problem_lines:
            selection = QTextEdit.ExtraSelection()
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            selection.format.setUnderlineColor(QColor(BREAK_COLOR))
            selection.cursor = QTextCursor(self.document().findBlockByNumber(line))
            selection.cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            selections.append(selection)
        self.setExtraSelections(selections)
        self.gutter.update()

    # --- Tippen ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        popup_open = self.completer is not None and self.completer.popup().isVisible()
        if popup_open and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Escape):
            event.ignore()
            return
        if event.key() == Qt.Key.Key_Tab:
            self.indent(False)
        elif event.key() == Qt.Key.Key_Backtab:
            self.indent(True)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            self.insertPlainText("\n" + self._next_indent())
        else:
            super().keyPressEvent(event)
            self._complete()

    def _next_indent(self) -> str:
        line = self.textCursor().block().text()
        indent = line[:len(line) - len(line.lstrip())]
        return indent + " " * TAB_WIDTH if line.rstrip().endswith((":", "{", "(", "[")) else indent

    def indent(self, back: bool) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection() and not back:
            cursor.insertText(" " * TAB_WIDTH)
            return
        lines = self._selected_blocks()
        cursor.beginEditBlock()
        for number in lines:
            line = QTextCursor(self.document().findBlockByNumber(number))
            if back:
                text = line.block().text()
                for _ in range(min(TAB_WIDTH, len(text) - len(text.lstrip(" ")))):
                    line.deleteChar()
            else:
                line.insertText(" " * TAB_WIDTH)
        cursor.endEditBlock()

    def _selected_blocks(self) -> list[int]:
        cursor = self.textCursor()
        document = self.document()
        first = document.findBlock(cursor.selectionStart()).blockNumber()
        end = document.findBlock(cursor.selectionEnd())
        last = end.blockNumber()
        if cursor.hasSelection() and cursor.selectionEnd() == end.position() and last > first:
            last -= 1
        return list(range(first, last + 1))

    def use_completions(self, words: list[str]) -> None:
        self.completer = QCompleter(QStringListModel(words), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setWidget(self)
        self.completer.activated.connect(self._insert_completion)

    def _word(self) -> str:
        line = self.textCursor().block().text()[:self.textCursor().positionInBlock()]
        return re.search(r"[\w-]*$", line).group()

    def _complete(self) -> None:
        if self.completer is None:
            return
        word = self._word()
        if len(word) < 3:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(word)
        rect = self.cursorRect()
        rect.setWidth(320)
        rect.translate(self.gutter_width(), 0)
        self.completer.complete(rect)

    def _insert_completion(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(self._word()))
        cursor.insertText(text)
        self.setTextCursor(cursor)

    # --- Zeilen-Befehle -------------------------------------------------------------
    def duplicate_line(self) -> None:
        cursor = self.textCursor()
        text = cursor.block().text()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertText("\n" + text)

    def move_line(self, step: int) -> None:
        lines = self._selected_blocks()
        target = (lines[0] - 1) if step < 0 else (lines[-1] + 1)
        if not 0 <= target < self.blockCount():
            return
        texts = self.toPlainText().split("\n")
        moved = texts[lines[0]:lines[-1] + 1]
        del texts[lines[0]:lines[-1] + 1]
        texts[lines[0] + step:lines[0] + step] = moved
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText("\n".join(texts))
        cursor.endEditBlock()
        cursor.setPosition(self.document().findBlockByNumber(lines[0] + step).position())
        self.setTextCursor(cursor)

    def transform_selection(self, change) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.insertText(change(cursor.selectedText().replace("\u2029", "\n")))

    def sort_lines(self) -> None:
        lines = self._selected_blocks()
        texts = self.toPlainText().split("\n")
        texts[lines[0]:lines[-1] + 1] = sorted(texts[lines[0]:lines[-1] + 1], key=str.lower)
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText("\n".join(texts))

    def go_to_line(self, number: int) -> None:
        block = self.document().findBlockByNumber(max(0, number - 1))
        self.setTextCursor(QTextCursor(block))
        self.centerCursor()

    def zoom(self, step: int) -> None:
        self.text_size = min(MAX_FONT, max(MIN_FONT, self.text_size + step))
        self._style_text()
        self._apply_tab_stop()
        self._fit_gutter()
        self.zoomed.emit(self.text_size)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
        else:
            super().wheelEvent(event)


@dataclass
class Tab:
    editor: CodeEditor
    path: str = ""
    encoding: str = "utf-8"
    newline: str = "\r\n"
    saved_text: str = ""
    image: bool = False

    @property
    def modified(self) -> bool:
        return not self.image and self.editor.toPlainText() != self.saved_text

    @property
    def title(self) -> str:
        return (os.path.basename(self.path) or "Unbenannt") + (" ●" if self.modified else "")


class FindBar(QWidget):
    def __init__(self, panel: "EditorPanel"):
        super().__init__()
        self.panel = panel
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        self.find = QLineEdit(placeholderText="Suchen")
        self.replace = QLineEdit(placeholderText="Ersetzen durch")
        self.case = QCheckBox("Aa")
        self.case.setToolTip("Groß- und Kleinschreibung beachten")
        self.regex = QCheckBox(".*")
        self.regex.setToolTip("Regulärer Ausdruck")
        self.count = QLabel(objectName="Muted")
        self.find.textChanged.connect(self._update_count)
        self.find.returnPressed.connect(lambda: self.step(True))
        for widget in (self.find, self.replace):
            row.addWidget(widget, 1)
        row.addWidget(self.case)
        row.addWidget(self.regex)
        row.addWidget(self.count)
        for label, handler in (("Zurück", lambda: self.step(False)), ("Weiter", lambda: self.step(True)),
                               ("Ersetzen", self.replace_one), ("Alle", self.replace_all), ("Schließen", self.hide)):
            button = QPushButton(label, objectName="Secondary")
            button.clicked.connect(handler)
            row.addWidget(button)

    def pattern(self) -> re.Pattern | None:
        text = self.find.text()
        if not text:
            return None
        try:
            return re.compile(text if self.regex.isChecked() else re.escape(text),
                              0 if self.case.isChecked() else re.IGNORECASE)
        except re.error:
            return None

    def _update_count(self) -> None:
        pattern = self.pattern()
        editor = self.panel.editor()
        total = len(pattern.findall(editor.toPlainText())) if pattern and editor else 0
        self.count.setText(f"{total} Treffer" if self.find.text() else "")

    def step(self, forward: bool) -> bool:
        pattern, editor = self.pattern(), self.panel.editor()
        if pattern is None or editor is None:
            return False
        text = editor.toPlainText()
        cursor = editor.textCursor()
        matches = list(pattern.finditer(text))
        if not matches:
            return False
        if forward:
            match = next((m for m in matches if m.start() >= cursor.selectionEnd()), matches[0])
        else:
            match = next((m for m in reversed(matches) if m.end() <= cursor.selectionStart()), matches[-1])
        cursor.setPosition(match.start())
        cursor.setPosition(match.end(), QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        return True

    def replace_one(self) -> None:
        pattern, editor = self.pattern(), self.panel.editor()
        if pattern is None or editor is None:
            return
        cursor = editor.textCursor()
        if cursor.hasSelection() and pattern.fullmatch(cursor.selectedText()):
            cursor.insertText(self.replace.text())
        self.step(True)

    def replace_all(self) -> None:
        pattern, editor = self.pattern(), self.panel.editor()
        if pattern is None or editor is None:
            return
        cursor = editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        replacement = self.replace.text() if self.regex.isChecked() else self.replace.text().replace("\\", "\\\\")
        cursor.insertText(pattern.sub(replacement, editor.toPlainText()))


class EditorPanel(QWidget):
    """Tabs, Textfeld, Suche und Statuszeile. Offene Tabs samt Ungespeichertem überstehen jeden Neustart."""
    title_changed = Signal(str)

    def __init__(self, ctx, session_file: str = SESSION_FILE):
        super().__init__()
        self.ctx = ctx
        self.session = DATA_DIR / session_file
        self.tabs: list[Tab] = []
        self.wrap = False
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.bar = QTabBar()
        self.bar.setTabsClosable(True)
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.tabCloseRequested.connect(self.close_tab)
        self.bar.currentChanged.connect(self._show_tab)
        self.bar.tabMoved.connect(lambda old, new: self.tabs.insert(new, self.tabs.pop(old)))
        self.stack = QVBoxLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.findbar = FindBar(self)
        self.findbar.hide()
        self.status = QLabel(objectName="Muted")
        self.status.setContentsMargins(10, 3, 10, 3)
        box.addWidget(self.bar)
        box.addLayout(self.stack, 1)
        box.addWidget(self.findbar)
        box.addWidget(self.status)
        self.autosave = False
        self._saver = QTimer(self, singleShot=True, interval=AUTOSAVE_MS)
        self._saver.timeout.connect(self._autosave)
        self.setAcceptDrops(True)

    # --- Tabs ---------------------------------------------------------------------
    def editor(self) -> CodeEditor | None:
        index = self.bar.currentIndex()
        return self.tabs[index].editor if 0 <= index < len(self.tabs) else None

    def current(self) -> Tab | None:
        index = self.bar.currentIndex()
        return self.tabs[index] if 0 <= index < len(self.tabs) else None

    def new_tab(self, text: str = "", path: str = "", encoding: str = "utf-8", newline: str = "\r\n",
                saved: str | None = None) -> Tab:
        editor = CodeEditor(self.ctx.theme.mode)
        editor.setPlainText(text)
        editor.highlighter.set_language(language_of(path))
        self._apply_wrap(editor)
        tab = Tab(editor, path, encoding, newline, text if saved is None else saved)
        editor.textChanged.connect(lambda: self._changed(tab))
        editor.cursorPositionChanged.connect(self._update_status)
        self.tabs.append(tab)
        self.stack.addWidget(editor)
        editor.hide()
        index = self.bar.addTab(tab.title)
        self.bar.setCurrentIndex(index)
        self._show_tab(index)
        return tab

    def _show_tab(self, index: int) -> None:
        for number, tab in enumerate(self.tabs):
            tab.editor.setVisible(number == index)
        tab = self.current()
        if tab:
            tab.editor.setFocus()
            self.title_changed.emit(tab.path or "Unbenannt")
        self._update_status()

    def _changed(self, tab: Tab) -> None:
        if tab in self.tabs:
            self.bar.setTabText(self.tabs.index(tab), tab.title)
        self._update_status()
        self._saver.start()

    def close_tab(self, index: int) -> None:
        """Ungespeichertes geht nicht verloren: Es wandert in den Papierkorb des Editors, siehe restore_closed."""
        tab = self.tabs[index]
        if tab.modified and tab.editor.toPlainText().strip():
            self._remember_closed(tab)
        self.tabs.pop(index)
        self.bar.removeTab(index)
        self.stack.removeWidget(tab.editor)
        tab.editor.deleteLater()
        if not self.tabs:
            self.new_tab()
        self.save_session()

    def _remember_closed(self, tab: Tab) -> None:
        folder = DATA_DIR / "editor_closed"
        folder.mkdir(parents=True, exist_ok=True)
        name = (os.path.basename(tab.path) or "Unbenannt") + time.strftime(" %Y-%m-%d %H-%M-%S") + ".txt"
        (folder / name).write_text(tab.editor.toPlainText(), encoding="utf-8")

    def step_tab(self, step: int) -> None:
        if self.tabs:
            self.bar.setCurrentIndex((self.bar.currentIndex() + step) % len(self.tabs))

    def unsaved_tabs(self) -> list[Tab]:
        return [tab for tab in self.tabs if tab.modified and tab.editor.toPlainText().strip()]

    def _autosave(self) -> None:
        if self.autosave:
            for tab in self.tabs:
                if tab.path and tab.modified:
                    self.save(tab)
        self.save_session()

    # --- Dateien ------------------------------------------------------------------
    def open_file(self, path: str) -> Tab | None:
        path = os.path.normpath(path)
        known = next((tab for tab in self.tabs if tab.path == path), None)
        if known:
            self.bar.setCurrentIndex(self.tabs.index(known))
            return known
        if os.path.splitext(path)[1].lower() in IMAGE_TYPES:
            current = self.current()
            tab = self.new_tab("", path)
            if tab.editor.show_image(path):
                tab.image = True
                if current and not current.path and not current.editor.toPlainText():
                    self.close_tab(self.tabs.index(current))
                self._remember_recent(path)
                self._update_status()
                return tab
            self.close_tab(self.tabs.index(tab))
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            self.status.setText(f"Nicht lesbar: {exc.strerror or exc}")
            return None
        text, encoding = decode(raw)
        newline = line_ending(text)
        text = text.replace("\r\n", "\n")
        current = self.current()
        tab = self.new_tab(text, path, encoding, newline)
        if current and not current.path and not current.editor.toPlainText():
            self.close_tab(self.tabs.index(current))
        self._remember_recent(path)
        return tab

    def save(self, tab: Tab | None = None, path: str = "") -> bool:
        tab = tab or self.current()
        if tab is None or tab.image or not (path or tab.path):
            return False
        target = path or tab.path
        text = tab.editor.toPlainText()
        try:
            Path(target).write_bytes(text.replace("\n", tab.newline).encode(tab.encoding, "replace"))
        except OSError as exc:
            self.status.setText(f"Nicht gespeichert: {exc.strerror or exc}")
            return False
        tab.path, tab.saved_text = os.path.normpath(target), text
        tab.editor.highlighter.set_language(language_of(tab.path))
        self.bar.setTabText(self.tabs.index(tab), tab.title)
        self.title_changed.emit(tab.path)
        self._remember_recent(tab.path)
        self.save_session()
        self._update_status()
        return True

    def recent_files(self) -> list[str]:
        return self._read_session().get("recent", [])

    def _remember_recent(self, path: str) -> None:
        self._recent = [path] + [p for p in self.recent_files() if p != path][:14]

    def _read_session(self) -> dict:
        try:
            return json.loads(self.session.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_session(self) -> None:
        tabs = [{"path": tab.path, "text": tab.editor.toPlainText() if tab.modified else None,
                 "encoding": tab.encoding, "newline": tab.newline} for tab in self.tabs]
        data = {"tabs": tabs, "current": self.bar.currentIndex(), "autosave": self.autosave,
                "recent": getattr(self, "_recent", None) or self.recent_files()}
        self.session.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.session.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.session)

    def restore_session(self) -> None:
        data = self._read_session()
        self.autosave = bool(data.get("autosave"))
        for item in data.get("tabs", []):
            path, draft = item.get("path", ""), item.get("text")
            if path and os.path.isfile(path):
                tab = self.open_file(path)
                if tab and draft is not None:
                    tab.editor.setPlainText(draft)
            elif draft is not None:  # die Datei gibt es nicht mehr: der Text bleibt als Entwurf
                self.new_tab(draft, "", item.get("encoding", "utf-8"), item.get("newline", "\r\n"), saved="")
        if not self.tabs:
            self.new_tab()
        self.bar.setCurrentIndex(min(data.get("current", 0), len(self.tabs) - 1))

    # --- Anzeige -------------------------------------------------------------------
    def toggle_wrap(self) -> None:
        self.wrap = not self.wrap
        for tab in self.tabs:
            self._apply_wrap(tab.editor)

    def _apply_wrap(self, editor: CodeEditor) -> None:
        editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if self.wrap
                               else QTextOption.WrapMode.NoWrap)

    def _update_status(self) -> None:
        tab = self.current()
        if tab is None:
            return
        cursor = tab.editor.textCursor()
        text = tab.editor.toPlainText()
        selected = len(cursor.selectedText())
        if tab.image:
            self.status.setText(f"Bild   ·   {tab.editor.picture.width()} × {tab.editor.picture.height()} Pixel")
            return
        parts = [f"Zeile {cursor.blockNumber() + 1}, Spalte {cursor.positionInBlock() + 1}",
                 f"{len(text)} Zeichen, {len(text.split())} Wörter"]
        if selected:
            parts.append(f"{selected} markiert")
        parts += [language_of(tab.path), tab.encoding.upper(), "CRLF" if tab.newline == "\r\n" else "LF",
                  f"{tab.editor.text_size} pt"]
        self.status.setText("   ·   ".join(parts))

    def show_find(self) -> None:
        self.findbar.show()
        editor = self.editor()
        if editor and editor.textCursor().hasSelection():
            self.findbar.find.setText(editor.textCursor().selectedText())
        self.findbar.find.setFocus()
        self.findbar.find.selectAll()

    def ask_line(self) -> None:
        editor = self.editor()
        if editor is None:
            return
        number, ok = QInputDialog.getInt(self, "Gehe zu Zeile", "Zeile:", editor.textCursor().blockNumber() + 1,
                                         1, editor.blockCount())
        if ok:
            editor.go_to_line(number)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_file(url.toLocalFile())
        event.acceptProposedAction()

    def bind_shortcuts(self, window) -> None:
        """Tastenkürzel, die Notepad, Notepad++ und VS Code gemeinsam haben."""
        actions = (
            ("Ctrl+F", self.show_find), ("Ctrl+H", self.show_find), ("Ctrl+G", self.ask_line),
            ("F3", lambda: self.findbar.step(True)), ("Shift+F3", lambda: self.findbar.step(False)),
            ("Ctrl+D", lambda: self.editor() and self.editor().duplicate_line()),
            ("Alt+Up", lambda: self.editor() and self.editor().move_line(-1)),
            ("Alt+Down", lambda: self.editor() and self.editor().move_line(1)),
            ("Ctrl+U", lambda: self.editor() and self.editor().transform_selection(str.lower)),
            ("Ctrl+Shift+U", lambda: self.editor() and self.editor().transform_selection(str.upper)),
            ("Ctrl+=", lambda: self.editor() and self.editor().zoom(1)),
            ("Ctrl+-", lambda: self.editor() and self.editor().zoom(-1)),
            ("Alt+Z", self.toggle_wrap),
            ("Escape", self.findbar.hide),
        )
        for sequence, handler in actions:
            QShortcut(QKeySequence(sequence), window, activated=handler)
