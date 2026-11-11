"""PopWin Explorer: eigener Dateimanager mit Tabs, geteilter Ansicht, Vorschau und Blitzsuche.

Läuft als eigener Prozess neben PopWin. Ein Fehler hier reißt Taskbar und Topbar nicht mit.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)  # [Antigravity]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import (  # noqa: E402
    QByteArray, QDateTime, QDir, QEvent, QFileInfo, QMimeData, QObject, QPoint, QSize, Qt, QThread, QTimer, QUrl,
    Signal,
)
from PySide6.QtGui import (  # noqa: E402
    QAction, QActionGroup, QColor, QCursor, QGuiApplication, QIcon, QImage, QImageReader, QKeySequence, QMouseEvent, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileIconProvider, QFileSystemModel, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QFrame,
    QProgressBar, QPushButton,  # [Antigravity]
    QListView, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QStackedWidget,
    QStyledItemDelegate, QToolButton,
    QTabBar, QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core import i18n, win_api  # noqa: E402
from core.paths import DATA_DIR, app_command  # noqa: E402
from editor.widget import decode, line_ending  # noqa: E402
from ui.app_shell import run_app  # noqa: E402
from ui.base import G, IconButton, glyph_icon, icon_for_target, themed_menu  # noqa: E402
from ui.context_menu import ContextMenu  # noqa: E402
from ui.drag_switch import DragHover  # noqa: E402
from ui.window_frame import NativeWindow  # noqa: E402

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
TEXT_LIMIT = 64 * 1024
STAR = "★"
STAR_COLOR = "#F2C94C"
STAR_TOP_FROM = 48
SEARCH_LIMIT = 400
SKIP_FOLDERS = {".git", "node_modules", "__pycache__", ".venv", "AppData", "$Recycle.Bin", "Windows"}
CATEGORIES = {
    "Bilder": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg", ".psd", ".tif", ".tiff"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv"},
    "Audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".flp"},
    "Dokumente": {".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".rtf", ".csv"},
    "Archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "Programme": {".exe", ".msi", ".lnk", ".bat", ".cmd", ".jar"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".cpp", ".c", ".cs", ".qss", ".xml", ".yml"},
}
CATEGORY_ORDER = ["Ordner", *CATEGORIES, "Andere"]
DATE_ORDER = ["Heute", "Gestern", "Diese Woche", "Dieser Monat", "Früher"]
SORT_COLUMNS = {"Name": 0, "Größe": 1, "Typ": 2, "Änderungsdatum": 3}
SORT_KEYS = {"name": 0, "size": 1, "type": 2, "date": 3}
EMPTY_PREVIEW = "Wähle eine Datei aus, um sie hier zu sehen."
SLOW_CLICK_LIMIT_MS = 1500
PLACE_HEIGHT = 30
COLUMN_WIDTHS = {0: 360, 3: 150, 2: 130, 1: 90}
STORE = DATA_DIR / "explorer.json"
TEXT_TYPES = {".txt", ".md", ".log", ".ini", ".json", ".csv", ".ps1", ".py", ".bat", ".xml", ".cfg", ".toml", ".yml"}
ARCHIVE_TYPES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
CUT_EFFECT, COPY_EFFECT = 2, 1  # Windows-Kennung im Format „Preferred DropEffect“
AUTO_BLOCKS_WIDTH = 1000  # ab dieser Breite zeigt der Ordner Blöcke statt einer Liste
BLOCK_WIDTH, BLOCK_HEIGHT = 136, 144
THUMBNAIL_SIZE = 96
TEAR_DISTANCE = 40
TEAR_GRAB_X, TEAR_GRAB_Y = 180, 18  # dort hängt das neue Fenster an der Maus
TORN_WINDOWS: list = []
DAY = 86400


def is_slow_click(same_item: bool, elapsed_ms: float, double_click_ms: int) -> bool:
    """Zweiter Klick auf denselben Eintrag: zu langsam zum Öffnen, aber noch zusammengehörig."""
    return same_item and double_click_ms <= elapsed_ms <= SLOW_CLICK_LIMIT_MS


def file_category(name: str, is_folder: bool) -> str:
    if is_folder:
        return "Ordner"
    suffix = os.path.splitext(name)[1].lower()
    return next((label for label, types in CATEGORIES.items() if suffix in types), "Andere")


def date_bucket(modified: float, now: float) -> str:
    today = time.localtime(now)
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
    if modified >= midnight:
        return "Heute"
    if modified >= midnight - DAY:
        return "Gestern"
    if modified >= midnight - today.tm_wday * DAY:
        return "Diese Woche"
    if modified >= midnight - (today.tm_mday - 1) * DAY:
        return "Dieser Monat"
    return "Früher"


def tab_title(path: str) -> str:
    """Ordnername; ein Laufwerk zeigt seinen Namen aus Windows."""
    return os.path.basename(path.rstrip("\\/")) or win_api.shell_name(path)


def type_label(name: str, is_folder: bool) -> str:
    if is_folder:
        return "Dateiordner"
    suffix = os.path.splitext(name)[1].lstrip(".").upper()
    return f"{suffix}-Datei" if suffix else "Datei"


def read_store() -> dict:
    """Eigene Datei: PopWin schreibt settings.json laufend selbst und überschriebe die Werte des Explorers."""
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_store(**values) -> None:
    data = {**read_store(), **values}
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def free_name(folder: str, name: str) -> str:
    """„Datei.txt“, „Datei (2).txt“: Kopieren überschreibt nie etwas."""
    stem, suffix = os.path.splitext(name)
    candidate, number = os.path.join(folder, name), 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} ({number}){suffix}")
        number += 1
    return candidate


def transfer_file(source: str, folder: str, move: bool) -> str:
    target = free_name(folder, os.path.basename(source.rstrip("\\/")))
    if move:
        shutil.move(source, target)
    elif os.path.isdir(source):
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    return target


def inside_itself(source: str, folder: str) -> bool:
    source, folder = os.path.normcase(os.path.normpath(source)), os.path.normcase(os.path.normpath(folder))
    return folder == source or folder.startswith(source + os.sep)


def archive_stem(name: str) -> str:
    lowered = name.lower()
    return next((name[:-len(suffix)] for suffix in ARCHIVE_TYPES if lowered.endswith(suffix)), name)


def ordered_places(places: list[tuple[str, str]], saved: list[dict]) -> list[tuple[str, str]]:
    """Gespeicherte Reihenfolge und eigene Einträge; neue Laufwerke kommen ans Ende."""
    known = dict((target, label) for label, target in places)
    ordered = [(known.get(item["path"], item["label"]), item["path"]) for item in saved if os.path.exists(item["path"])]
    used = {target for _label, target in ordered}
    return ordered + [(label, target) for label, target in places if target not in used]


def split_places(fixed: list[tuple[str, str]], saved: list[dict]) -> tuple[list, list]:
    """Windows-Ordner und Laufwerke oben, eigene Einträge unten. Jede Gruppe hat ihre eigene Reihenfolge."""
    known = {target for _label, target in fixed}
    ordered = ordered_places(fixed, saved)
    return [place for place in ordered if place[1] in known], [place for place in ordered if place[1] not in known]


class Favorites:
    """Sterne an Dateien und Ordnern. Sie ändern an der Datei nichts, gemerkt wird nur der Pfad."""

    def __init__(self):
        self._paths: dict[str, str] | None = None

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    @property
    def paths(self) -> dict[str, str]:
        if self._paths is None:
            self._paths = {self._key(path): path for path in read_store().get("favorites", [])}
        return self._paths

    def has(self, path: str) -> bool:
        return self._key(path) in self.paths

    def _store(self) -> None:
        write_store(favorites=list(self.paths.values()))

    def toggle(self, paths: list[str]) -> None:
        """Hat eins noch keinen Stern, bekommen alle einen, wie im Windows-Explorer beim Anheften."""
        add = not all(self.has(path) for path in paths)
        for path in paths:
            if add:
                self.paths[self._key(path)] = os.path.normpath(path)
            else:
                self.paths.pop(self._key(path), None)
        self._store()

    def rename(self, old: str, new: str) -> None:
        if self.has(old):
            self.paths.pop(self._key(old))
            self.paths[self._key(new)] = os.path.normpath(new)
            self._store()

    def existing(self) -> list[str]:
        return sorted((path for path in self.paths.values() if os.path.exists(path)), key=lambda path: os.path.basename(path).lower())


FAVORITES = Favorites()


AI_NAMES = 1500
AI_SYSTEM = ("Du hilfst, Dateien zu finden. Antworte nur mit Pfaden aus der Liste, je Zeile einer und höchstens 25, ohne Erklärung. "
             "Passt nichts, antworte leer.")


def file_names(root: str, limit: int = AI_NAMES) -> list[str]:
    """Relative Pfade, nächste Ebenen zuerst: Bei großen Ordnern zählt das Naheliegende."""
    found: list[str] = []
    waiting = [root]
    while waiting and len(found) < limit:
        folder = waiting.pop(0)
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            found.append(os.path.relpath(entry.path, root))
            if entry.is_dir(follow_symlinks=False):
                waiting.append(entry.path)
    return found[:limit]


def matched_files(answer: str, names: list[str]) -> list[str]:
    """Nur was wirklich in der Liste steht: Erfundene Pfade der KI fallen weg."""
    known = {name.lower(): name for name in names}
    lines = (line.strip(" -*•\t`") for line in answer.splitlines())
    return list(dict.fromkeys(known[line.lower()] for line in lines if line.lower() in known))


class AiFileSearch(QThread):
    found = Signal(list, str)

    def __init__(self, root: str, question: str, key: str, models: list[str], parent=None):
        super().__init__(parent)
        self.root, self.question, self.key, self.models = root, question, key, models

    def run(self) -> None:
        from core.gemini_client import friendly_error, generate
        names = file_names(self.root)
        prompt = f"Gesucht: {self.question}\n\nDateien:\n" + "\n".join(names)
        try:
            answer = generate(self.key, self.models, prompt, AI_SYSTEM)[0]
        except Exception as exc:  # keine Verbindung, Limit erreicht: Das gehört als Satz ins Fenster, nicht als Absturz
            self.found.emit([], friendly_error(exc))
            return
        self.found.emit([os.path.join(self.root, name) for name in matched_files(answer, names)], "")


def sort_key(entry: os.DirEntry, column: int):
    """Sortierwert passend zu den Spalten in SORT_COLUMNS."""
    if column == 1:
        return 0 if entry.is_dir() else entry.stat().st_size
    if column == 2:
        return not entry.is_dir(), type_label(entry.name, entry.is_dir()), entry.name.lower()
    if column == 3:
        return entry.stat().st_mtime
    return entry.name.lower()


def grouped_entries(folder: str, group: str, now: float, column: int = 0,
                    descending: bool = False, custom_groups: dict[str, str] | None = None) -> dict[str, list[os.DirEntry]]:
    """Ordnet die Einträge eines Ordners in Gruppen wie im Windows-Explorer."""
    # --- [Antigravity Start: KI-gestützte thematische Gruppierung (Rest: Claude Code)] ---
    if group == "ai":
        groups: dict[str, list] = {}
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        label = "Ordner"
                    elif custom_groups and entry.name in custom_groups:
                        label = custom_groups[entry.name]
                    else:
                        label = heuristic_category_single(entry.name)
                except OSError:
                    continue
                groups.setdefault(label, []).append(entry)
        return {label: sorted(items, key=lambda e: sort_key(e, column), reverse=descending)
                for label, items in groups.items() if items}
    # --- [Antigravity Ende] ---
    order = DATE_ORDER if group == "date" else CATEGORY_ORDER
    groups: dict[str, list] = {label: [] for label in order}
    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                label = (date_bucket(entry.stat().st_mtime, now) if group == "date"
                         else file_category(entry.name, entry.is_dir()))
            except OSError:
                continue
            groups[label].append(entry)
    return {label: sorted(items, key=lambda e: sort_key(e, column), reverse=descending)
            for label, items in groups.items() if items}


def search_tree(root: str, needle: str, stop, limit: int = SEARCH_LIMIT):
    """Findet Dateinamen unterhalb von root, große Systemordner werden übersprungen."""
    text = needle.lower()
    found = 0
    for folder, subfolders, files in os.walk(root):
        if stop():
            return
        subfolders[:] = [name for name in subfolders if name not in SKIP_FOLDERS]
        for name in subfolders + files:
            if text in name.lower():
                yield os.path.join(folder, name)
                found += 1
                if found >= limit:
                    return



# --- [Antigravity Start: KI- und Heuristik-Dateisortierung (Rest: Claude Code)] ---
CATEGORIES_HEURISTIC = {
    "Rechnungen & Finanzen": {
        "keywords": ["rechnung", "invoice", "beleg", "quittung", "steuer", "finanz", "konto", "abrechnung", "gehalt", "banking", "auszug", "spesen"],
        "extensions": [".pdf", ".csv", ".xlsx", ".xls"]
    },
    "Bilder & Screenshots": {
        "keywords": ["screenshot", "bild", "foto", "photo", "screen", "grafik", "wallpaper", "avatar", "icon", "thumb"],
        "extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".psd", ".tif", ".tiff"]
    },
    "Setups & Programme": {
        "keywords": ["setup", "install", "installer", "update", "patch", "portable", "release"],
        "extensions": [".exe", ".msi", ".iso", ".bat", ".cmd"]
    },
    "Dokumente & Notizen": {
        "keywords": ["vertrag", "contract", "brief", "notiz", "protokoll", "bericht", "report", "lebenslauf", "cv", "anschreiben", "dokument", "paper"],
        "extensions": [".pdf", ".docx", ".doc", ".odt", ".txt", ".md", ".rtf", ".pptx"]
    },
    "Archive & Backups": {
        "keywords": ["backup", "archiv", "archive", "sicherung", "dump", "save"],
        "extensions": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"]
    },
    "Code & Entwicklung": {
        "keywords": ["script", "skript", "test", "build", "config", "docker", "main", "app", "dev"],
        "extensions": [".py", ".js", ".ts", ".html", ".css", ".json", ".ps1", ".java", ".cpp", ".c", ".cs", ".sql", ".sh", ".yml", ".yaml", ".xml"]
    },
    "Videos & Aufnahmen": {
        "keywords": ["aufnahme", "recording", "video", "clip", "film", "movie", "screenrec"],
        "extensions": [".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv"]
    },
    "Audio & Musik": {
        "keywords": ["song", "musik", "track", "audio", "voice", "sprachmemo", "podcast"],
        "extensions": [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"]
    }
}


def heuristic_category_single(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    base = os.path.splitext(name)[0].lower()
    for cat, rule in CATEGORIES_HEURISTIC.items():
        if any(kw in base for kw in rule["keywords"]):
            return cat
    for cat, rule in CATEGORIES_HEURISTIC.items():
        if ext in rule["extensions"]:
            return cat
    return "Sonstiges"


BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_folder_name(name: str) -> str:
    """Ordnername aus einem KI-Vorschlag: keine Pfade wie „..\\“ oder „C:\\“, keine verbotenen Zeichen."""
    cleaned = " ".join(BAD_NAME_CHARS.sub(" ", str(name)).split()).strip(" .")[:60].strip(" .")
    if cleaned.upper() in RESERVED_NAMES:
        cleaned += "_"
    return cleaned or "Sonstiges"


def heuristic_categorize(names: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name in names:
        cat = heuristic_category_single(name)
        result.setdefault(cat, []).append(name)
    return result


class AiSortWorker(QThread):
    ready = Signal(dict, bool, str)  # categories: dict[str, list[str]], is_ai: bool, note: str

    def __init__(self, folder: str, file_names: list[str], key: str, models: list[str], parent=None):
        super().__init__(parent)
        self.folder = folder
        self.file_names = file_names
        self.key = key
        self.models = models

    def run(self) -> None:
        if self.key and self.models and len(self.file_names) > 0:
            prompt = (
                "Du hilfst beim Sortieren und Organisieren von Dateien in einem Ordner.\n"
                "Ordne die folgenden Dateien sinnvollen, prägnanten deutschsprachigen Kategorienamen (Ziel-Unterordnern) zu.\n"
                "Verwende höchstens 7 Kategorien. Erfinde keine Dateinamen, jede Datei aus der Liste muss genau einer Kategorie zugeordnet sein.\n"
                "Antworte ausschließlich mit reinem JSON im Format: {\"categories\": {\"Kategoriename\": [\"datei1.ext\"]}}\n\n"
                "Dateien:\n" + "\n".join(self.file_names[:150])
            )
            system = "Du bist ein intelligenter Datei-Organisator für PopWin Explorer. Antworte nur mit JSON."
            try:
                from core.gemini_client import generate, parse_json
                reply, model = generate(self.key, self.models, prompt, system)
                data = parse_json(reply)
                cats = data.get("categories")
                if isinstance(cats, dict) and cats:
                    validated = {}
                    known_set = set(self.file_names)
                    assigned = set()
                    for cat_name, items in cats.items():
                        if isinstance(items, list):
                            clean_items = list(dict.fromkeys(f for f in items if f in known_set and f not in assigned))
                            if clean_items:  # jede Datei nur einmal, auch wenn die KI sie doppelt nennt
                                validated.setdefault(safe_folder_name(cat_name), []).extend(clean_items)
                                assigned.update(clean_items)
                    unassigned = [f for f in self.file_names if f not in assigned]
                    if unassigned:
                        fallback_cats = heuristic_categorize(unassigned)
                        for fcat, fitems in fallback_cats.items():
                            validated.setdefault(fcat, []).extend(fitems)
                    if validated:
                        self.ready.emit(validated, True, f"Mit Gemini ({model}) analysiert")
                        return
            except Exception as exc:
                log.info("Gemini-Dateisortierung fehlgeschlagen, nutze Heuristik: %s", exc)

        cats = heuristic_categorize(self.file_names)
        self.ready.emit(cats, False, "Mit lokaler Heuristik analysiert")
# --- [Antigravity Ende] ---

class SearchWorker(QThread):
    hit = Signal(str)

    def __init__(self, root: str, needle: str, parent=None):
        super().__init__(parent)
        self.root = root
        self.needle = needle
        self.cancelled = False

    def run(self) -> None:
        for path in search_tree(self.root, self.needle, lambda: self.cancelled):
            self.hit.emit(path)


class TransferWorker(QThread):
    """Große Dateien dürfen das Fenster nicht einfrieren."""
    finished_with = Signal(list, list, str)  # (Quelle, Ziel), Fehlermeldung

    def __init__(self, paths: list[str], folder: str, move: bool, parent=None):
        super().__init__(parent)
        self.paths, self.folder, self.move_files = paths, folder, move

    def run(self) -> None:
        done, error = [], ""
        for path in self.paths:
            if inside_itself(path, self.folder):
                error = "Ein Ordner kann nicht in sich selbst kopiert werden."
                continue
            try:
                done.append((path, transfer_file(path, self.folder, self.move_files)))
            except (OSError, shutil.Error) as exc:
                error = str(exc)
        self.finished_with.emit(done, [], error)


def keep_source_rows(event) -> None:
    """Die Datei verschiebt der Pane selbst. Hieße die Antwort „verschoben“, löschte Qt die Zeilen aus der Ansicht,
    auch wenn nichts passiert ist; die Datei wäre dann bis zum Neuladen unsichtbar."""
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()


class GroupTree(QTreeWidget):
    """Gruppenansicht: Dateien lassen sich herausziehen und auf Ordner oder in den Ordner fallen lassen."""

    def __init__(self, pane: "Pane"):
        super().__init__()
        self.pane = pane
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, items):
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(item.toolTip(0)) for item in items if item.toolTip(0)])
        return data

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        path = item.toolTip(0) if item else ""
        folder = path if path and os.path.isdir(path) else self.pane.path
        self.pane.drop_urls(event.mimeData(), folder, event.modifiers())
        keep_source_rows(event)

    def startDrag(self, actions) -> None:
        super().startDrag(actions)
        self.pane._reload_timer.start()  # ein fremdes Ziel kann „verschoben“ melden, dann fehlen hier Zeilen


class BlockView(QListView):
    """Kachelansicht. Qt verschöbe die Kacheln beim Ablegen frei im Bild, statt die Dateien zu verschieben."""

    def __init__(self, pane: "Pane"):
        super().__init__()
        self.pane = pane

    def startDrag(self, actions) -> None:
        QAbstractItemView.startDrag(self, actions)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        folder = self.pane.model.filePath(index) if index.isValid() and self.pane.model.isDir(index) else self.pane.path
        self.pane.drop_urls(event.mimeData(), folder, event.modifiers())
        keep_source_rows(event)


def read_thumbnail(path: str, side: int = THUMBNAIL_SIZE) -> QImage:
    """Liest nur so viel wie nötig: Das Bild kommt beim Laden schon klein an. Kleine Bilder bleiben, wie sie sind."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > side or size.height() > side):
        reader.setScaledSize(size.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


class ThumbnailLoader(QObject):
    """Holt Vorschaubilder nacheinander im Hintergrund; das Fenster zeigt solange das normale Dateisymbol."""
    loaded = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.waiting: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="thumbnails").start()

    def _run(self) -> None:
        while True:
            path = self.waiting.get()
            image = read_thumbnail(path)
            if image.isNull():
                continue
            try:
                self.loaded.emit(path, image)
            except RuntimeError:  # das Fenster ist inzwischen zu
                return


