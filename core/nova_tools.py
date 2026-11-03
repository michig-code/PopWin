"""Was Nova am PC tun darf. Die Entscheidung, was gefragt wird, liegt hier und nicht bei der KI."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from core import ai_actions, nova_artifacts, win_api
from core.dev import is_dev
from core.gemini_client import client_for
from core.nova_connections import Connections
from core.paths import FROZEN, ROOT, app_command

log = logging.getLogger(__name__)

FREE, ASK, ALWAYS = "frei", "fragen", "immer fragen"
DEV = "dev"  # nur im Entwickler-Build, etwa die OVH-Anbindung
MAX_READ = 20000
TOOL_SECONDS = 60
MAX_OUTPUT = 4000
COMMAND_SECONDS = 60
SHOT_SIZES = {"sparsam": 1280, "qualitaet": 1920}
JPEG_QUALITY = 75
GRID = 1000
SETTLE_SECONDS = 0.25
_KEY_NAMES = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
              "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
              "pagedown": 0x22, "ctrl": 0x11, "strg": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
_KEY_NAMES.update({f"f{number}": 0x6F + number for number in range(1, 13)})

MOUSE_DOWN = {"left": 0x0002, "right": 0x0008}
MOUSE_UP = {"left": 0x0004, "right": 0x0010}
MOUSE_ABSOLUTE = 0x8000 | 0x4000 | 0x0001  # ABSOLUTE, VIRTUALDESK, MOVE
KEY_UNICODE, KEY_UP = 0x0004, 0x0002
SM_VIRTUAL = (76, 77, 78, 79)  # links, oben, Breite, Höhe des gesamten Desktops


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str
    label: str  # steht im Chat, während Nova es benutzt
    description: str
    parameters: dict  # Name -> (Typ, Beschreibung)
    required: tuple[str, ...] = ()
    needs: str = ""  # Einstellung, die es erst erlaubt


_STRING, _NUMBER, _BOOL = "STRING", "INTEGER", "BOOLEAN"
TOOLS = (
    Tool("look_at_screen", FREE, "Schaut auf den Bildschirm",
         "Macht ein frisches Bildschirmfoto des Monitors, auf dem die Maus steht, und zeigt es dir.", {}, needs="allow_screen"),
    Tool("read_chess_board", FREE, "Liest das Schachbrett",
         "Liest ein 2D-Schachbrett vom Bildschirm und erkennt neue Züge. Kostet fast keine Tokens: Nimm bei Schach immer das hier, "
         "nie look_at_screen. Liefert Stellung, FEN und Zugrecht.",
         {"new_game": (_BOOL, "Wahr, wenn eine neue Partie beginnt"),
          "side_to_move": (_STRING, "white oder black, nur beim Einstieg mitten in eine Partie")}, needs="allow_screen"),
    Tool("list_windows", FREE, "Sieht nach, welche Fenster offen sind", "Listet die offenen Fenster mit Titel.", {}),
    Tool("remember", FREE, "Merkt sich etwas", "Merkt dir dauerhaft einen Fakt über den Host.",
         {"text": (_STRING, "Der Fakt")}, ("text",), needs="allow_memory"),
    Tool("forget", FREE, "Vergisst etwas", "Löscht Fakten aus dem Gedächtnis.",
         {"id_or_text": (_STRING, "Nummer oder Textteil")}, ("id_or_text",)),
    Tool("popwin_settings", FREE, "Liest die PopWin-Einstellungen", "Liefert alle PopWin-Einstellungen als Schlüssel=Wert.", {}),
    Tool("voices_list", FREE, "Sieht ihre Stimmen durch", "Listet die Stimmen, mit denen du sprechen kannst, und die jetzige.", {}),
    Tool("set_voice", FREE, "Ändert ihre Stimme",
         "Stellt deine Stimme ein: eine Stimme aus voices_list und/oder eine Sprechweise in Worten, etwa "
         "„ruhig und etwas langsamer“ oder „fröhlich, mit leichtem bayerischem Einschlag“. Leere Sprechweise heißt natürlich.",
         {"voice": (_STRING, "Name der Stimme, leer lässt sie"), "style": (_STRING, "Sprechweise, '-' setzt sie zurück"),
          "speak": (_BOOL, "Wahr schaltet das Vorlesen ein, falsch aus")}),
    Tool("open_target", ASK, "Öffnet etwas", "Startet ein Programm oder öffnet eine Datei, einen Ordner oder eine Webadresse.",
         {"target": (_STRING, "Programmname, Pfad oder URL")}, ("target",)),
    Tool("focus_window", ASK, "Holt ein Fenster nach vorn", "Bringt das Fenster, dessen Titel den Text enthält, nach vorn.",
         {"title": (_STRING, "Teil des Fenstertitels")}, ("title",), needs="allow_input"),
    Tool("read_file", ASK, "Liest eine Datei", f"Liest eine Textdatei (höchstens {MAX_READ} Zeichen).",
         {"path": (_STRING, "Dateipfad")}, ("path",)),
    Tool("list_folder", ASK, "Sieht in einen Ordner", "Listet den Inhalt eines Ordners.",
         {"path": (_STRING, "Ordnerpfad")}, ("path",)),
    Tool("clipboard_get", ASK, "Liest die Zwischenablage", "Liest den Text aus der Zwischenablage.", {}),
    Tool("clipboard_set", ASK, "Füllt die Zwischenablage", "Schreibt Text in die Zwischenablage.",
         {"text": (_STRING, "Der Text")}, ("text",)),
    Tool("change_popwin_setting", ASK, "Ändert eine PopWin-Einstellung", "Ändert eine PopWin-Einstellung.",
         {"key": (_STRING, "Schlüssel aus popwin_settings"), "value": (_STRING, "Neuer Wert")}, ("key", "value")),
    Tool("click", ASK, "Klickt", "Klickt auf eine Stelle im Raster 0 bis 1000 des letzten Bildschirmfotos.",
         {"x": (_NUMBER, "0 bis 1000 von links"), "y": (_NUMBER, "0 bis 1000 von oben"),
          "button": (_STRING, "left oder right"), "double": (_BOOL, "Doppelklick")}, ("x", "y"), needs="allow_input"),
    Tool("type_text", ASK, "Tippt Text", "Tippt Text in das aktive Fenster.",
         {"text": (_STRING, "Der Text")}, ("text",), needs="allow_input"),
    Tool("press_key", ASK, "Drückt Tasten", "Drückt eine Taste oder Kombination, zum Beispiel enter oder ctrl+s.",
         {"keys": (_STRING, "Tasten, mit + verbunden")}, ("keys",), needs="allow_input"),
    Tool("run_command", ALWAYS, "Führt einen Befehl aus", "Führt einen PowerShell-Befehl aus und liefert die Ausgabe.",
         {"command": (_STRING, "Der Befehl")}, ("command",)),
    Tool("write_file", ALWAYS, "Schreibt eine Datei", "Schreibt eine Textdatei oder hängt an sie an.",
         {"path": (_STRING, "Dateipfad"), "content": (_STRING, "Inhalt"), "append": (_BOOL, "Anhängen statt überschreiben")},
         ("path", "content")),
    Tool("artifact_create", FREE, "Legt ein Artefakt an",
         "Legt ein dauerhaftes Artefakt an, um dir eine Aufgabe über längere Zeit zu merken. Arten: aufgabe (Checkliste, "
         "Fälligkeit), notiz (freier Text), ueberwachung (wird regelmäßig geprüft), werkzeug (kleines Python-Skript, content ist "
         "der Code; was es mit print ausgibt, ist sein Ergebnis).",
         {"title": (_STRING, "Kurzer Titel"), "kind": (_STRING, "aufgabe, notiz, ueberwachung oder werkzeug"),
          "content": (_STRING, "Notiz oder bei werkzeug der Python-Code"),
          "steps": (_STRING, "Schritte der Checkliste, einer pro Zeile"), "due": (_STRING, "Fälligkeit als JJJJ-MM-TT"),
          "check": (_STRING, "Nur bei ueberwachung: eingebaute Prüfung, etwa ovh_domains"),
          "tool": (_STRING, "Nur bei ueberwachung: Kennung eines Werkzeugs, das die Prüfung macht"),
          "read_only": (_BOOL, "Nur bei werkzeug: wahr, wenn es nur liest und nichts ändert"),
          "connection": (_STRING, "Nur bei werkzeug: Name der Verbindung, deren Keys es mit secret('feld') bekommt")},
         ("title", "kind")),
    Tool("connection_request", FREE, "Richtet eine Verbindung ein",
         "Legt eine Verbindung zu einem Dienst an (etwa ovh, github, ein Wetterdienst) und zeigt dem Host Eingabefelder für die "
         "Keys. Der Host tippt sie selbst in PopWin ein; du siehst sie nie. Frag nie im Chat nach Keys. Werkzeuge mit dieser "
         "Verbindung bekommen die Werte mit secret('feld'), bei ovh zusätzlich ovh_client().",
         {"name": (_STRING, "Kurzer Name, Kleinbuchstaben, etwa ovh"), "fields": (_STRING, "Feldnamen, einer pro Zeile"),
          "help_url": (_STRING, "https-Seite, auf der man die Keys bekommt"), "note": (_STRING, "Kurzer Hinweis für den Host")},
         ("name", "fields")),
    Tool("connection_list", FREE, "Sieht die Verbindungen durch", "Listet die Verbindungen und welche Felder gesetzt sind.", {}),
    Tool("artifact_list", FREE, "Sieht die Artefakte durch", "Listet alle Artefakte mit Kennung, Art, Zustand und Fortschritt.", {}),
    Tool("artifact_read", FREE, "Liest ein Artefakt", "Liefert Notizen, Checkliste, Code und Verlauf eines Artefakts.",
         {"id": (_STRING, "Kennung oder Teil des Titels")}, ("id",)),
    Tool("artifact_update", FREE, "Pflegt ein Artefakt",
         "Ändert ein Artefakt: Notiz anhängen, Schritte abhaken oder hinzufügen, Zustand, Fälligkeit oder Code ändern.",
         {"id": (_STRING, "Kennung oder Teil des Titels"), "note": (_STRING, "Text, der an die Notizen angehängt wird"),
          "step_done": (_STRING, "Nummer ab 1 oder Text des Schritts, der erledigt ist"),
          "step_open": (_STRING, "Nummer oder Text eines Schritts, der wieder offen ist"),
          "add_steps": (_STRING, "Neue Schritte, einer pro Zeile"),
          "state": (_STRING, "offen, aktiv, pausiert oder erledigt"), "due": (_STRING, "Fälligkeit als JJJJ-MM-TT"),
          "title": (_STRING, "Neuer Titel"), "code": (_STRING, "Neuer Code eines Werkzeugs; die Freigabe gilt dann nicht mehr")},
         ("id",)),
    Tool("artifact_delete", ASK, "Löscht ein Artefakt", "Löscht ein Artefakt endgültig.",
         {"id": (_STRING, "Kennung oder Teil des Titels")}, ("id",)),
    Tool("artifact_schedule", ASK, "Plant eine Überwachung", "Legt fest, wie oft PopWin eine Überwachung prüft. 0 schaltet sie ab.",
         {"id": (_STRING, "Kennung oder Teil des Titels"), "hours": (_NUMBER, "Abstand in Stunden, 1 bis 168")}, ("id", "hours")),
    Tool("artifact_run_tool", ALWAYS, "Führt ein Werkzeug aus",
         "Führt ein Werkzeug-Artefakt aus und liefert seine Ausgabe. Der Host sieht vorher den Code.",
         {"id": (_STRING, "Kennung oder Teil des Titels")}, ("id",)),
    Tool("ovh_overview", ASK, "Sieht bei OVH nach",
         "Liest die Domains des Hosts bei OVH: Ablaufdatum, Nameserver, DNS-Einträge und Erreichbarkeit. Ändert nichts.",
         {"records": (_BOOL, "Wahr, um die DNS-Einträge einzeln aufzulisten")}, needs=DEV),
    Tool("ovh_dns_change", ALWAYS, "Ändert einen DNS-Eintrag bei OVH",
         "Legt einen DNS-Eintrag bei OVH an, ändert oder löscht ihn und lädt die Zone danach neu. Der Host sieht vorher alt → neu.",
         {"zone": (_STRING, "Domain, etwa example.de"), "subdomain": (_STRING, "Subdomain, leer oder @ für die Domain selbst"),
          "field_type": (_STRING, "A, AAAA, CNAME, MX, TXT, NS, SRV oder CAA"), "target": (_STRING, "Ziel des Eintrags"),
          "ttl": (_NUMBER, "TTL in Sekunden, 0 für den Standard"), "action": (_STRING, "set (Standard), add oder delete"),
          "artifact": (_STRING, "Kennung des Artefakts, in dessen Verlauf die Änderung landet")},
         ("zone", "field_type"), needs=DEV),
)
BY_NAME = {tool.name: tool for tool in TOOLS}
# Ein Werkzeug soll nie an Zugangsdaten kommen. Python kann alles, deshalb sieht der Host den Code vor jeder neuen Fassung.
FORBIDDEN_IN_TOOLS = ("keyring", "win32cred", "credread", "credential", "ovh_application", "ovh_consumer")


def available(tool: Tool, allowed: dict) -> bool:
    if tool.needs == DEV:
        return is_dev()
    return not tool.needs or bool(allowed.get(tool.needs))


def declarations(allowed: dict) -> list[dict]:
    """Nur was die Einstellungen erlauben: Was Nova nicht kennt, kann sie auch nicht versuchen."""
    result = []
    for tool in TOOLS:
        if not available(tool, allowed):
            continue
        entry = {"name": tool.name, "description": tool.description}
        if tool.parameters:
            properties = {key: {"type": kind, "description": text} for key, (kind, text) in tool.parameters.items()}
            entry["parameters"] = {"type": "OBJECT", "properties": properties, "required": list(tool.required)}
        result.append(entry)
    return [{"function_declarations": result}]


def approval_text(name: str, arguments: dict) -> str:
    """Was der Host vor dem Erlauben liest: immer der echte Inhalt, nie Novas Zusammenfassung."""
    if name == "write_file":
        action = "anhängen an" if arguments.get("append") else "schreiben nach"
        return f"Datei {action}:\n{arguments.get('path', '')}\n\n{str(arguments.get('content', ''))[:600]}"
    if name == "click":
        return f"Klicken bei {arguments.get('x')}, {arguments.get('y')} ({arguments.get('button', 'left')})"
    return "\n".join(str(value) for value in arguments.values()) or BY_NAME[name].label


def is_dangerous(name: str, arguments: dict) -> bool:
    if name == "run_command":
        return ai_actions.is_risky({"kind": "powershell", "command": str(arguments.get("command", ""))})
    return name == "write_file" and not arguments.get("append") and os.path.exists(str(arguments.get("path", "")))


def key_codes(keys: str) -> list[int]:
    codes = []
    for name in re.split(r"\+", keys.lower().replace(" ", "")):
        if name in _KEY_NAMES:
            codes.append(_KEY_NAMES[name])
        elif len(name) == 1 and name.isalnum():
            codes.append(ord(name.upper()))
        else:
            raise ValueError(f"Unbekannte Taste: {name}")
    return codes


def grid_to_pixel(x: int, y: int, monitor: dict) -> tuple[int, int]:
    x, y = max(0, min(GRID, int(x))), max(0, min(GRID, int(y)))
    return (monitor["left"] + round(x / GRID * (monitor["width"] - 1)),
            monitor["top"] + round(y / GRID * (monitor["height"] - 1)))


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KeyInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _Union(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _Union)]


def _send(events: list[_Input]) -> None:
    array = (_Input * len(events))(*events)
    ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(_Input))


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> _Input:
    return _Input(0, _Union(mi=_MouseInput(dx, dy, 0, flags, 0, 0)))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _Input:
    return _Input(1, _Union(ki=_KeyInput(vk, scan, flags, 0, 0)))


def click_at(x: int, y: int, button: str, double: bool) -> None:
    metric = ctypes.windll.user32.GetSystemMetrics
    left, top, width, height = (metric(index) for index in SM_VIRTUAL)
    dx, dy = (x - left) * 65535 // max(1, width - 1), (y - top) * 65535 // max(1, height - 1)
    events = [_mouse(MOUSE_ABSOLUTE, dx, dy)]
    for _ in range(2 if double else 1):
        events += [_mouse(MOUSE_DOWN[button]), _mouse(MOUSE_UP[button])]
    _send(events)


def type_unicode(text: str) -> None:
    events = []
    data = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-16-le")
    for index in range(0, len(data), 2):
        unit = int.from_bytes(data[index:index + 2], "little")
        events += [_key(scan=unit, flags=KEY_UNICODE), _key(scan=unit, flags=KEY_UNICODE | KEY_UP)]
    if events:
        _send(events)


def press_keys(codes: list[int]) -> None:
    _send([_key(code) for code in codes] + [_key(code, flags=KEY_UP) for code in reversed(codes)])


def screenshot(longest: int) -> tuple[bytes, dict]:
    """JPEG des Monitors unter der Maus und seine Maße für die Klick-Koordinaten."""
    import mss
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    with mss.mss() as grabber:
        monitor = next((m for m in grabber.monitors[1:]
                        if m["left"] <= point.x < m["left"] + m["width"] and m["top"] <= point.y < m["top"] + m["height"]),
                       grabber.monitors[1])
        shot = grabber.grab(monitor)
    image = QImage(shot.bgra, shot.width, shot.height, QImage.Format.Format_RGB32)
    if max(shot.width, shot.height) > longest:
        image = image.scaled(longest, longest, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", JPEG_QUALITY)
    return bytes(buffer.data()), dict(monitor)


def monitor_images() -> list:
    """Graustufen aller Monitore, der unter der Maus zuerst: Dort läuft die Partie fast immer."""
    import mss

    from core import nova_chess
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    with mss.mss() as grabber:
        monitors = sorted(grabber.monitors[1:], key=lambda m: not (
            m["left"] <= point.x < m["left"] + m["width"] and m["top"] <= point.y < m["top"] + m["height"]))
        for monitor in monitors:
            shot = grabber.grab(monitor)
            yield nova_chess.to_gray(shot.bgra, shot.width, shot.height)


def board_jpeg(gray, box: tuple[int, int, int]) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage

    from core import nova_chess
    x, y, size = box
    data = gray[y:y + size, x:x + size].astype("uint8")
    image = QImage(data.data, size, size, size, QImage.Format.Format_Grayscale8)
    image = image.scaled(nova_chess.VISION_SIZE, nova_chess.VISION_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", JPEG_QUALITY)
    return bytes(buffer.data())


def _clipboard_text() -> str:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return ""
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard(text: str) -> None:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def ovh_module():
    """Die OVH-Anbindung gibt es nur im Dev-Build. Über importlib, damit PyInstaller devtools nie einpackt."""
    if not is_dev():
        raise PermissionError("Die OVH-Anbindung gibt es nur im Entwickler-Build.")
    import importlib
    return importlib.import_module("devtools.ovh_domains")


def ovh_secrets(config) -> tuple[str, ...]:
    """Was in keiner Ausgabe an Nova stehen darf."""
    return tuple(value for value in Connections(config).secrets("ovh").values() if value)


def tool_problem(code: str) -> str:
    lowered = code.lower()
    found = next((word for word in FORBIDDEN_IN_TOOLS if word in lowered), "")
    return f"Werkzeuge dürfen nicht an Zugangsdaten heran ({found})." if found else ""


def tool_command(script: str) -> list[str]:
    if FROZEN:
        return app_command("nova_tool", script)
    return [sys.executable, str(ROOT / "core" / "nova_tool_runner.py"), script]  # python.exe: pythonw hätte keine Ausgabe


def run_tool_code(code: str, connection: str = "", secrets=()) -> str:
    """Startet das Werkzeug in einem eigenen Prozess und liefert, was es ausgegeben hat, ohne Zugangsdaten."""
    from core.nova_tool_runner import CONNECTION_FLAG
    problem = tool_problem(code)
    if problem:
        raise PermissionError(problem)
    environment = {key: value for key, value in os.environ.items() if key != CONNECTION_FLAG}
    environment["PYTHONIOENCODING"] = "utf-8"
    if connection:
        environment[CONNECTION_FLAG] = connection
    with tempfile.TemporaryDirectory(prefix="nova_tool_") as folder:
        script = Path(folder) / "werkzeug.py"
        script.write_text(code, encoding="utf-8")
        try:
            process = subprocess.run(tool_command(str(script)), capture_output=True, stdin=subprocess.DEVNULL, timeout=TOOL_SECONDS,
                                     cwd=folder, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"Nach {TOOL_SECONDS} Sekunden abgebrochen."
    output = (process.stdout + process.stderr).decode("utf-8", "replace").strip()
    text = ai_actions.redact(output[-MAX_OUTPUT:])
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return f"Exit-Code {process.returncode}\n{text or '(keine Ausgabe)'}"


def _lines(text: str) -> list[str]:
    return [line.strip(" -*\t") for line in str(text or "").splitlines() if line.strip(" -*\t")]


class Toolbox:
    """Läuft im Hintergrund-Thread. Änderungen an PopWin selbst gehen über `on_main` zurück in die Oberfläche."""

    def __init__(self, ctx, memory, on_main, artifacts=None, on_artifacts=None):
        self.ctx = ctx
        self.memory = memory
        self.on_main = on_main  # (Funktion) -> Ergebnis; führt sie im Hauptthread aus
        self.artifacts = artifacts if artifacts is not None else nova_artifacts.ArtifactStore()
        self.on_artifacts = on_artifacts or (lambda: None)  # sagt der Oberfläche, dass sich ein Artefakt geändert hat
        self.connections = Connections(ctx.config)
        self.on_connection = lambda name: None  # zeigt dem Host die Felder einer Verbindung
        self.monitor: dict | None = None
        self.image: bytes | None = None  # letztes Bildschirmfoto, geht einmal an die KI
        self.chess = None  # NumPy und python-chess laden erst, wenn jemand Schach spielt
        self._plans: dict[str, dict] = {}  # DNS-Änderungen, wie der Host sie vor dem Ja gesehen hat

    def run(self, name: str, arguments: dict) -> dict:
        try:
            return {"ok": True, "result": getattr(self, f"t_{name}")(**arguments)}
        except TypeError as exc:
            return {"ok": False, "result": f"Falsche Angaben: {exc}"}
        except Exception as exc:  # jeder Fehler geht an Nova, sie sagt ihn dem Host; der Chat läuft weiter
            log.warning("Werkzeug %s: %s", name, exc)
            return {"ok": False, "result": ai_actions.redact(str(exc))[:400]}

    def take_image(self) -> bytes | None:
        image, self.image = self.image, None
        return image

    def t_look_at_screen(self) -> str:
        longest = SHOT_SIZES.get(self.ctx.config.get("nova.economy"), SHOT_SIZES["sparsam"])
        self.image, self.monitor = screenshot(longest)
        return "Das Bildschirmfoto hängt an dieser Antwort."

    def _chess_vision(self, gray, box: tuple[int, int, int]) -> dict:
        from core import nova_chess
        nova = self.ctx.nova
        return nova_chess.ask_gemini(lambda: client_for(nova.api_key()), nova.models(), board_jpeg(gray, box))

    def t_read_chess_board(self, new_game: bool = False, side_to_move: str = "") -> str:
        from core import nova_chess
        self.chess = self.chess or nova_chess.ChessEyes(monitor_images, self._chess_vision)
        return nova_chess.describe(self.chess.read(new_game, side_to_move))

    def t_list_windows(self) -> str:
        return "\n".join(win.title for win in win_api.list_windows()[:40]) or "Keine Fenster gefunden."

    def t_remember(self, text: str) -> str:
        return f"Gemerkt (Nummer {self.memory.add(text)})."

    def t_forget(self, id_or_text: str) -> str:
        return f"{self.memory.remove(id_or_text)} Einträge gelöscht."

    def t_popwin_settings(self) -> str:
        return self.on_main(lambda: ai_actions.settings_catalog(self.ctx))

    def t_voices_list(self) -> str:
        from core.nova_voice import VOICES
        settings = self.ctx.config.get("nova")
        lines = [f"{name}: {text}" for name, text in VOICES.items()]
        return (f"Jetzt: {settings['voice']}, Sprechweise: {settings['voice_style'] or 'natürlich'}, "
                f"Vorlesen {'an' if settings['speak'] else 'aus'}\n" + "\n".join(lines))

    def t_set_voice(self, voice: str = "", style: str = "", speak: bool | None = None) -> str:
        from core.nova_voice import VOICES
        config = self.ctx.config
        name = next((known for known in VOICES if known.lower() == voice.strip().lower()), "")
        if voice.strip() and not name:
            raise ValueError(f"Die Stimme {voice} gibt es nicht. Sieh mit voices_list nach.")

        def apply() -> None:
            if name:
                config.set("nova.voice", name)
            if style.strip():
                config.set("nova.voice_style", "" if style.strip() == "-" else style.strip()[:200])
            if speak is not None:
                config.set("nova.speak", bool(speak))
        self.on_main(apply)
        settings = config.get("nova")
        return (f"Stimme {settings['voice']}, Sprechweise {settings['voice_style'] or 'natürlich'}, "
                f"Vorlesen {'an' if settings['speak'] else 'aus'}. Gilt ab der nächsten Antwort.")

    def t_change_popwin_setting(self, key: str, value: str) -> str:
        return self.on_main(lambda: ai_actions.apply_setting(self.ctx, key, value)) or f"{key} steht jetzt auf {value}."

    def t_open_target(self, target: str) -> str:
        if not win_api.shell_open(target):
            raise OSError(f"Konnte nicht geöffnet werden: {target}")
        return f"{target} geöffnet."

    def t_focus_window(self, title: str) -> str:
        needle = title.lower()
        window = next((win for win in win_api.list_windows() if needle in win.title.lower()), None)
        if window is None:
            return f"Kein Fenster mit {title} gefunden."
        win_api.force_foreground(window.hwnd)
        return f"{window.title} ist vorn."

    def t_read_file(self, path: str) -> str:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as file:
            data = file.read(MAX_READ + 1)
        return ai_actions.redact(data[:MAX_READ]) + ("\n[gekürzt]" if len(data) > MAX_READ else "")

    def t_list_folder(self, path: str) -> str:
        names = sorted(entry.name + ("\\" if entry.is_dir() else "") for entry in Path(os.path.expanduser(path)).iterdir())
        return "\n".join(names[:200]) or "Der Ordner ist leer."

    def t_clipboard_get(self) -> str:
        return ai_actions.redact(_clipboard_text()[:MAX_OUTPUT]) or "Die Zwischenablage enthält keinen Text."

    def t_clipboard_set(self, text: str) -> str:
        _set_clipboard(text)
        return "Steht in der Zwischenablage."

    def t_click(self, x: int, y: int, button: str = "left", double: bool = False) -> str:
        if self.monitor is None:
            return "Schau zuerst mit look_at_screen hin."
        px, py = grid_to_pixel(x, y, self.monitor)
        click_at(px, py, "right" if button == "right" else "left", bool(double))
        time.sleep(SETTLE_SECONDS)
        return f"Geklickt bei {x}, {y}."

    def t_type_text(self, text: str) -> str:
        type_unicode(text)
        return f"{len(text)} Zeichen getippt."

    def t_press_key(self, keys: str) -> str:
        press_keys(key_codes(keys))
        time.sleep(SETTLE_SECONDS)
        return f"{keys} gedrückt."

    def t_run_command(self, command: str) -> str:
        try:
            process = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                     capture_output=True, stdin=subprocess.DEVNULL, timeout=COMMAND_SECONDS,
                                     cwd=os.path.expanduser("~"), creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"Nach {COMMAND_SECONDS} Sekunden abgebrochen."
        output = (process.stdout + process.stderr).decode("cp850", "replace").strip()
        return f"Exit-Code {process.returncode}\n{ai_actions.redact(output[-MAX_OUTPUT:]) or '(keine Ausgabe)'}"

    def t_write_file(self, path: str, content: str, append: bool = False) -> str:
        target = os.path.abspath(os.path.expanduser(path))
        with open(target, "a" if append else "w", encoding="utf-8") as file:
            file.write(content)
        return f"Geschrieben: {target}"

    # --- Artefakte -----------------------------------------------------------------
    def _store(self, artifact: dict) -> None:
        self.artifacts.save(artifact)
        self.on_artifacts()

    def t_connection_request(self, name: str, fields: str, help_url: str = "", note: str = "") -> str:
        name = self.connections.define(name, _lines(fields.replace(",", "\n")), help_url, note)
        self.on_connection(name)
        missing = [field for field in self.connections.get(name)["fields"] if not self.connections.is_set(name, field)]
        return (f"Der Host sieht jetzt die Felder der Verbindung {name}. Sag ihm, dass er sie dort eintragen soll."
                if missing else f"Die Verbindung {name} ist schon vollständig.")

    def t_connection_list(self) -> str:
        return self.connections.describe()

    def t_artifact_create(self, title: str, kind: str, content: str = "", steps: str = "", due: str = "", check: str = "",
                          tool: str = "", read_only: bool = False, connection: str = "") -> str:
        artifact = nova_artifacts.new_artifact(title, kind, content, _lines(steps))
        if check:
            if check not in nova_artifacts.CHECKS:
                raise ValueError(f"Unbekannte Prüfung {check}. Es gibt: {', '.join(nova_artifacts.CHECKS)}")
            if check == "ovh_domains":
                ovh_module()
            artifact["check"] = check
        if tool:
            helper = self.artifacts.get(tool)
            if helper["kind"] != "werkzeug":
                raise ValueError(f"{helper['title']} ist kein Werkzeug.")
            artifact["tool"] = helper["id"]
        if artifact["kind"] == "werkzeug":
            if not artifact["code"].strip():
                raise ValueError("Ein Werkzeug braucht Code in content.")
            problem = tool_problem(artifact["code"])
            if problem:
                raise ValueError(problem)
            artifact["read_only"] = bool(read_only)
            if connection:
                self.connections.get(connection)  # scheitert, wenn es die Verbindung nicht gibt
                artifact["connection"] = connection.strip().lower()
        artifact["due"] = due.strip()
        self._store(artifact)
        return f"Angelegt: {artifact['id']}"

    def t_artifact_list(self) -> str:
        return "\n".join(nova_artifacts.summary_line(item) for item in self.artifacts.all()) or "Es gibt noch keine Artefakte."

    def t_artifact_read(self, id: str) -> str:  # noqa: A002 - so heißt das Feld für Nova
        return nova_artifacts.describe(self.artifacts.get(id))

    def t_artifact_update(self, id: str, note: str = "", step_done: str = "", step_open: str = "", add_steps: str = "",  # noqa: A002
                          state: str = "", due: str = "", title: str = "", code: str = "") -> str:
        artifact = self.artifacts.get(id)
        done = []
        if note.strip():
            artifact["notes"] = (artifact["notes"] + "\n\n" if artifact["notes"] else "") + note.strip()
            nova_artifacts.add_history(artifact, f"Notiz: {note.strip()[:120]}")
            done.append("Notiz angehängt")
        for key, finished in ((step_done, True), (step_open, False)):
            if str(key).strip():
                step = nova_artifacts.find_step(artifact, key)
                step["done"] = finished
                nova_artifacts.add_history(artifact, f"{'Erledigt' if finished else 'Wieder offen'}: {step['text']}")
                done.append(f"Schritt {'erledigt' if finished else 'offen'}")
        for text in _lines(add_steps):
            artifact["steps"].append({"text": text, "done": False})
            done.append("Schritt hinzugefügt")
        if state:
            if state not in nova_artifacts.STATES:
                raise ValueError(f"Zustand ist {', '.join(nova_artifacts.STATES)}.")
            artifact["state"] = state
            nova_artifacts.add_history(artifact, f"Zustand: {nova_artifacts.STATES[state]}")
            done.append("Zustand geändert")
        if due:
            artifact["due"], artifact["reminded"] = due.strip(), ""
            done.append("Fälligkeit gesetzt")
        if title.strip():
            artifact["title"] = title.strip()[:120]
            done.append("Titel geändert")
        if code.strip():
            if artifact["kind"] != "werkzeug":
                raise ValueError("Nur Werkzeuge haben Code.")
            artifact["code"], artifact["approved"] = code[:nova_artifacts.MAX_CODE], ""
            nova_artifacts.add_history(artifact, "Code geändert, braucht eine neue Freigabe")
            done.append("Code geändert")
        if not done:
            return "Nichts geändert."
        self._store(artifact)
        progress = "{} von {} Schritten erledigt".format(*nova_artifacts.progress(artifact))
        return f"{', '.join(done)}. {progress}."

    def t_artifact_delete(self, id: str) -> str:  # noqa: A002
        title = self.artifacts.delete(id)
        self.on_artifacts()
        return f"Gelöscht: {title}"

    def t_artifact_schedule(self, id: str, hours: int) -> str:  # noqa: A002
        artifact = self.artifacts.get(id)
        if artifact["kind"] != "ueberwachung":
            raise ValueError("Einen Zeitplan haben nur Überwachungen.")
        nova_artifacts.schedule(artifact, hours)
        nova_artifacts.add_history(artifact, f"Zeitplan: alle {int(hours)} Stunden" if int(hours) else "Zeitplan aus")
        self._store(artifact)
        return f"Nächste Prüfung: {artifact['next_check'] or 'keine'}"

    def has_ovh(self) -> bool:
        """Die eingebaute OVH-Prüfung gibt es nur im Dev-Build; eigene OVH-Werkzeuge kann jeder bauen."""
        try:
            ovh_module()
            return True
        except Exception:
            return False

    def run_artifact_tool(self, artifact: dict) -> str:
        """Für Nova und die Überwachung: Die Freigabe gilt nur für Werkzeuge, die nur lesen, und genau diesen Code."""
        connection = artifact.get("connection", "")
        output = run_tool_code(artifact["code"], connection, tuple(self.connections.secrets(connection).values()) if connection else ())
        if artifact.get("read_only"):
            artifact["approved"] = nova_artifacts.code_hash(artifact["code"])
        nova_artifacts.add_history(artifact, f"Ausgeführt: {output.splitlines()[0]}")
        self._store(artifact)
        return output

    def t_artifact_run_tool(self, id: str) -> str:  # noqa: A002
        artifact = self.artifacts.get(id)
        if artifact["kind"] != "werkzeug":
            raise ValueError(f"{artifact['title']} ist kein Werkzeug.")
        return self.run_artifact_tool(artifact)

    def preapproved(self, name: str, arguments: dict) -> bool:
        if name != "artifact_run_tool":
            return False
        try:
            artifact = self.artifacts.get(arguments.get("id", ""))
        except KeyError:
            return False
        return bool(artifact.get("read_only") and artifact.get("approved") == nova_artifacts.code_hash(artifact.get("code", "")))

    # --- OVH, nur im Dev-Build ----------------------------------------------------------
    def t_ovh_overview(self, records: bool = False) -> str:
        from datetime import date
        ovh = ovh_module()
        data = ovh.snapshot(ovh.client(self.ctx.config))
        text = ovh.overview(data, date.today())
        if records:
            text += "".join(f"\n\n{domain}:\n" + "\n".join(info["records"]) for domain, info in data.items())
        return ovh.scrub(text, ovh_secrets(self.ctx.config))

    @staticmethod
    def _plan_key(arguments: dict) -> str:
        """Nova lässt Standardwerte mal weg und mal nicht: Der Schlüssel soll in beiden Fällen gleich sein."""
        full = {"subdomain": "", "target": "", "ttl": 0, "action": "set", **{k: v for k, v in arguments.items() if k != "artifact"}}
        full["ttl"], full["action"] = int(full["ttl"] or 0), full["action"] or "set"
        return json.dumps(full, sort_keys=True, default=str)

    def _plan(self, arguments: dict) -> dict:
        ovh = ovh_module()
        return ovh.plan_change(ovh.client(self.ctx.config), arguments.get("zone", ""), arguments.get("subdomain", ""),
                               arguments.get("field_type", ""), arguments.get("target", ""), arguments.get("ttl", 0) or 0,
                               arguments.get("action", "set") or "set")

    def t_ovh_dns_change(self, zone: str, field_type: str, subdomain: str = "", target: str = "", ttl: int = 0, action: str = "set",
                         artifact: str = "") -> str:
        ovh = ovh_module()
        arguments = {"zone": zone, "field_type": field_type, "subdomain": subdomain, "target": target, "ttl": ttl, "action": action,
                     "artifact": artifact}
        shown = self._plans.pop(self._plan_key(arguments), None)
        api = ovh.client(self.ctx.config)
        plan = self._plan(arguments)
        if shown is None or not ovh.same_plan(shown, plan):
            raise RuntimeError("Die Zone hat sich seit der Nachfrage geändert. Es wurde nichts geändert; frag bitte noch einmal.")
        result = ovh.apply_plan(api, plan)
        if artifact:
            entry = self.artifacts.get(artifact)
            nova_artifacts.add_history(entry, result)
            self._store(entry)
        return result

    def preview(self, name: str, arguments: dict) -> tuple[str, bool] | None:
        """Was der Host vor dem Ja liest, wenn der reine Aufruf nicht genügt: der Code oder genau alt → neu."""
        if name == "artifact_run_tool":
            artifact = self.artifacts.get(arguments.get("id", ""))
            reading = artifact.get("read_only")
            head = f"Werkzeug „{artifact['title']}“ " + ("(nur lesen; danach läuft genau dieser Code ohne Nachfrage)"
                                                       if reading else "(darf etwas ändern)")
            if artifact.get("connection"):
                head += f"\nBekommt die Keys der Verbindung {artifact['connection']}."
            return f"{head}\n\n{artifact.get('code', '')}", not reading
        if name == "ovh_dns_change":
            plan = self._plan(arguments)
            self._plans[self._plan_key(arguments)] = plan
            return ovh_module().describe_plan(plan), True
        if name == "artifact_delete":
            return f"Artefakt löschen: {self.artifacts.get(arguments.get('id', ''))['title']}", True
        if name == "artifact_schedule":
            artifact = self.artifacts.get(arguments.get("id", ""))
            hours = int(arguments.get("hours", 0) or 0)
            return (f"„{artifact['title']}“ alle {hours} Stunden prüfen" if hours else f"Zeitplan von „{artifact['title']}“ abschalten"), False
        return None


def as_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
