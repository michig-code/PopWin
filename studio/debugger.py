"""Debugger des PowerShell Studios. Er nutzt den Debugger von PowerShell selbst.

Die Konsole lädt beim Start ein kleines Skript. Sein `prompt` läuft jedes Mal, wenn PowerShell auf eine Eingabe
wartet, also auch an jedem Haltepunkt, und legt dort Zeile, Variablen und Aufrufliste als Datei ab. So bleibt die Konsole
frei von Hilfsausgaben, und Schritte sind nur die Befehle, die PowerShell im Debugger ohnehin kennt (s, v, o, c, q).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from shiboken6 import isValid

from core.paths import DATA_DIR

HOST_FILE = "studio_host.ps1"
PLAN_FILE = "studio_plan.json"
STATE_FILE = "studio_state.json"
POLL_MS = 120
VALUE_LENGTH = 300
STEP_INTO, STEP_OVER, STEP_OUT, CONTINUE, QUIT = "s", "v", "o", "c", "q"

HOST_SCRIPT = r"""
$global:StudioFolder = '@FOLDER@'
$global:StudioKnown = @((Get-Variable -Scope Global).Name) + @('args', 'input', 'PSBoundParameters', 'MyInvocation',
    'PSCommandPath', 'PSScriptRoot', 'PSCmdlet', 'PSDebugContext', 'foreach', 'switch', 'this', '_', 'StudioKnown',
    'StudioFolder', 'StudioPrompt', 'StudioWatches', 'StudioNumber', 'CurrentlyExecutingCommand', 'StackTrace', '?', '^', '$',
    'state', 'where', 'path', 'type', 'value', 'watch')
$global:StudioWatches = @()
$global:StudioNumber = 0
$global:StudioPrompt = $function:prompt

function global:Get-StudioText($value) {
    if ($null -eq $value) { return '$null' }
    try {
        if ($value -is [array]) { $text = '[' + (($value | Select-Object -First 20) -join ', ') + ']' } else { $text = "$value" }
    } catch { $text = '?' }
    if ($text.Length -gt @LENGTH@) { $text = $text.Substring(0, @LENGTH@) + '...' }
    return $text
}