class TrackedModel(QFileSystemModel):
    """Lässt Dateien über den Pane laufen, damit Rückgängig, Laufwerkswechsel und freie Namen stimmen."""
    HEADERS = {0: "Name", 1: "Größe", 2: "Typ", 3: "Änderungsdatum"}

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and section in self.HEADERS:
            return i18n.translate(self.HEADERS[section])
        return super().headerData(section, orientation, role)

    def __init__(self, pane: "Pane"):
        super().__init__(pane)
        self.pane = pane

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.column() == 2 and role == Qt.ItemDataRole.DisplayRole:
            return type_label(self.fileName(index), self.isDir(index))
        if index.column() == 0 and role == Qt.ItemDataRole.DecorationRole:
            thumbnail = self.pane.thumbnail(self.filePath(index))
            if thumbnail is not None:
                return thumbnail
        return super().data(index, role)

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        folder = self.filePath(parent) if parent.isValid() and self.isDir(parent) else self.pane.path
        self.pane.drop_urls(data, folder, QApplication.keyboardModifiers())
        return False  # sonst löschte die Ansicht die Quelle noch einmal selbst


def text_field() -> QLineEdit | QPlainTextEdit | None:
    """Das Textfeld mit dem Fokus. Strg + X und Co. gelten dann dem Text, nicht den Dateien."""
    field = QApplication.focusWidget()
    return field if isinstance(field, (QLineEdit, QPlainTextEdit)) else None


