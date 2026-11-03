"""Räumt PowerShell-Code auf. PowerShell zerlegt ihn selbst in Bausteine, der Formatierer ändert nur den Platz dazwischen.

Texte, Here-Strings und Kommentare bleiben dadurch Zeichen für Zeichen erhalten. Zeilen wandern nicht: Sie werden einzeln
ersetzt, damit Haltepunkte an ihrer Zeile bleiben. Vor dem Übernehmen liest PowerShell das Ergebnis noch einmal; weichen die
Bausteine ab, bleibt der Code unberührt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, Signal
from shiboken6 import isValid

from core.paths import DATA_DIR
from studio.debugger import quoted

WORK_FILE = "studio_format.ps1"
TOKEN_SCRIPT = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$errors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(@PATH@, [ref]$tokens, [ref]$errors)
$list = @($tokens | ForEach-Object { @{ k = $_.Kind.ToString(); t = $_.Text; s = $_.Extent.StartOffset; e = $_.Extent.EndOffset
    l = $_.Extent.StartLineNumber; f = $_.TokenFlags.ToString() } })
ConvertTo-Json -Compress -Depth 3 @{ errors = @($errors).Count - [int]($null -eq $errors); tokens = $list }
"""
OPENERS = {"LCurly", "AtCurly", "LParen", "AtParen", "DollarParen", "LBracket"}
CLOSERS = {"RCurly", "RParen", "RBracket"}
TIGHT_AFTER = {"LParen", "AtParen", "DollarParen", "LBracket"}
TIGHT_BEFORE = {"RParen", "RBracket"}
NO_SPACE_OPERATORS = {"Dot", "DotDot", "ColonColon", "QuestionDot"}
SPACED_KEYWORDS = {"if", "elseif", "while", "for", "foreach", "switch", "catch", "until"}
LINE_ENDS = {"NewLine", "EndOfInput"}


@dataclass(frozen=True)
class FormatOptions:
    indent: int = 4
    tabs: bool = False
    spacing: bool = True
    casing: bool = True
    blank_lines: int = 2


def has(token: dict, flag: str) -> bool:
    return flag in token["f"]


def is_operator(token: dict) -> bool:
    """Plus und Minus können auch ein Vorzeichen sein; dort entscheidet weiter, was im Code steht."""
    return ((has(token, "BinaryOperator") or has(token, "AssignmentOperator")) and not has(token, "UnaryOperator")
            and token["k"] not in NO_SPACE_OPERATORS)


def space_between(before: dict, token: dict, gap: str) -> str:
    kind, previous = token["k"], before["k"]
    if kind == "Comment":
        return gap or " "  # ausgerichtete Kommentare bleiben stehen
    if kind in ("Comma", "Semi"):
        return ""
    if previous in ("Comma", "Semi") or kind == "Pipe" or previous == "Pipe":
        return " "
    if previous in TIGHT_AFTER or kind in TIGHT_BEFORE:
        return ""
    if is_operator(token) or is_operator(before):
        return " "
    if kind == "LCurly" and (previous in ("RParen", "RBracket", "Identifier", "Generic") or has(before, "Keyword")):
        return " "
    if previous == "RCurly" and has(token, "Keyword"):
        return " "
    if kind == "LParen" and has(before, "Keyword") and before["t"].lower() in SPACED_KEYWORDS:
        return " "
    return " " if gap else ""


def python_offsets(text: str) -> list[int] | None:
    """PowerShell zählt Zeichen wie .NET, Emojis also doppelt. None heißt: Es gibt nichts umzurechnen."""
    if all(ord(character) < 0x10000 for character in text):
        return None
    offsets: list[int] = []
    for index, character in enumerate(text):
        offsets += [index] * (2 if ord(character) >= 0x10000 else 1)
    return offsets + [len(text)]


def spelled(token: dict, options: FormatOptions, commands: dict[str, str]) -> str:
    if options.casing and has(token, "Keyword"):
        return token["t"].lower()
    if options.casing and has(token, "CommandName"):
        return commands.get(token["t"].lower(), token["t"])
    return token["t"]