function global:Use-StudioPlan {
    $plan = [IO.File]::ReadAllText((Join-Path $global:StudioFolder '@PLAN@')) | ConvertFrom-Json
    $global:StudioWatches = @($plan.watches)
    Get-PSBreakpoint | Remove-PSBreakpoint
    foreach ($mark in $plan.breakpoints) {
        $options = @{ Script = $mark.file; Line = $mark.line }
        if ($mark.message) {
            $options.Action = [scriptblock]::Create("Write-Host `"$($mark.message -replace '"', '`"')`" -ForegroundColor Cyan")
        } elseif ($mark.condition) {
            $options.Action = [scriptblock]::Create("if ($($mark.condition)) { break }")
        }
        Set-PSBreakpoint @options | Out-Null
    }
}

function global:prompt {
    try {
        $global:StudioNumber++
        $state = @{ number = $global:StudioNumber; paused = $false; file = ''; line = 0; variables = @(); stack = @(); watches = @() }
        if (Test-Path variable:PSDebugContext) {
            $where = $PSDebugContext.InvocationInfo
            $state.paused = $true
            $state.file = $where.ScriptName
            $state.line = $where.ScriptLineNumber
            $state.variables = @(Get-Variable -Scope 1 | Where-Object { $global:StudioKnown -notcontains $_.Name } |
                Sort-Object Name | ForEach-Object {
                    $type = if ($null -eq $_.Value) { '' } else { $_.Value.GetType().Name }
                    @{ name = $_.Name; type = $type; value = Get-StudioText $_.Value } })
            $state.stack = @($Host.Runspace.Debugger.GetCallStack() | ForEach-Object {
                @{ name = $_.FunctionName; file = $_.ScriptName; line = $_.ScriptLineNumber } })
            $state.watches = @(foreach ($watch in $global:StudioWatches) {
                try { $value = Get-StudioText (Invoke-Expression $watch) } catch { $value = "Fehler: $($_.Exception.Message)" }
                @{ expression = $watch; value = $value } })
        }
        $path = Join-Path $global:StudioFolder '@STATE@'
        [IO.File]::WriteAllText("$path.tmp", (ConvertTo-Json $state -Depth 4 -Compress))
        Move-Item -LiteralPath "$path.tmp" -Destination $path -Force
    } catch { }
    & $global:StudioPrompt
}
"""


@dataclass
class BreakPoint:
    file: str
    line: int  # ab 1 gezählt, wie in PowerShell
    enabled: bool = True
    condition: str = ""
    message: str = ""  # Logpoint: schreibt etwas in die Konsole, statt anzuhalten


def quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def host_script() -> str:
    text = HOST_SCRIPT.replace("@FOLDER@", str(DATA_DIR).replace("'", "''")).replace("@PLAN@", PLAN_FILE)
    return text.replace("@STATE@", STATE_FILE).replace("@LENGTH@", str(VALUE_LENGTH))


def console_command(shell: str) -> list[str]:
    """Die Konsole lädt zuerst das Hilfsskript; danach sieht sie aus wie jede andere PowerShell."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / HOST_FILE
    path.write_text(host_script(), encoding="utf-8-sig")
    return [shell, "-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", f". {quoted(str(path))}; Clear-Host"]


def run_command(script: str, arguments: str = "") -> str:
    """Punkt-Aufruf wie in der ISE: Variablen und Funktionen des Skripts bleiben in der Konsole erhalten."""
    return f"Use-StudioPlan; . {quoted(script)}" + (f" {arguments}" if arguments.strip() else "")


def write_plan(breakpoints: list[BreakPoint], watches: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    active = [asdict(mark) for mark in breakpoints if mark.enabled and mark.file]
    (DATA_DIR / PLAN_FILE).write_text(json.dumps({"breakpoints": active, "watches": watches}), encoding="utf-8")


def read_state(path: Path | None = None) -> dict | None:
    try:
        state = json.loads((path or DATA_DIR / STATE_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None  # PowerShell ersetzt die Datei gerade
    return state if isinstance(state, dict) and "paused" in state else None


class DebugSession(QObject):
    """Merkt, wann PowerShell anhält oder weiterläuft, und sagt dem Fenster Bescheid."""
    paused = Signal(dict)
    resumed = Signal()
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state: dict | None = None
        self.running = False
        self._seen = 0
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    @property
    def is_paused(self) -> bool:
        return bool(self.state and self.state["paused"])

    def started(self) -> None:
        self.running = True
        self.state = None
        self.resumed.emit()

    def check(self) -> None:
        state = read_state()
        if state is None or state.get("number", 0) == self._seen:
            return
        self._seen = state.get("number", 0)
        self.state = state
        if state["paused"]:
            self.running = True
            self.paused.emit(state)
        else:
            self.running = False
            self.idle.emit()


def outline(text: str) -> list[tuple[str, int]]:
    """(Name, Zeile ab 1) aller Funktionen und Klassen."""
    pattern = re.compile(r"^\s*(?:function|filter|workflow|class)\s+([\w:.-]+)", re.I)
    return [(match[1], number) for number, line in enumerate(text.splitlines(), 1) if (match := pattern.match(line))]


PARSE_SCRIPT = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$errors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(@PATH@, [ref]$tokens, [ref]$errors)
ConvertTo-Json -Compress @($errors | ForEach-Object { @{ line = $_.Extent.StartLineNumber; column = $_.Extent.StartColumnNumber; message = $_.Message } })
"""


def parse_problems(output: str) -> list[dict]:
    try:
        problems = json.loads(output)
    except ValueError:
        return []
    if isinstance(problems, dict):  # PowerShell 5 packt eine Liste mit einem Eintrag aus
        problems = [problems]
    return [problem for problem in problems or [] if isinstance(problem, dict)]


class SyntaxChecker(QObject):
    """Prüft das Skript im Hintergrund, ohne es auszuführen."""
    finished = Signal(list)

    def __init__(self, shell: str, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.process: QProcess | None = None

    def check(self, text: str) -> None:
        if self.process is not None:
            return  # die nächste Pause im Tippen prüft den neuen Stand
        path = DATA_DIR / "studio_check.ps1"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8-sig")
        self.process = process = QProcess(self)
        process.finished.connect(self._done)
        process.errorOccurred.connect(self._reset)
        process.start(self.shell, ["-NoProfile", "-NonInteractive", "-Command", PARSE_SCRIPT.replace("@PATH@", quoted(str(path)))])

    def _reset(self) -> None:
        process, self.process = self.process, None
        if process is not None and isValid(process):  # beim Schließen des Fensters ist er womöglich schon weg
            process.deleteLater()

    def _done(self) -> None:
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        self._reset()
        self.finished.emit(parse_problems(output))