def has_selection(field: QLineEdit | QPlainTextEdit) -> bool:
    return field.hasSelectedText() if isinstance(field, QLineEdit) else field.textCursor().hasSelection()


def add_text_menu(ctx, field: QLineEdit | QPlainTextEdit) -> None:
    """Rechtsklick wie überall in PopWin; das Menü von Qt wäre englisch und passte nicht zum Theme."""
    def show(position) -> None:
        marked, editable = has_selection(field), not field.isReadOnly()
        menu = themed_menu(ctx, field)
        for label, handler, enabled in (("Rückgängig", field.undo, editable and field.isUndoAvailable()
                                         if isinstance(field, QLineEdit) else editable and field.document().isUndoAvailable()),
                                        ("Ausschneiden", field.cut, marked and editable), ("Kopieren", field.copy, marked),
                                        ("Einfügen", field.paste, editable), ("Alles auswählen", field.selectAll, True)):
            menu.addAction(label, handler).setEnabled(enabled)
        menu.popup((field.viewport() if isinstance(field, QPlainTextEdit) else field).mapToGlobal(position))
    field.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    field.customContextMenuRequested.connect(show)


class RenameDelegate(QStyledItemDelegate):
    """Markiert beim Umbenennen nur den Namen, nicht die Endung."""

    def __init__(self, ctx, parent):
        super().__init__(parent)
        self.ctx = ctx

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            add_text_menu(self.ctx, editor)
        return editor

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        path = index.data(QFileSystemModel.Roles.FilePathRole) or index.data(Qt.ItemDataRole.ToolTipRole)
        if path and FAVORITES.has(path):
            painter.save()
            painter.setPen(QColor(STAR_COLOR))
            big = option.rect.height() > STAR_TOP_FROM  # in der Kachelansicht in die Ecke, sonst ans Zeilenende
            where = Qt.AlignmentFlag.AlignTop if big else Qt.AlignmentFlag.AlignVCenter
            painter.drawText(option.rect.adjusted(0, 2, -6, 0), Qt.AlignmentFlag.AlignRight | where, STAR)
            painter.restore()

    def setEditorData(self, editor, index) -> None:
        super().setEditorData(editor, index)
        stem = os.path.splitext(editor.text())[0]
        # Qt markiert danach noch einmal alles, deshalb erst im nächsten Durchlauf setzen.
        QTimer.singleShot(0, editor, lambda: editor.setSelection(0, len(stem) or len(editor.text())))