def format_lines(text: str, tokens: list[dict], options: FormatOptions, commands: dict[str, str] | None = None) -> list[str | None]:
    """Der neue Text jeder alten Zeile; None heißt: Diese Leerzeile fällt weg."""
    commands = commands or {}
    source = text.split("\n")
    result: list[str | None] = [line.strip() if not line.strip() else None for line in source]
    offsets = python_offsets(text)
    place = (lambda value: offsets[min(value, len(offsets) - 1)]) if offsets else (lambda value: value)
    unit = "\t" if options.tabs else " " * options.indent
    stack: list[bool] = []  # je offene Klammer: rückt sie ein? Zwei auf einer Zeile rücken nur einmal ein
    opened_on = -1
    before: dict | None = None
    continued = False
    end = 0
    for number, token in enumerate(tokens):
        kind = token["k"]
        start, stop = place(token["s"]), place(token["e"])
        line = token["l"] - 1
        if kind in LINE_ENDS:
            if before is not None and before["k"] == "Pipe":
                continued = True
            elif before is not None:
                continued = False
            before = None
            end = stop
            continue
        word = spelled(token, options, commands)
        if before is None and result[line] is None:
            closing = 0
            while tokens[number + closing]["k"] in CLOSERS:
                closing += 1
            level = sum(stack[:len(stack) - closing] if closing else stack)
            extra = 1 if continued or kind == "Pipe" else 0
            result[line] = unit * (level + extra)
        elif before is not None:
            gap = text[end:start]
            result[line] = (result[line] or "") + (space_between(before, token, gap) if options.spacing else gap)
        if kind == "LineContinuation":
            result[line] = (result[line] or "") + "`"
            before, continued, end = None, True, stop
            continue
        pieces = word.split("\n")
        result[line] = (result[line] or "") + pieces[0]
        for offset, piece in enumerate(pieces[1:], 1):  # mehrzeiliger Text oder Kommentar: bleibt, wie er ist
            result[line + offset] = piece
        line += len(pieces) - 1
        if kind in CLOSERS and stack and stack.pop() and opened_on == line:
            opened_on = -1  # eine schon geschlossene Klammer zählt nicht: if ($a) { rückt wegen der geschweiften ein
        if kind in OPENERS:
            stack.append(opened_on != line)
            opened_on = line
        before, end = token, stop
    return thin_blank_lines(result, options.blank_lines)


def thin_blank_lines(lines: list[str | None], allowed: int) -> list[str | None]:
    run = 0
    for index, line in enumerate(lines):
        run = run + 1 if line == "" else 0
        if run > allowed:
            lines[index] = None
    last = len(lines) - 1
    while last > 0 and lines[last] in ("", None):  # am Dateiende bleibt genau eine leere Zeile
        lines[last] = None
        last -= 1
    if last + 1 < len(lines):
        lines[last + 1] = ""
    return lines


def only_lines(lines: list[str | None], text: str, first: int, last: int) -> list[str | None]:
    """Auswahl formatieren: Alles andere bleibt, wie es war, und keine Zeile fällt weg."""
    source = text.split("\n")
    return [(source[index] if line is None else line) if first <= index <= last else source[index]
            for index, line in enumerate(lines)]


def joined(lines: list[str | None]) -> str:
    return "\n".join(line for line in lines if line is not None)


def same_code(old: list[dict], new: list[dict]) -> bool:
    """Gleiche Bausteine in gleicher Folge; nur die Schreibweise von Befehlen und Schlüsselwörtern darf abweichen."""
    def key(token: dict) -> tuple:
        loose = has(token, "Keyword") or has(token, "CommandName")
        return token["k"], token["t"].lower() if loose else token["t"].replace("\r\n", "\n")
    return [key(token) for token in old if token["k"] != "NewLine"] == [key(token) for token in new if token["k"] != "NewLine"]


class ScriptFormatter(QObject):
    """Zerlegen, formatieren, zur Sicherheit neu zerlegen. Das dauert etwa eine Sekunde, also im Hintergrund."""
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, shell: str, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.process: QProcess | None = None
        self.text = ""
        self.tokens: list[dict] = []
        self.lines: list[str | None] = []
        self.options = FormatOptions()
        self.commands: dict[str, str] = {}
        self.selection: tuple[int, int] | None = None

    @property
    def busy(self) -> bool:
        return self.process is not None

    def start(self, text: str, options: FormatOptions, commands: dict[str, str], selection: tuple[int, int] | None = None) -> None:
        if self.busy:
            return
        self.text, self.options, self.commands, self.selection = text, options, commands, selection
        self._ask(text, self._read_first)

    def _ask(self, text: str, handler) -> None:
        path = DATA_DIR / WORK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8-sig"))  # keine Zeilenumbrüche umschreiben, sonst stimmen die Positionen nicht
        self.process = process = QProcess(self)
        process.finished.connect(lambda *_: self._answer(handler))
        process.errorOccurred.connect(self._broken)
        process.start(self.shell, ["-NoProfile", "-NonInteractive", "-Command", TOKEN_SCRIPT.replace("@PATH@", quoted(str(path)))])

    def _broken(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._drop()
            self.failed.emit(f"{self.shell} ließ sich nicht starten.")

    def _drop(self) -> QProcess | None:
        process, self.process = self.process, None
        if process is not None and isValid(process):
            process.deleteLater()
            return process
        return None

    def _answer(self, handler) -> None:
        process = self._drop()
        output = bytes(process.readAllStandardOutput()).decode("utf-8-sig", "replace") if process else ""
        try:
            answer = json.loads(output)
        except ValueError:
            self.failed.emit("PowerShell hat den Code nicht zerlegt.")
            return
        handler(answer)

    def _read_first(self, answer: dict) -> None:
        if answer["errors"]:
            self.failed.emit("Erst die Syntaxfehler beheben, dann lässt sich formatieren.")
            return
        self.tokens = answer["tokens"]
        self.lines = format_lines(self.text, self.tokens, self.options, self.commands)
        if self.selection:
            self.lines = only_lines(self.lines, self.text, *self.selection)
        self._ask(joined(self.lines), self._verify)

    def _verify(self, answer: dict) -> None:
        if answer["errors"] or not same_code(self.tokens, answer["tokens"]):
            self.failed.emit("Formatieren hätte den Code verändert, deshalb bleibt er, wie er ist.")
            return
        self.finished.emit(self.lines)