class Pane(QWidget):
    """Ein Ordner-Bereich mit Adresszeile, Liste und Suche."""
    selected = Signal(str)
    navigated = Signal(str)
    undoable = Signal(str, object)  # Beschreibung und Funktion, die den Schritt zurücknimmt
    status_changed = Signal()
    problem = Signal(str)
    pin_place = Signal(str)

    def __init__(self, ctx, path: str):
        super().__init__()
        self.ctx = ctx
        self.history: list[str] = []
        self.future: list[str] = []
        self._worker: SearchWorker | None = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.back = IconButton("", "Zurück")
        self.forward = IconButton("", "Vor")
        self.up = IconButton("", "Übergeordneter Ordner")
        self.back.clicked.connect(self.go_back)
        self.forward.clicked.connect(self.go_forward)
        self.up.clicked.connect(self.go_up)
        self.address = QLineEdit()
        add_text_menu(ctx, self.address)
        self.address.returnPressed.connect(lambda: self.open(self.address.text().strip()))
        self.search = QLineEdit()
        add_text_menu(ctx, self.search)
        self.search.setPlaceholderText("Blitzsuche, ? für KI")
        self.search.returnPressed.connect(self._ask_ai)
        self.search.setMaximumWidth(170)
        self.search.setMinimumWidth(90)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_later)
        for button in (self.back, self.forward, self.up):
            bar.addWidget(button)
        bar.addWidget(self.address, 3)
        bar.addWidget(self.search, 1)
        box.addLayout(bar)

        self.model = TrackedModel(self)
        self.model.setRootPath("")
        self.model.setReadOnly(False)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setRootIsDecorated(False)
        self.view.setSortingEnabled(True)
        saved = read_store()
        self.view.sortByColumn(saved.get("sort_column", 0), Qt.SortOrder(saved.get("sort_order", 0)))
        self.view.header().sortIndicatorChanged.connect(lambda column, order: self._store_sort(column, order))
        self.view.setUniformRowHeights(True)
        # Schneller Doppelklick öffnet. Umbenennen läuft über einen langsamen zweiten Klick, F2 oder das Menü.
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._last_click = ("", 0.0)
        self.view.clicked.connect(lambda index: self._slow_click(
            self.model.filePath(index.siblingAtColumn(0)), index.column() == 0))
        self.view.setItemDelegateForColumn(0, RenameDelegate(ctx, self.view))
        self.view.setAlternatingRowColors(False)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDropIndicatorShown(True)
        self.view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        for column, width in COLUMN_WIDTHS.items():
            self.view.setColumnWidth(column, width)
        self.view.header().moveSection(3, 1)  # Reihenfolge wie im Windows-Explorer: Name, Datum, Typ, Größe
        self.view.header().moveSection(2, 3)
        self.view.setIndentation(0)
        self.view.activated.connect(self._activate)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.selectionModel().currentChanged.connect(lambda index, _prev: self.selected.emit(self.model.filePath(index)))
        self.model.fileRenamed.connect(self._renamed)
        self.model.directoryLoaded.connect(lambda _folder: self.status_changed.emit())
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.status_changed.emit())
        self.group = saved.get("group", "")
        self._transfers: set = set()
        self.tree = GroupTree(self)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Name", "Änderungsdatum", "Typ", "Größe"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 140)
        self.tree.setColumnWidth(2, 110)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setItemDelegateForColumn(0, RenameDelegate(ctx, self.tree))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemActivated.connect(self._tree_activated)
        self.tree.itemClicked.connect(lambda item, column: self._slow_click(item.toolTip(0), column == 0))
        self.tree.itemChanged.connect(self._tree_renamed)
        self.tree.currentItemChanged.connect(lambda item, _p: item and item.toolTip(0) and self.selected.emit(item.toolTip(0)))
        self._reload_timer = QTimer(self, singleShot=True, interval=300)
        self._reload_timer.timeout.connect(self._fill_groups)
        self.model.rowsInserted.connect(self._reload_timer.start)
        self.model.rowsRemoved.connect(self._reload_timer.start)
        self.view_mode = saved.get("view_mode", "auto")
        self.blocks = BlockView(self)
        self.blocks.setModel(self.model)
        self.blocks.setViewMode(QListView.ViewMode.IconMode)
        self.blocks.setResizeMode(QListView.ResizeMode.Adjust)
        self.blocks.setMovement(QListView.Movement.Static)
        self.blocks.setIconSize(QSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        self.blocks.setGridSize(QSize(BLOCK_WIDTH, BLOCK_HEIGHT))
        self.blocks.setWordWrap(True)
        self.blocks.setUniformItemSizes(True)
        self.blocks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.blocks.setItemDelegate(RenameDelegate(ctx, self.blocks))
        self.blocks.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.blocks.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.blocks.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.blocks.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.blocks.activated.connect(self._activate)
        self.blocks.customContextMenuRequested.connect(self._menu)
        self.blocks.clicked.connect(lambda index: self._slow_click(self.model.filePath(index), True))
        self.blocks.selectionModel().currentChanged.connect(
            lambda index, _prev: self.selected.emit(self.model.filePath(index)))
        self._ai_job: AiFileSearch | None = None
        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._menu)
        self.in_favorites = False
        self.results.itemActivated.connect(lambda item: self.open(item.toolTip()))
        self.results.currentItemChanged.connect(lambda item, _p: item and self.selected.emit(item.toolTip()))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.blocks)
        self.stack.addWidget(self.results)
        self.stack.addWidget(self.tree)
        box.addWidget(self.stack, 1)
        self._search_timer = QTimer(self, singleShot=True, interval=220)
        self._search_timer.timeout.connect(self._search)
        self.icons = QFileIconProvider()
        self.context_menu = ContextMenu(ctx)
        self.thumbnails: dict[str, QIcon] = {}
        self._asked_thumbnails: set[str] = set()
        self.thumbnail_loader = ThumbnailLoader(self)
        self.thumbnail_loader.loaded.connect(self._thumbnail_ready)
        self.path = ""
        self.open(path, remember=False)

    # --- Navigation -------------------------------------------------------------
    def open(self, path: str, remember: bool = True) -> None:
        path = os.path.expandvars(path)
        if os.path.isfile(path):
            self.open_file(path)
            return
        if not os.path.isdir(path):
            self.address.setText(self.path)
            return
        if remember and self.path:
            self.history.append(self.path)
            self.future.clear()
        self.path = os.path.normpath(path)
        root = self.model.setRootPath(self.path)
        self.view.setRootIndex(root)
        self.blocks.setRootIndex(root)
        self.address.setText(self.path)
        self.search.clear()
        self._show_list()
        self.navigated.emit(self.path)
        self.status_changed.emit()

    def open_file(self, path: str) -> None:
        is_text = os.path.splitext(path)[1].lower() in TEXT_TYPES
        if is_text and self.ctx.config.get("replacements.editor"):
            subprocess.Popen(app_command("editor", path), creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            win_api.shell_open(path)

    def _store_sort(self, column: int, order) -> None:
        write_store(sort_column=column, sort_order=Qt.SortOrder(order).value)

    def thumbnail(self, path: str) -> QIcon | None:
        """Bildvorschau für die Block-Ansicht; in der Liste bleibt es beim kleinen Symbol wie in Windows."""
        if self.stack.currentWidget() is not self.blocks or os.path.splitext(path)[1].lower() not in IMAGE_TYPES:
            return None
        if path not in self._asked_thumbnails:
            self._asked_thumbnails.add(path)
            self.thumbnail_loader.waiting.put(path)
        return self.thumbnails.get(path)

    def _thumbnail_ready(self, path: str, image: QImage) -> None:
        self.thumbnails[path] = QIcon(QPixmap.fromImage(image))
        index = self.model.index(path)
        self.model.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def status_text(self) -> str:
        count = self.model.rowCount(self.view.rootIndex())
        chosen = len(self.selection())
        return f"{count} Elemente" + (f"   ·   {chosen} ausgewählt" if chosen else "")

    def cut(self) -> None:
        self.copy_selection(cut=True)

    def delete_selection(self) -> None:
        self.recycle(self.selection())

    def _activate(self, index) -> None:
        self._last_click = ("", 0.0)
        self.open(self.model.filePath(index))

    def _slow_click(self, path: str, on_name: bool) -> None:
        now = time.monotonic() * 1000
        last_path, last_time = self._last_click
        self._last_click = (path, now)
        slow = is_slow_click(path == last_path, now - last_time, QApplication.doubleClickInterval())
        plain = QApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier
        if path and on_name and slow and plain and len(self.selection()) == 1:
            self._last_click = ("", 0.0)
            self.rename_selected()

    def go_back(self) -> None:
        if self.history:
            self.future.append(self.path)
            self.open(self.history.pop(), remember=False)

    def go_forward(self) -> None:
        if self.future:
            self.history.append(self.path)
            self.open(self.future.pop(), remember=False)

    def go_up(self) -> None:
        parent = os.path.dirname(self.path)
        if parent and parent != self.path:
            self.open(parent)

    # --- Blitzsuche ----------------------------------------------------------------
    def _search_later(self, _text: str) -> None:
        self._search_timer.start()

    def _search(self) -> None:
        if self._worker:
            self._worker.cancelled = True
        text = self.search.text().strip()
        if text.startswith("?"):
            return  # die KI fragt erst Enter, beim Tippen ginge sonst jeder Buchstabe an Google
        self.in_favorites = False
        self.results.clear()
        if len(text) < 2:
            self._show_list()
            return
        self.stack.setCurrentWidget(self.results)
        self._worker = SearchWorker(self.path, text, self)
        self._worker.hit.connect(self._add_hit)
        self._worker.start()

    def _ask_ai(self) -> None:
        text = self.search.text().strip()
        if not text.startswith("?") or len(text) < 3 or self._ai_job is not None:
            return
        key = self.ctx.config.api_key()
        if not key:
            self.problem.emit(i18n.translate("Für die KI braucht es einen API-Key (Setup)"))
            return
        self.in_favorites = False
        self.results.clear()
        self.stack.setCurrentWidget(self.results)
        self.search.setPlaceholderText(i18n.translate("Die KI sucht..."))
        models = [model.strip() for model in self.ctx.config.get("nova.models").split(",") if model.strip()]
        self._ai_job = AiFileSearch(self.path, text[1:].strip(), key, models, self)
        self._ai_job.found.connect(self._ai_found)
        self._ai_job.start()

    def _ai_found(self, paths: list, error: str) -> None:
        self._ai_job = None
        self.search.setPlaceholderText(i18n.translate("Blitzsuche, ? für KI"))
        if error:
            self.problem.emit(error)
        elif not paths:
            self.problem.emit(i18n.translate("Die KI hat nichts Passendes gefunden."))
        for path in paths:
            self._add_hit(path)

    def _add_hit(self, path: str) -> None:
        item = QListWidgetItem(self.icons.icon(self.model.fileInfo(self.model.index(path))),
                               os.path.relpath(path, self.path))
        item.setToolTip(path)
        self.results.addItem(item)

    # --- Sortieren und Gruppieren -----------------------------------------------------
    def _show_list(self) -> None:
        if self.group:
            self._fill_groups()
            self.stack.setCurrentWidget(self.tree)
        else:
            self.stack.setCurrentWidget(self._file_view())

    def _file_view(self):
        """Blöcke, sobald die Liste so breit würde, dass ihre Spalten nur noch auseinanderlaufen."""
        wide = self.width() >= AUTO_BLOCKS_WIDTH
        return self.blocks if self.view_mode == "blocks" or (self.view_mode == "auto" and wide) else self.view

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.stack.currentWidget() in (self.view, self.blocks):
            self.stack.setCurrentWidget(self._file_view())

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        write_store(view_mode=mode)
        self.set_group("")

    def sort_menu(self):
        menu = themed_menu(self.ctx, self)
        column = self.view.header().sortIndicatorSection()
        order = self.view.header().sortIndicatorOrder()

        def add(label: str, checked: bool, handler) -> QAction:
            # Eigene Funktion: "triggered" übergibt den Haken und würde Standardwerte eines Lambdas überschreiben.
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(checked)
            action.triggered.connect(lambda _checked=False: handler())
            return action

        columns = QActionGroup(menu)
        for label, index in SORT_COLUMNS.items():
            columns.addAction(add(label, index == column, lambda c=index: self.set_sort(c, order)))
        menu.addSeparator()
        for label, value in (("Aufsteigend", Qt.SortOrder.AscendingOrder), ("Absteigend", Qt.SortOrder.DescendingOrder)):
            add(label, value == order, lambda o=value: self.set_sort(column, o))
        menu.addSeparator()
        for label, value in (("Nicht gruppieren", ""), ("Nach Dateityp gruppieren", "type"), ("Nach Datum gruppieren", "date"), ("Mit KI nach Themen gruppieren", "ai")):
            add(label, value == self.group, lambda g=value: self.set_group(g))
        menu.addSeparator()
        for label, value in (("Ansicht automatisch", "auto"), ("Immer als Liste", "list"), ("Immer als Blöcke", "blocks")):
            add(label, value == self.view_mode, lambda m=value: self.set_view_mode(m))
        return menu

    def set_sort(self, column: int, order) -> None:
        self.view.sortByColumn(column, order)
        self._fill_groups()  # die Gruppenansicht sortiert selbst, das Modell gilt nur für die Liste

    def set_group(self, group: str) -> None:
        self.group = group
        write_store(group=group)
        # --- [Antigravity Start: KI-Gruppen bei Bedarf vorbereiten] ---
        if group == "ai" and getattr(self, "_ai_groups_folder", None) != self.path:  # Gruppen gelten nur für ihren Ordner
            try:
                names = [e.name for e in os.scandir(self.path) if e.is_file()]
                cats = heuristic_categorize(names)
                self._ai_groups = {fname: cat for cat, flist in cats.items() for fname in flist}
            except OSError:
                self._ai_groups = {}
            self._ai_groups_folder = self.path
        # --- [Antigravity Ende] ---
        self._show_list()

    def _fill_groups(self) -> None:
        if not self.group:
            return
        self.tree.clear()
        try:
            header = self.view.header()
            groups = grouped_entries(self.path, self.group, time.time(), header.sortIndicatorSection(),
                                     header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder,
                                     custom_groups=getattr(self, "_ai_groups", None)
                                     if getattr(self, "_ai_groups_folder", None) == self.path else None)  # [Antigravity]
        except OSError:
            return
        for label, entries in groups.items():
            head = QTreeWidgetItem([f"{label} ({len(entries)})"])
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(head)
            for entry in entries:
                info = entry.stat()
                size = "" if entry.is_dir() else f"{info.st_size / 1024:,.0f} KB".replace(",", ".")
                date = QDateTime.fromSecsSinceEpoch(int(info.st_mtime)).toString("dd.MM.yyyy HH:mm")
                item = QTreeWidgetItem([entry.name, date, type_label(entry.name, entry.is_dir()), size])
                item.setToolTip(0, entry.path)
                item.setIcon(0, self.icons.icon(QFileInfo(entry.path)))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                head.addChild(item)
            head.setExpanded(True)

    def _tree_activated(self, item, _column) -> None:
        self._last_click = ("", 0.0)
        if item.toolTip(0):
            self.open(item.toolTip(0))

    def _tree_renamed(self, item, column: int) -> None:
        """Die Gruppenansicht hat kein Dateimodell, das Umbenennen läuft hier von Hand."""
        path = item.toolTip(0)
        name = item.text(0).strip()
        old = os.path.basename(path)
        if column != 0 or not path or name == old:
            return
        folder = os.path.dirname(path)
        try:
            if not name:
                raise OSError("Leerer Name")
            os.rename(path, os.path.join(folder, name))
        except OSError:  # Name schon vergeben oder ungültig: der alte Name bleibt
            item.setText(0, old)
            return
        item.setToolTip(0, os.path.join(folder, name))
        self._renamed(folder, old, name)

    # --- Dateiaktionen ---------------------------------------------------------------
    def selection(self) -> list[str]:
        if self.stack.currentWidget() is self.results:
            return [item.toolTip() for item in self.results.selectedItems()]
        if self.stack.currentWidget() is self.tree:
            return [item.toolTip(0) for item in self.tree.selectedItems() if item.toolTip(0)]
        if self.stack.currentWidget() is self.blocks:
            return [self.model.filePath(i) for i in self.blocks.selectedIndexes()]
        return [self.model.filePath(i) for i in self.view.selectionModel().selectedRows()]

    def rename_selected(self) -> None:
        if self.stack.currentWidget() is self.tree:
            item = self.tree.currentItem()
            if item and item.toolTip(0):
                self.tree.editItem(item, 0)
        elif self.stack.currentWidget() is self.blocks and self.blocks.currentIndex().isValid():
            self.blocks.edit(self.blocks.currentIndex())
        elif self.stack.currentWidget() is self.view and self.view.currentIndex().isValid():
            self.view.edit(self.view.currentIndex().siblingAtColumn(0))

    def recycle(self, paths: list[str]) -> None:
        if paths and win_api.move_to_recycle_bin(paths):
            names = paths[0] if len(paths) == 1 else f"{len(paths)} Elemente"
            self.undoable.emit(f"Löschen von {os.path.basename(names)}", lambda: win_api.restore_from_recycle_bin(paths))

    def toggle_favorite(self, paths: list[str]) -> None:
        FAVORITES.toggle(paths)
        for view in (self.view, self.blocks, self.tree):
            view.viewport().update()
        if self.in_favorites and self.stack.currentWidget() is self.results:
            self.show_favorites()  # was keinen Stern mehr hat, verschwindet sofort aus der Liste

    def show_favorites(self) -> None:
        self.results.clear()
        self.in_favorites = True
        self.stack.setCurrentWidget(self.results)
        for path in FAVORITES.existing():
            item = QListWidgetItem(icon_for_target(path), os.path.basename(path) or path)
            item.setToolTip(path)
            self.results.addItem(item)
        self.status_changed.emit()

    def _renamed(self, folder: str, old: str, new: str) -> None:
        FAVORITES.rename(os.path.join(folder, old), os.path.join(folder, new))
        self.undoable.emit(f"Umbenennen in {new}", lambda: os.rename(os.path.join(folder, new), os.path.join(folder, old)))

    # --- Kopieren, Verschieben, Entpacken ------------------------------------------------
    def drop_urls(self, data, folder: str, modifiers) -> None:
        paths = [url.toLocalFile() for url in data.urls() if url.isLocalFile()]
        same_drive = paths and os.path.splitdrive(paths[0])[0].lower() == os.path.splitdrive(folder)[0].lower()
        copy = bool(modifiers & Qt.KeyboardModifier.ControlModifier) or not same_drive  # wie im Windows-Explorer
        paths = [path for path in paths if os.path.dirname(path.rstrip("/\\")) != folder.rstrip("/\\") or copy]
        if paths:
            self.transfer([os.path.normpath(path) for path in paths], folder, move=not copy)

    def transfer(self, paths: list[str], folder: str, move: bool) -> None:
        worker = TransferWorker(paths, folder, move, self)
        worker.finished_with.connect(lambda done, _x, error: self._transferred(done, move, error))
        worker.finished.connect(lambda w=worker: (self._transfers.discard(w), w.deleteLater()))
        self._transfers.add(worker)
        worker.start()

    def _transferred(self, done: list, move: bool, error: str) -> None:
        if error:
            self.problem.emit(error)
        if not done:
            return

        def undo() -> None:
            for source, target in reversed(done):
                if move:
                    shutil.move(target, source)
                else:
                    win_api.move_to_recycle_bin([target])
        self.undoable.emit("Verschieben" if move else "Kopieren", undo)
        self._reload_timer.start()

    def copy_selection(self, cut: bool = False) -> None:
        paths = self.selection()
        if not paths:
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        effect = CUT_EFFECT if cut else COPY_EFFECT
        data.setData("Preferred DropEffect", QByteArray(effect.to_bytes(4, "little")))
        QGuiApplication.clipboard().setMimeData(data)

    def paste(self) -> None:
        data = QGuiApplication.clipboard().mimeData()
        paths = [os.path.normpath(url.toLocalFile()) for url in data.urls() if url.isLocalFile()]
        effect = bytes(data.data("Preferred DropEffect"))
        cut = int.from_bytes(effect[:4], "little") == CUT_EFFECT if effect else False
        if paths:
            self.transfer(paths, self.path, move=cut)
            if cut:
                QGuiApplication.clipboard().clear()

    def duplicate(self) -> None:
        for path in self.selection():
            self.transfer([path], os.path.dirname(path), move=False)

    def extract(self, path: str) -> None:
        target = free_name(os.path.dirname(path), archive_stem(os.path.basename(path)))
        try:
            shutil.unpack_archive(path, target)
        except (OSError, ValueError, shutil.ReadError) as exc:
            self.problem.emit(f"Nicht entpackbar: {exc}")
            return
        self.undoable.emit("Entpacken", lambda: win_api.move_to_recycle_bin([target]))

    def _menu(self, _position) -> None:
        paths = self.selection()
        self.context_menu.show_custom(QCursor.pos(), lambda menu: self._fill_menu(menu, paths))

    def _open_ai_sort(self, target_paths: list[str] | None = None) -> None:
        # [Antigravity: KI-Sortierdialog öffnen]
        dialog = AiSortDialog(self.ctx, self, target_paths, parent=self.window())
        self._active_ai_sort = dialog
        dialog.show()

    def _fill_menu(self, menu: ContextMenu, paths: list[str]) -> None:
        if paths:
            self._file_items(menu, paths)
            return
        menu.add_sort_row(lambda key: self.set_sort(SORT_KEYS[key], self.view.header().sortIndicatorOrder()))
        menu.add_item("Einfügen", G.DOC, self.paste, QGuiApplication.clipboard().mimeData().hasUrls())
        menu.addSeparator()
        menu.add_item("Neuer Ordner", G.FOLDER, self.new_folder)
        menu.add_item("Diesen Ordner zum Schnellzugriff", G.PIN, lambda: self.pin_place.emit(self.path))
        menu.add_item("Dateien mit KI sortieren …", G.QUICK, lambda: self._open_ai_sort())  # [Antigravity]
        menu.add_item("Terminal hier öffnen", G.TERMINAL, self._open_terminal)
        menu.add_item("Im Windows-Explorer öffnen", G.WINDOWS, lambda: win_api.shell_open(self.path))

    def _file_items(self, menu: ContextMenu, paths: list[str]) -> None:
        menu.add_item("Öffnen", G.FOLDER if os.path.isdir(paths[0]) else G.DOC, lambda: self.open(paths[0]))
        if len(paths) == 1 and self.stack.currentWidget() is not self.results:
            menu.add_item("Umbenennen", G.DOC, self.rename_selected)
        menu.add_item("Ausschneiden", G.LIST, lambda: self.copy_selection(cut=True))
        menu.add_item("Kopieren", G.LIST, self.copy_selection)
        menu.add_item("Duplizieren", G.LIST, self.duplicate)
        menu.add_item("Pfad kopieren", G.LIST, lambda: QGuiApplication.clipboard().setText("\n".join(paths)))
        if len(paths) == 1 and paths[0].lower().endswith(ARCHIVE_TYPES):
            menu.add_item("Hier entpacken", G.FOLDER, lambda: self.extract(paths[0]))
        folders = [path for path in paths if os.path.isdir(path)]
        if folders:
            menu.add_item("Zum Schnellzugriff", G.PIN, lambda: [self.pin_place.emit(path) for path in folders])
        marked = all(FAVORITES.has(path) for path in paths)
        menu.add_item("Aus Favoriten entfernen" if marked else "Zu Favoriten", G.PIN, lambda: self.toggle_favorite(paths))
        menu.add_item("Auswahl mit KI sortieren …", G.QUICK, lambda: self._open_ai_sort(paths))  # [Antigravity]
        menu.add_item("In den Papierkorb", G.TRASH, lambda: self.recycle(paths))
        menu.addSeparator()
        menu.add_shell_items(int(self.window().winId()), paths)

    def _open_terminal(self) -> None:
        if self.ctx.config.get("replacements.terminal"):
            subprocess.Popen(app_command("terminal", self.path), creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            win_api.shell_open("cmd.exe", None, self.path)

    def new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "Neuer Ordner", "Name:")
        if ok and name.strip():
            self.create_folder(name.strip())

    def create_folder(self, name: str) -> None:
        folder = os.path.join(self.path, name)
        os.makedirs(folder, exist_ok=True)
        self.undoable.emit(f"Neuer Ordner {name}", lambda: os.rmdir(folder))


class PreviewPane(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(290)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 0, 0)
        self.name = QLabel(objectName="MediaTitle")
        self.name.setWordWrap(True)
        self.info = QLabel(EMPTY_PREVIEW, objectName="Muted")
        self.info.setWordWrap(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path = ""
        self.encoding = "utf-8"
        self.newline = "\n"
        self.text = QPlainTextEdit()
        self.text.hide()
        self.text.modificationChanged.connect(self._modified)
        QShortcut(QKeySequence.StandardKey.Save, self.text, self.save, context=Qt.ShortcutContext.WidgetShortcut)
        self.save_button = QPushButton("Speichern", objectName="Primary")
        self.save_button.clicked.connect(self.save)
        self.save_button.hide()
        box.addWidget(QLabel("Vorschau", objectName="PanelTitle"))
        box.addWidget(self.name)
        box.addWidget(self.info)
        box.addWidget(self.image)
        box.addWidget(self.text, 100)
        box.addWidget(self.save_button)
        box.addStretch(1)

    def _modified(self, changed: bool) -> None:
        self.save_button.setVisible(changed)

    def save(self) -> bool:
        if not self.text.document().isModified():
            return True
        try:
            Path(self.path).write_bytes(self.text.toPlainText().replace("\n", self.newline).encode(self.encoding, "replace"))
        except OSError as exc:
            self.info.setText(f"Nicht gespeichert: {exc}")
            return False
        self.text.document().setModified(False)
        return True

    def keep_changes(self) -> None:
        """Vor dem Weiterklicken: Ausgeschnittenes darf nicht still verloren gehen."""
        if self.text.document().isModified():
            answer = QMessageBox.question(self, "PopWin Explorer", f"Änderungen an {os.path.basename(self.path)} speichern?")
            if answer == QMessageBox.StandardButton.Yes:
                self.save()
            self.text.document().setModified(False)

    def show_path(self, path: str) -> None:
        self.keep_changes()
        self.image.clear()
        self.text.hide()
        if not path or not os.path.exists(path):
            self.name.clear()
            self.info.setText(EMPTY_PREVIEW)
            return
        file = Path(path)
        self.name.setText(file.name or path)
        if file.is_dir():
            self.info.setText("Ordner")
            return
        size = file.stat().st_size
        self.info.setText(f"{size / 1024 ** 2:.1f} MB" if size > 1024 ** 2 else f"{size / 1024:.1f} KB")
        if file.suffix.lower() in IMAGE_TYPES:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.image.setPixmap(pixmap.scaled(QSize(270, 270), Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
            return
        try:
            data = file.read_bytes()[:TEXT_LIMIT]
        except OSError:
            return
        if b"\0" not in data[:2048]:
            content, self.encoding = decode(data)
            self.path, self.newline = path, line_ending(content)
            self.text.setPlainText(content.replace("\r\n", "\n"))
            whole = size <= TEXT_LIMIT  # vom Rest einer großen Datei ist hier nichts zu sehen, Speichern schnitte ihn ab
            self.text.setReadOnly(not whole)
            if not whole:
                self.info.setText(f"{self.info.text()}. Nur der Anfang ist zu sehen, deshalb lässt sich hier nichts ändern.")
            self.text.show()


class TabPage(QSplitter):
    def __init__(self, ctx, path: str):
        super().__init__(Qt.Orientation.Horizontal)
        self.ctx = ctx
        self.panes = [Pane(ctx, path)]
        self.addWidget(self.panes[0])

    def toggle_split(self, on_selected, on_undoable) -> None:
        if len(self.panes) == 1:
            pane = Pane(self.ctx, self.panes[0].path)
            pane.selected.connect(on_selected)
            pane.undoable.connect(on_undoable)
            self.panes.append(pane)
            self.addWidget(pane)
        else:
            self.panes.pop().deleteLater()


def is_torn_out(y: float, height: int) -> bool:
    return y < -TEAR_DISTANCE or y > height + TEAR_DISTANCE


class TearTabBar(QTabBar):
    """Ein Tab, den man weit genug aus der Leiste zieht, wird zu einem eigenen Fenster."""
    torn = Signal(int, QPoint)

    def __init__(self):
        super().__init__()
        self._holding = False
        # Hinhalten beim Ziehen: Bleibt eine gezogene Datei über einem Tab stehen, wechselt der Explorer dorthin.
        self.setAcceptDrops(True)
        self._drag_hover = DragHover(self)
        self._drag_hover.held.connect(lambda index: index < self.count() and self.setCurrentIndex(index))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.accept()

    def dragMoveEvent(self, event) -> None:
        index = self.tabAt(event.position().toPoint())
        self._drag_hover.over(index if index >= 0 and index != self.currentIndex() else None)
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.ignore()  # ablegen lässt sich auf einem Tab nichts, nur wechseln

    def dragLeaveEvent(self, event) -> None:
        self._drag_hover.leave()

    def dropEvent(self, event) -> None:
        self._drag_hover.leave()
        event.ignore()

    def mousePressEvent(self, event) -> None:
        self._holding = event.button() == Qt.MouseButton.LeftButton and self.tabAt(event.position().toPoint()) >= 0
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        if not self._holding or self.count() < 2 or not is_torn_out(event.position().y(), self.height()):
            return
        self._holding = False
        # Die Leiste beendet erst ihr eigenes Verschieben, sonst hängt sie am gelöschten Tab.
        self.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, event.position(), event.globalPosition(),
                                           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                           Qt.KeyboardModifier.NoModifier))
        index, position = self.currentIndex(), event.globalPosition().toPoint()
        QTimer.singleShot(0, lambda: self.torn.emit(index, position))



# --- [Antigravity Start: Dialog zum KI-Dateisortieren & Aufräumen (Rest: Claude Code)] ---
class AiSortDialog(NativeWindow):
    def __init__(self, ctx, pane: "Pane", target_paths: list[str] | None = None, parent=None):
        super().__init__(ctx, "Dateien mit KI sortieren", "apps/explorer")
        self.pane = pane
        self.folder = pane.path
        self.target_paths = target_paths or []
        self.resize(700, 560)
        if hasattr(self.titlebar, "minimize"):
            self.titlebar.minimize.hide()
        if hasattr(self.titlebar, "maximize"):
            self.titlebar.maximize.hide()

        if self.target_paths:
            self.file_names = [os.path.basename(p) for p in self.target_paths if os.path.isfile(p)]
        else:
            try:
                self.file_names = [e.name for e in os.scandir(self.folder) if e.is_file()]
            except OSError:
                self.file_names = []

        self.categories: dict[str, list[str]] = {}
        self.icons = pane.icons

        box = QVBoxLayout(self.content)
        box.setContentsMargins(18, 14, 18, 16)
        box.setSpacing(10)

        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("Dateien mit KI sortieren", objectName="Heading")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #FFFFFF;")
        folder_display = os.path.basename(self.folder.rstrip("\\/")) or self.folder
        self.info_label = QLabel(f"Ordner: {folder_display}  ·  {len(self.file_names)} Dateien gefunden")
        self.info_label.setStyleSheet("color: rgba(220, 225, 255, 0.7); font-size: 12px;")
        self.status_label = QLabel("⏳ Analysiere Dateien mit KI …")
        self.status_label.setStyleSheet("color: #F2C94C; font-size: 12px; font-weight: 600;")

        head.addWidget(title)
        head.addWidget(self.info_label)
        head.addWidget(self.status_label)
        box.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        box.addWidget(self.progress)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Datei / Zielordner", "Größe", "Aktion / Ziel"])
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 170)
        self.tree.setStyleSheet("QTreeWidget { background: rgba(16, 18, 30, 0.6); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; }")
        self.tree.itemChanged.connect(self._item_toggled)
        box.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self.cancel_btn = QPushButton("Abbrechen", objectName="Secondary")
        self.cancel_btn.clicked.connect(self.close)
        bottom.addWidget(self.cancel_btn)

        bottom.addStretch(1)

        self.group_btn = QPushButton("Nur in Ansicht gruppieren", objectName="Secondary")
        self.group_btn.setToolTip("Gruppiert die Dateien im Explorer nach diesen KI-Kategorien, ohne sie physisch zu verschieben")
        self.group_btn.clicked.connect(self._apply_view_grouping)
        self.group_btn.setEnabled(False)
        bottom.addWidget(self.group_btn)

        self.sort_btn = QPushButton("Jetzt in Ordner sortieren", objectName="Primary")
        self.sort_btn.clicked.connect(self._apply_move)
        self.sort_btn.setEnabled(False)
        bottom.addWidget(self.sort_btn)

        box.addLayout(bottom)

        if not self.file_names:
            self.status_label.setText("Keine Dateien im Ordner vorhanden.")
            self.status_label.setStyleSheet("color: rgba(220, 225, 255, 0.5);")
            self.progress.hide()
            return

        key = self.ctx.config.api_key()
        models = [m.strip() for m in self.ctx.config.get("nova.models").split(",") if m.strip()] if self.ctx.config.get("nova.models") else []
        if not models:
            models = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]
        self.worker = AiSortWorker(self.folder, self.file_names, key, models, self)
        self.worker.ready.connect(self._results_ready)
        self.worker.start()

    def _results_ready(self, categories: dict[str, list[str]], is_ai: bool, note: str) -> None:
        self.categories = categories
        self.progress.hide()
        badge = "✨ Gemini KI" if is_ai else "⚡ Lokale Heuristik"
        self.status_label.setText(f"{badge}: {len(categories)} Kategorien vorgeschlagen ({note})")
        self.status_label.setStyleSheet("color: #41cd52; font-size: 12px; font-weight: 600;" if is_ai else "color: #79a6dc; font-size: 12px;")
        self._populate_tree()
        self.group_btn.setEnabled(True)
        self.sort_btn.setEnabled(True)

    def _populate_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for cat_name, fnames in self.categories.items():
            cat_item = QTreeWidgetItem([f"📁 {cat_name} ({len(fnames)})", "", f"→ {cat_name}\\"])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Checked)
            cat_item.setData(0, Qt.ItemDataRole.UserRole, "category")
            cat_item.setData(0, Qt.ItemDataRole.UserRole + 1, cat_name)

            for fname in sorted(fnames):
                fpath = os.path.join(self.folder, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    size_str = f"{fsize / 1024:,.0f} KB".replace(",", ".") if fsize >= 1024 else f"{fsize} B"
                except OSError:
                    size_str = ""
                file_item = QTreeWidgetItem([fname, size_str, f"→ {cat_name}\\"])
                file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                file_item.setCheckState(0, Qt.CheckState.Checked)
                file_item.setIcon(0, self.icons.icon(QFileInfo(fpath)))
                file_item.setData(0, Qt.ItemDataRole.UserRole, "file")
                file_item.setData(0, Qt.ItemDataRole.UserRole + 1, (fname, cat_name))
                cat_item.addChild(file_item)

            self.tree.addTopLevelItem(cat_item)
            cat_item.setExpanded(True)
        self.tree.blockSignals(False)
        self._update_button_label()

    def _item_toggled(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        self.tree.blockSignals(True)
        kind = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "category":
            state = item.checkState(0)
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
        self.tree.blockSignals(False)
        self._update_button_label()

    def _update_button_label(self) -> None:
        count = sum(1 for i in range(self.tree.topLevelItemCount())
                    for j in range(self.tree.topLevelItem(i).childCount())
                    if self.tree.topLevelItem(i).child(j).checkState(0) == Qt.CheckState.Checked)
        self.sort_btn.setText(f"Jetzt in Ordner sortieren ({count})")
        self.sort_btn.setEnabled(count > 0)

    def _apply_view_grouping(self) -> None:
        if not self.categories:
            return
        mapping = {}
        for cat, items in self.categories.items():
            for item in items:
                mapping[item] = cat
        self.pane._ai_groups = mapping
        self.pane._ai_groups_folder = self.folder
        self.pane.set_group("ai")
        self.close()

    def _apply_move(self) -> None:
        moved = []
        failed: list[str] = []
        created_dirs = set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            cat_name = top.data(0, Qt.ItemDataRole.UserRole + 1)
            target_dir = os.path.join(self.folder, cat_name)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) != Qt.CheckState.Checked:
                    continue
                fname, _ = child.data(0, Qt.ItemDataRole.UserRole + 1)
                src = os.path.join(self.folder, fname)
                if not os.path.isfile(src) or inside_itself(src, target_dir):
                    continue
                try:
                    if not os.path.exists(target_dir):
                        os.makedirs(target_dir, exist_ok=True)
                        created_dirs.add(target_dir)
                    moved.append((src, transfer_file(src, target_dir, move=True)))
                except OSError as exc:  # etwa eine geöffnete Datei: die anderen trotzdem sortieren
                    log.info("KI-Sortierung: %s nicht verschoben: %s", fname, exc)
                    failed.append(fname)

        if moved:
            pane = self.pane
            folder = self.folder
            def undo():
                for orig_src, orig_dst in moved:
                    if os.path.exists(orig_dst) and not os.path.exists(orig_src):  # nie eine neue Datei überschreiben
                        try:
                            shutil.move(orig_dst, orig_src)
                        except OSError:
                            pass
                for cdir in created_dirs:
                    try:
                        if os.path.isdir(cdir) and not os.listdir(cdir):
                            os.rmdir(cdir)
                    except OSError:
                        pass
                pane.open(folder)

            self.pane.undoable.emit(f"KI-Sortierung ({len(moved)} Dateien)", undo)

        self.pane.open(self.folder)
        if failed:
            QMessageBox.information(self, "PopWin Explorer", f"{len(failed)} Datei(en) ließen sich nicht verschieben, "
                                    f"etwa weil sie gerade offen sind:\n" + "\n".join(failed[:10]))
        self.close()
# --- [Antigravity Ende] ---

class PlacesList(QListWidget):
    """Seitenleiste: Einträge per Maus anordnen, Ordner hineinziehen, per Rechtsklick entfernen."""

    def __init__(self, window: "ExplorerWindow", removable: bool):
        super().__init__(objectName="ExplorerPlaces")
        self.explorer = window
        self.removable = removable
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def startDrag(self, actions) -> None:
        super().startDrag(actions)
        self.explorer.store_places()

    def _foreign(self, event) -> bool:
        """Einträge bleiben in ihrer Gruppe: Feste Orte wandern nicht in den Schnellzugriff und umgekehrt."""
        return isinstance(event.source(), PlacesList) and event.source() is not self

    def dragEnterEvent(self, event) -> None:
        if self._foreign(event):
            event.ignore()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._foreign(event):
            event.ignore()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self._foreign(event):
            event.ignore()
            return
        if event.source() is self or not event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.MoveAction)
            super().dropEvent(event)
            return
        for url in event.mimeData().urls():
            if os.path.isdir(url.toLocalFile()):
                self.explorer.add_place(url.toLocalFile())
        event.acceptProposedAction()

    def _menu(self, position) -> None:
        row = self.indexAt(position).row()
        if row < 0 or not self.removable:
            return
        menu = themed_menu(self.explorer.ctx, self)
        menu.addAction("Aus Schnellzugriff entfernen", lambda: self.explorer.remove_place(row))
        menu.popup(self.viewport().mapToGlobal(position))


class ExplorerWindow(NativeWindow):
    def __init__(self, ctx, path: str):
        super().__init__(ctx, "PopWin Explorer", "apps/explorer")
        self.resize(1160, 720)
        self.tabbar = TearTabBar()
        self.tabbar.torn.connect(self.tear_tab)
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.currentChanged.connect(self._tab_changed)
        add = IconButton(G.NEW, "Neuer Tab")
        add.clicked.connect(lambda: self.new_tab(self.current_pane().path))
        split = IconButton("", "Geteilte Ansicht")
        split.clicked.connect(self.toggle_split)
        self.undo_button = IconButton("\uE7A7", "Rückgängig (Strg + Z)")
        self.undo_button.clicked.connect(self.undo)
        self.undo_button.setEnabled(False)
        self.history: list[tuple[str, object]] = []
        self.titlebar.middle.addWidget(self.tabbar, 1)
        self.titlebar.middle.addWidget(self.undo_button)
        self.titlebar.middle.addWidget(add)
        self.titlebar.middle.addWidget(split)
        self.tabs: list[TabPage] = []

        row = QHBoxLayout(self.content)
        row.setContentsMargins(10, 4, 10, 10)
        row.setSpacing(6)
        self.places = PlacesList(self, removable=False)
        self.quick = PlacesList(self, removable=True)
        for places in (self.places, self.quick):
            places.setIconSize(QSize(20, 20))
            places.itemClicked.connect(lambda item: self.current_pane().open(item.toolTip()))
        self.places.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.fill_places()
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(128, 128, 128, 0.45); border: none;")
        favorites = QPushButton(f"{STAR}  " + i18n.translate("Favoriten"), objectName="Secondary")
        favorites.setToolTip("Alle Favoriten zeigen")
        favorites.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        favorites.clicked.connect(lambda: self.current_pane().show_favorites())
        side = QWidget()
        side.setFixedWidth(240)
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(0, 0, 0, 0)
        side_box.setSpacing(4)
        side_box.addWidget(self.places)
        side_box.addWidget(line)
        side_box.addWidget(favorites)
        side_box.addWidget(self.quick, 1)
        self.pages = QStackedWidget()
        self.preview = PreviewPane()
        add_text_menu(ctx, self.preview.text)
        self.preview.setVisible(read_store().get("preview", False))  # im Windows-Explorer anfangs aus
        middle = QVBoxLayout()
        middle.setSpacing(2)
        middle.addLayout(self._command_bar())
        middle.addWidget(self.pages, 1)
        self.status = QLabel(objectName="Muted")
        middle.addWidget(self.status)
        row.addWidget(side)
        row.addLayout(middle, 1)
        row.addWidget(self.preview)
        self.new_tab(path)
        self.apply_theme()
        for sequence, handler in ((QKeySequence.StandardKey.AddTab, lambda: self.new_tab(self.current_pane().path)),
                                  (QKeySequence.StandardKey.Close, lambda: self.close_tab(self.tabbar.currentIndex())),
                                  (QKeySequence(Qt.Key.Key_Backspace), lambda: self.current_pane().go_up()),
                                  (QKeySequence.StandardKey.Undo, lambda: self._for_text("undo", self.undo)),
                                  (QKeySequence.StandardKey.Copy, lambda: self._for_text(
                                      "copy", self.current_pane().copy_selection)),
                                  (QKeySequence.StandardKey.Cut, lambda: self._for_text(
                                      "cut", lambda: self.current_pane().copy_selection(cut=True))),
                                  (QKeySequence.StandardKey.Paste, lambda: self._for_text("paste", self.current_pane().paste)),
                                  (QKeySequence(Qt.Key.Key_F2), lambda: self.current_pane().rename_selected()),
                                  (QKeySequence(Qt.Key.Key_Delete), lambda: self._delete()),
                                  (QKeySequence("Ctrl+Tab"), lambda: self.switch_tab(1)),
                                  (QKeySequence("Ctrl+Shift+Tab"), lambda: self.switch_tab(-1))):
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(handler)
            self.addAction(action)

    def closeEvent(self, event) -> None:
        self.preview.keep_changes()
        super().closeEvent(event)

    @staticmethod
    def _for_text(action: str, for_files) -> None:
        field = text_field()
        if field is None:
            for_files()
        else:
            getattr(field, action)()

    @staticmethod
    def _quick_places() -> list[tuple[str, str]]:
        # Über Windows erfragen: Desktop und Bilder liegen oft im OneDrive, nicht im Benutzerordner.
        from win32com.shell import shell, shellcon
        known = (("Desktop", shellcon.CSIDL_DESKTOPDIRECTORY), ("Dokumente", shellcon.CSIDL_PERSONAL),
                 ("Bilder", shellcon.CSIDL_MYPICTURES), ("Musik", shellcon.CSIDL_MYMUSIC), ("Videos", shellcon.CSIDL_MYVIDEO))
        places = [("Downloads", str(Path.home() / "Downloads"))]
        places += [(label, shell.SHGetFolderPath(0, folder, None, 0)) for label, folder in known]
        for drive in QDir.drives():
            path = drive.absolutePath()
            usage = shutil.disk_usage(path) if os.path.exists(path) else None
            if usage:
                places.append((win_api.shell_name(path), path))
        return [(label, target) for label, target in places if os.path.exists(target)]

    def _command_bar(self) -> QHBoxLayout:
        """Die häufigsten Handgriffe wie im Windows-Explorer; alles wirkt auf den aktuellen Tab."""
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.addWidget(self._command("Neu", "\uE710", "Neuer Ordner", lambda: self.current_pane().new_folder()))
        for glyph, tip, handler in (("\uE8C6", "Ausschneiden", lambda: self.current_pane().copy_selection(cut=True)),
                                    ("\uE8C8", "Kopieren", lambda: self.current_pane().copy_selection()),
                                    ("\uE77F", "Einfügen", lambda: self.current_pane().paste()),
                                    ("\uE8AC", "Umbenennen", lambda: self.current_pane().rename_selected()),
                                    ("\uE74D", "Löschen", self._delete)):
            bar.addWidget(self._command("", glyph, tip, handler))  # wie die Symbolleiste im Windows-Explorer
        bar.addStretch(1)
        bar.addWidget(self._command("KI-Sortieren", "\uE945", "Dateien mit KI analysieren und aufräumen", self._open_ai_sort))  # [Antigravity]
        bar.addWidget(self._command("Sortieren", "\uE8CB", "Sortieren, gruppieren und Ansicht", self._sort_menu))
        bar.addWidget(self._command("Vorschau", "\uE8A1", "Vorschau ein- oder ausblenden", self.toggle_preview))
        return bar

    def _command(self, label: str, glyph: str, tip: str, handler) -> QToolButton:
        button = QToolButton(objectName="CommandButton")
        button.setText(label)
        button.setToolTip(tip)
        button.setIcon(glyph_icon(glyph, 16, "#FFFFFF" if self.ctx.theme.mode == "dark" else "#202020"))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(handler)
        return button

    def _open_ai_sort(self) -> None:
        # [Antigravity: KI-Sortierdialog aufrufen]
        self.current_pane()._open_ai_sort()

    def _sort_menu(self) -> None:
        button = self.sender()
        self.current_pane().sort_menu().popup(button.mapToGlobal(button.rect().bottomLeft()))

    def toggle_preview(self) -> None:
        self.preview.setVisible(not self.preview.isVisible())
        write_store(preview=not self.preview.isHidden())

    def update_status(self) -> None:
        self.status.setText(self.current_pane().status_text())

    def fill_places(self) -> None:
        self.places.clear()
        self.quick.clear()
        fixed, own = split_places(self._quick_places(), read_store().get("places", []))
        for label, target in fixed:
            self.places.addItem(self._place_item(label, target))
        for label, target in own:
            self.quick.addItem(self._place_item(label, target))
        self.places.setFixedHeight(len(fixed) * PLACE_HEIGHT + 4)

    @staticmethod
    def _place_item(label: str, target: str) -> QListWidgetItem:
        item = QListWidgetItem(icon_for_target(target), label)
        item.setToolTip(target)
        item.setSizeHint(QSize(0, PLACE_HEIGHT))  # kompakt wie im Windows-Explorer, unabhängig vom UI-Stil
        return item

    def store_places(self) -> None:
        write_store(places=[{"label": places.item(row).text(), "path": places.item(row).toolTip()}
                            for places in (self.places, self.quick) for row in range(places.count())])

    def add_place(self, path: str) -> None:
        path = os.path.normpath(path)
        if any(os.path.normpath(places.item(row).toolTip()) == path
               for places in (self.places, self.quick) for row in range(places.count())):
            return
        self.quick.addItem(self._place_item(os.path.basename(path) or path, path))
        self.store_places()

    def remove_place(self, row: int) -> None:
        self.quick.takeItem(row)
        self.store_places()

    def show_problem(self, message: str) -> None:
        self.titlebar.title.setText(message[:90])
        QTimer.singleShot(5000, lambda: self.titlebar.title.setText("PopWin Explorer"))

    def mouse_step(self, step: int) -> None:
        (self.current_pane().go_forward if step > 0 else self.current_pane().go_back)()

    def _tab_changed(self, index: int) -> None:
        if index >= 0 and self._page(index):
            self.pages.setCurrentWidget(self._page(index))
            self.update_status()

    def _page(self, index: int) -> TabPage:
        return self.tabbar.tabData(index)

    def current_pane(self) -> Pane:
        return self._page(self.tabbar.currentIndex()).panes[0]

    def new_tab(self, path: str) -> None:
        page = TabPage(self.ctx, path)
        self.pages.addWidget(page)
        index = self.tabbar.addTab(tab_title(path))
        self.tabbar.setTabData(index, page)
        pane = page.panes[0]
        pane.selected.connect(self.preview.show_path)
        pane.undoable.connect(self.remember)
        pane.problem.connect(self.show_problem)
        pane.status_changed.connect(self.update_status)
        pane.pin_place.connect(self.add_place)
        pane.navigated.connect(lambda folder: self.tabbar.setTabText(self.tabbar.currentIndex(), tab_title(folder)))
        self.tabbar.setCurrentIndex(index)

    def close_tab(self, index: int) -> None:
        if self.tabbar.count() == 1:
            self.close()
            return
        page = self._page(index)
        self.tabbar.removeTab(index)
        self.pages.removeWidget(page)
        page.deleteLater()

    def tear_tab(self, index: int, position: QPoint) -> None:
        if self.tabbar.count() < 2:
            return
        path = self._page(index).panes[0].path
        self.close_tab(index)
        window = ExplorerWindow(self.ctx, path)
        TORN_WINDOWS.append(window)  # ohne Verweis räumt Python das Fenster sofort wieder ab
        window.resize(self.size())
        window.move(position - QPoint(TEAR_GRAB_X, TEAR_GRAB_Y))
        window.show()

    def toggle_split(self) -> None:
        self._page(self.tabbar.currentIndex()).toggle_split(self.preview.show_path, self.remember)

    def switch_tab(self, step: int) -> None:
        self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + step) % self.tabbar.count())

    def _delete(self) -> None:
        pane = self.current_pane()
        pane.recycle(pane.selection())

    # --- Rückgängig ---------------------------------------------------------------------
    def remember(self, label: str, action) -> None:
        self.history.append((label, action))
        self.undo_button.setEnabled(True)
        self.undo_button.setToolTip(f"Rückgängig: {label} (Strg + Z)")

    def undo(self) -> None:
        if not self.history:
            return
        label, action = self.history.pop()
        try:
            action()
        except OSError as exc:
            self.titlebar.title.setText(f"Rückgängig nicht möglich: {exc.strerror or exc}")
            QTimer.singleShot(4000, lambda: self.titlebar.title.setText("PopWin Explorer"))
        self.undo_button.setEnabled(bool(self.history))
        self.undo_button.setToolTip(f"Rückgängig: {self.history[-1][0]} (Strg + Z)" if self.history else "Rückgängig (Strg + Z)")


def main() -> int:
    def create(ctx, arguments: list[str]) -> ExplorerWindow:
        return ExplorerWindow(ctx, next((a.strip('"') for a in arguments if os.path.isdir(a.strip('"'))), str(Path.home())))
    return run_app("Explorer", "PopWin Explorer", create)


if __name__ == "__main__":
    sys.exit(main())
