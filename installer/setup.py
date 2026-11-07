"""Eigener Installer im PopWin-Stil. Installiert ohne Administratorrechte in den Benutzerordner.

Aufrufe: PopWin Setup.exe                      führt durch die Installation
         PopWin Setup.exe --uninstall          entfernt PopWin wieder
         PopWin Setup.exe --silent --test ZIEL  entpackt nur, für den Build-Test
"""
from __future__ import annotations

import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import winreg
import zipfile
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

VERSION = "3.8.5"  # bei jeder neuen Ausgabe erhöhen, daran erkennt das Setup ein Update
APP_NAME = "PopWin"
EXE_NAME = "PopWin.exe"
UNINSTALLER = "Deinstallieren.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PopWin"
RUN_LINK = "Microsoft/Windows/Start Menu/Programs/Startup/PopWin.lnk"  # früherer Weg, wird beim Update abgelöst
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
START_LINK = "Microsoft/Windows/Start Menu/Programs/PopWin.lnk"
# Der Notausgang, den früher die .bat im Projektordner bot: Endnutzer bekommen PopWin nur über Setup oder Portable.
RESTORE_LINK = "Microsoft/Windows/Start Menu/Programs/Windows-Taskleiste wiederherstellen.lnk"
RESTORE_ARGUMENTS = "--app watchdog --restore"
AUTO_UPDATE = "--auto-update"  # von PopWins Update-Dialog: ohne Rückfrage aktualisieren und PopWin neu starten
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASIDE_PREFIX = ".alt-"
DELETE_TRIES = 6
DELETE_WAIT = 0.5
WATCHDOG_WAIT = 4
OLD_NOVA_DATA = "CopilotPro"
STYLE = """
QWidget#Root { background: #1E2126; border: 1px solid #3A4048; border-radius: 12px; }
QWidget { color: #EDEFF2; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; }
QLabel#Title { font-size: 28px; font-weight: 600; }
QLabel#Muted { color: #A8B0BA; }
QLineEdit { background: #2A2E35; border: 1px solid #434A53; border-radius: 6px; padding: 7px 9px; }
QPushButton { background: #2F343C; border: none; border-radius: 6px; padding: 8px 20px; }
QPushButton:hover { background: #3A4049; }
QPushButton#Primary { background: #3AA6B4; color: #0D1216; font-weight: 600; }
QPushButton#Primary:hover { background: #4FB9C6; }
QPushButton#Close { background: transparent; padding: 4px 10px; font-size: 16px; }
QPushButton#Close:hover { background: #C42B1C; }
QCheckBox { spacing: 10px; padding: 3px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; background: #2A2E35; border: 1px solid #434A53; }
QCheckBox::indicator:checked { background: #3AA6B4; border-color: #3AA6B4; }
QProgressBar { background: #2A2E35; border: none; border-radius: 4px; max-height: 8px; }
QProgressBar::chunk { background: #3AA6B4; border-radius: 4px; }
"""


def default_folder() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_NAME


def app_data(relative: str) -> Path:
    return Path(os.environ["APPDATA"]) / relative


def write_shortcut(link: Path, target: Path, arguments: str = "", description: str = "PopWin Desktop-Oberfläche") -> None:
    from win32com.client import Dispatch
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(link))
    shortcut.TargetPath = str(target)
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = str(target.parent)
    shortcut.IconLocation = str(target)
    shortcut.Description = description
    shortcut.Save()


def autostart_on() -> bool:
    if app_data(RUN_LINK).exists():
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, APP_NAME)[0])
    except OSError:
        return False


def set_autostart(target: Path | None) -> None:
    """Der Run-Schlüssel läuft vor dem Autostart-Ordner, den Windows nach dem Anmelden lange zurückhält."""
    app_data(RUN_LINK).unlink(missing_ok=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if target:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{target}" --autostart')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


def desktop_link() -> Path:
    from win32com.client import Dispatch
    return Path(Dispatch("WScript.Shell").SpecialFolders("Desktop")) / "PopWin.lnk"


def version_numbers(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def update_kind(installed: str, new: str) -> str:
    """new, update, same oder older: entscheidet, was das Setup anbietet."""
    if not installed:
        return "new"
    old, fresh = version_numbers(installed), version_numbers(new)
    return "update" if fresh > old else "same" if fresh == old else "older"


def stop_running(folder: Path) -> bool:
    """Beendet PopWin im Installationsordner und sagt, ob es lief; fremde Programme bleiben unberührt.

    Der Wächter bleibt verschont: Er holt die Windows-Taskleiste zurück und beendet sich dann selbst.
    """
    import psutil
    prefix = str(folder).lower() + os.sep
    me = os.getpid()
    mine = [p for p in psutil.process_iter(["exe", "cmdline"])
            if (p.info["exe"] or "").lower().startswith(prefix) and p.pid != me]
    was_running = False
    for process in mine:
        arguments = process.info["cmdline"] or []
        if "watchdog" in arguments:
            continue
        was_running = was_running or "--app" not in arguments
        try:
            process.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(mine, timeout=8)
    return was_running


def end_leftovers(folder: Path) -> None:
    """Der Wächter ist selbst PopWin.exe und hielte die Dateien fest. Er bekommt kurz Zeit, die Taskleiste zurückzuholen."""
    import psutil
    prefix = str(folder).lower() + os.sep
    me = os.getpid()
    left = [p for p in psutil.process_iter(["exe"]) if (p.info["exe"] or "").lower().startswith(prefix) and p.pid != me]
    psutil.wait_procs(left, timeout=WATCHDOG_WAIT)
    for process in left:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(left, timeout=2)


def _writable(function, path, _error) -> None:
    """Schreibgeschützte Dateien lässt Windows nicht löschen; rmtree ruft das hier und versucht es danach neu."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, onerror=_writable)
    elif path.exists():
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def clear_old(path: Path) -> None:
    """Löscht alte Programmdateien. Hält noch jemand sie fest, wandern sie zur Seite: Umbenennen erlaubt Windows auch dann."""
    for attempt in range(DELETE_TRIES):
        try:
            remove_path(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt < DELETE_TRIES - 1:
                time.sleep(DELETE_WAIT)
        except OSError:
            break
    if path.exists():
        path.rename(path.with_name(f"{ASIDE_PREFIX}{os.getpid()}-{time.time_ns()}-{path.name}"))


def sweep_aside(folder: Path) -> None:
    """Reste früherer Updates, sobald nichts sie mehr festhält."""
    for path in folder.glob(ASIDE_PREFIX + "*"):
        try:
            remove_path(path)
        except OSError:
            pass


def can_write(folder: Path) -> bool:
    """Prüft den Ordner oder, wenn es ihn noch nicht gibt, den nächsten vorhandenen darüber."""
    place = folder
    while not place.exists() and place != place.parent:
        place = place.parent
    try:
        with tempfile.TemporaryFile(dir=place):
            return True
    except OSError:
        return False


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def elevated_arguments(folder: Path, options: dict[str, bool], launch: bool) -> str:
    parts = ["--elevated", f'"{folder}"']
    parts += [f"--{name}" for name in ("desktop", "autostart") if options.get(name)]
    return " ".join(parts + ([] if launch else ["--no-launch"]))


def run_as_admin(arguments: str) -> bool:
    """Windows fragt selbst nach; lehnt man ab, kommt ein Wert bis 32 zurück und das Setup bleibt offen."""
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arguments, None, 1) > 32


def retire_old_nova(folder: Path, local_data: Path, new_memory: Path) -> None:
    """Das frühere Nova verschwindet ganz. Nur ihre Erinnerungen ziehen vorher um, falls das noch nicht passiert ist."""
    clear_old(folder / "Nova")
    old = local_data / OLD_NOVA_DATA
    memory = old / "memory.json"
    if memory.exists() and not new_memory.exists():
        new_memory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(memory, new_memory)
    shutil.rmtree(old, ignore_errors=True)


def restore_taskbar(folder: Path) -> None:
    """Wurde PopWin hart beendet, holt sein Wächter die Windows-Taskleiste zurück; zur Sicherheit auch hier."""
    exe = folder / EXE_NAME
    if exe.exists():
        subprocess.run([str(exe), "--app", "watchdog", "--restore"], timeout=10, check=False)


def register(folder: Path, size_kb: int) -> None:
    uninstaller = folder / UNINSTALLER
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_NAME),
                            ("DisplayIcon", str(folder / EXE_NAME)), ("InstallLocation", str(folder)),
                            ("UninstallString", f'"{uninstaller}" --uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def installed_version() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return ""


def installed_folder() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0])
    except OSError:
        return None


def remove_files_later(folder: Path) -> None:
    """Der Deinstallierer liegt im Ordner und kann sich nicht selbst löschen; das erledigt cmd nach dem Beenden."""
    command = f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{folder}"'
    subprocess.Popen(["cmd", "/c", command], creationflags=subprocess.CREATE_NO_WINDOW, cwd=tempfile.gettempdir())


class InstallThread(QThread):
    progress = Signal(int)
    failed = Signal(str, bool)  # Meldung und ob es nur an den Rechten lag
    done = Signal()

    def __init__(self, folder: Path, options: dict[str, bool]):
        super().__init__()
        self.folder = folder
        self.options = options
        self.was_running = False

    def run(self) -> None:
        try:
            self.install()
        except PermissionError:
            self.failed.emit(f"PopWin darf nicht in {self.folder} schreiben.", not is_admin())
            return
        except OSError as exc:
            self.failed.emit(exc.strerror or str(exc), False)
            return
        self.done.emit()

    def install(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        if self.folder.exists():
            self.was_running = stop_running(self.folder)
            end_leftovers(self.folder)
            sweep_aside(self.folder)
            for name in ("_internal", EXE_NAME):  # alte Dateien dürfen nicht neben den neuen liegen bleiben
                clear_old(self.folder / name)
            if not self.options.get("test"):
                retire_old_nova(self.folder, Path(os.environ["LOCALAPPDATA"]), app_data(APP_NAME) / "nova" / "memory.json")
        self.folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(BUNDLE / "payload.zip") as archive:
            members = archive.infolist()
            for index, member in enumerate(members):
                archive.extract(member, self.folder)
                self.progress.emit((index + 1) * 95 // len(members))
            size = sum(member.file_size for member in members) // 1024
        if self.options.get("test"):
            self.progress.emit(100)
            return
        clear_old(self.folder / UNINSTALLER)
        shutil.copy2(sys.executable, self.folder / UNINSTALLER)
        target = self.folder / EXE_NAME
        register(self.folder, size)
        write_shortcut(app_data(START_LINK), target)
        write_shortcut(app_data(RESTORE_LINK), target, RESTORE_ARGUMENTS, "Holt die Windows-Taskleiste zurück")
        if self.options["desktop"]:
            write_shortcut(desktop_link(), target)
        set_autostart(target if self.options["autostart"] else None)
        self.progress.emit(100)


class UninstallThread(QThread):
    done = Signal()

    def __init__(self, folder: Path, remove_settings: bool):
        super().__init__()
        self.folder = folder
        self.remove_settings = remove_settings

    def run(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        restore = self.folder.exists()
        stop_running(self.folder)
        if restore:
            restore_taskbar(self.folder)
        set_autostart(None)
        for link in (app_data(START_LINK), app_data(RESTORE_LINK), desktop_link()):
            link.unlink(missing_ok=True)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except OSError:
            pass
        if self.remove_settings:
            shutil.rmtree(app_data(APP_NAME), ignore_errors=True)
        self.done.emit()


class Wizard(QWidget):
    def __init__(self, uninstall: bool, elevated: Path | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("PopWin Setup")
        self.setWindowIcon(QIcon(str(BUNDLE / "popwin.ico")))
        self.setFixedSize(640, 440)
        self._drag: QPoint | None = None
        self.worker = None
        root = QWidget(self, objectName="Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(28, 14, 16, 22)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(BUNDLE / "popwin.png")).scaled(
            26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        close = QPushButton("✕", objectName="Close")
        close.clicked.connect(self.close)
        head.addWidget(logo)
        head.addWidget(QLabel("PopWin Setup"))
        head.addStretch(1)
        head.addWidget(close)
        box.addLayout(head)
        self.pages = QStackedWidget()
        box.addWidget(self.pages, 1)
        self.automatic = False
        self.buttons = QHBoxLayout()
        self.back = QPushButton("Zurück")
        self.back.clicked.connect(lambda: self.show_page(self.pages.currentIndex() - 1))
        self.next = QPushButton("Weiter", objectName="Primary")
        self._next_action = None
        self.next.clicked.connect(lambda: self._next_action and self._next_action())
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.back)
        self.buttons.addWidget(self.next)
        self.buttons.setContentsMargins(0, 0, 12, 0)
        box.addLayout(self.buttons)
        if uninstall:
            self._build_uninstall()
        else:
            self._build_install()
        if elevated:
            self.folder.setText(str(elevated))
            self.desktop.setChecked("--desktop" in sys.argv)
            self.autostart.setChecked("--autostart" in sys.argv)
            self.launch.setChecked("--no-launch" not in sys.argv)
            self._install()

    def page(self, title: str, text: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 12, 8)
        layout.setSpacing(10)
        layout.addWidget(QLabel(title, objectName="Title"))
        note = QLabel(text, objectName="Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.pages.addWidget(page)
        return layout

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        step = self.steps[index]
        self.back.setVisible(step.get("back", False))
        self.next.setVisible("next" in step)
        self.next.setText(step.get("next", ""))
        self._next_action = step.get("action")

    def _build_install(self) -> None:
        known = installed_folder()
        kind = update_kind(installed_version() if known else "", VERSION)
        self.updating = kind != "new"
        if self.updating:
            self._build_update_start(kind)
        layout = self.page("Willkommen bei PopWin",
                           "PopWin legt eine Pop!_OS-artige Oberfläche über Windows: Topbar, Taskbar, Home-Menü, Suche, "
                           "Widgets sowie eigene Versionen von Explorer, Editor und Terminal.\n\n"
                           "Es läuft ohne Administratorrechte und lässt sich jederzeit vollständig entfernen.")
        layout.addStretch(1)

        layout = self.page("Wo soll PopWin hin?", "Der Standardordner braucht keine Administratorrechte.")
        row = QHBoxLayout()
        self.folder = QLineEdit(str(known or default_folder()))
        browse = QPushButton("Durchsuchen")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addStretch(1)

        layout = self.page("Wie soll PopWin starten?", "Das lässt sich später in den Einstellungen ändern.")
        self.desktop = QCheckBox("Verknüpfung auf dem Desktop")
        self.desktop.setChecked(True)
        self.autostart = QCheckBox("Mit Windows starten")
        self.launch = QCheckBox("PopWin nach der Installation öffnen")
        self.launch.setChecked(True)
        for box in (self.desktop, self.autostart, self.launch):
            layout.addWidget(box)
        layout.addStretch(1)

        layout = self.page("PopWin wird installiert", "Das dauert nur einen Moment.")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.progress_note = QLabel(objectName="Muted")
        self.progress_note.setWordWrap(True)
        layout.addWidget(self.progress_note)
        layout.addStretch(1)

        layout = self.page("Fertig", "PopWin ist installiert. Du findest es im Startmenü. Entfernen kannst du es in den "
                                     "Windows-Einstellungen unter Apps.")
        layout.addStretch(1)
        steps = [{"next": "Weiter", "action": lambda: self.show_page(self.first + 1)},
                 {"next": "Weiter", "back": True, "action": lambda: self.show_page(self.first + 2)},
                 {"next": "Installieren", "back": True, "action": self._install},
                 {},
                 {"next": "Schließen", "action": self._finish}]
        self.first = 1 if self.updating else 0
        label = {"update": "Aktualisieren", "same": "Neu installieren", "older": "Trotzdem installieren"}
        self.steps = ([{"next": label.get(kind, ""), "action": self._update}] if self.updating else []) + steps
        self.progress_page = self.first + 3
        self.done_page = self.first + 4
        self.show_page(0)

    def _build_update_start(self, kind: str) -> None:
        old = installed_version()
        title, text = {
            "update": ("Update verfügbar", f"Installiert ist Version {old}, dieses Setup bringt Version {VERSION}."),
            "same": ("PopWin ist aktuell", f"Version {old} ist schon installiert. Du kannst sie neu installieren, "
                                           "falls etwas nicht stimmt."),
            "older": ("Neuere Version installiert", f"Installiert ist Version {old}, dieses Setup bringt nur "
                                                    f"Version {VERSION}."),
        }[kind]
        layout = self.page(title, text + "\n\nPopWin wird dafür kurz beendet. Einstellungen, Editor-Entwürfe und "
                                         "Nova bleiben erhalten, und PopWin startet danach von selbst wieder.")
        layout.addStretch(1)

    def _update(self) -> None:
        """Ohne Fragen: Ordner und Verknüpfungen bleiben, wie sie sind."""
        self.desktop.setChecked(desktop_link().exists())
        self.autostart.setChecked(autostart_on())
        self.launch.setChecked(True)
        self._install()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Ordner wählen", self.folder.text())
        if folder:
            self.folder.setText(str(Path(folder) / APP_NAME))

    def _install(self) -> None:
        self.show_page(self.progress_page)
        options = {"desktop": self.desktop.isChecked(), "autostart": self.autostart.isChecked()}
        self.worker = InstallThread(Path(self.folder.text()), options)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.failed.connect(self._failed)
        self.worker.done.connect(self._finish if self.automatic else lambda: self.show_page(self.done_page))
        self.worker.start()

    def auto_update(self) -> None:
        """Aus PopWin gestartet: gleich aktualisieren und danach PopWin wieder öffnen, ohne dass jemand klicken muss.
        Das Fenster ging sonst hinter den Leisten unter und wartete dort still auf „Aktualisieren“."""
        self.automatic = True
        self._update()

    def _failed(self, message: str, needs_admin: bool) -> None:
        self.progress_note.setText(f"Das hat nicht geklappt: {message}")
        self.steps[self.progress_page] = ({"next": "Als Administrator installieren", "action": self._elevate} if needs_admin
                                          else {"next": "Schließen", "action": self.close})
        self.show_page(self.progress_page)

    def _elevate(self) -> None:
        options = {"desktop": self.desktop.isChecked(), "autostart": self.autostart.isChecked()}
        if run_as_admin(elevated_arguments(Path(self.folder.text()), options, self.launch.isChecked())):
            self.close()
        else:
            self.progress_note.setText("Ohne Administratorrechte geht es in diesen Ordner nicht. "
                                       "Der Standardordner braucht keine.")

    def _finish(self) -> None:
        if self.launch.isChecked():
            exe = str(Path(self.folder.text()) / EXE_NAME)
            # Über den Explorer: PopWin soll normal laufen, auch wenn das Setup Administrator ist.
            subprocess.Popen(["explorer.exe", exe] if is_admin() else [exe], cwd=self.folder.text())
        self.close()

    def _build_uninstall(self) -> None:
        layout = self.page("PopWin entfernen", "PopWin wird beendet, die Windows-Taskleiste kommt zurück, und alle "
                                               "Programmdateien und Verknüpfungen werden gelöscht.")
        self.remove_settings = QCheckBox("Auch Einstellungen und Editor-Entwürfe löschen")
        layout.addWidget(self.remove_settings)
        layout.addStretch(1)
        layout = self.page("PopWin wird entfernt", "Einen Moment bitte.")
        layout.addStretch(1)
        layout = self.page("Fertig", "PopWin wurde entfernt.")
        layout.addStretch(1)
        self.steps = [{"next": "Entfernen", "action": self._uninstall}, {}, {"next": "Schließen", "action": self._after_uninstall}]
        self.show_page(0)

    def _uninstall(self) -> None:
        self.show_page(1)
        self.target = installed_folder() or Path(sys.executable).parent
        self.worker = UninstallThread(self.target, self.remove_settings.isChecked())
        self.worker.done.connect(lambda: self.show_page(2))
        self.worker.start()

    def _after_uninstall(self) -> None:
        remove_files_later(self.target)
        self.close()

    def mousePressEvent(self, event) -> None:
        self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "PopWin Setup", "Bitte warte kurz, bis der Schritt fertig ist.")
            event.ignore()


def started_by_popwin() -> bool:
    """Ältere PopWins starten das Setup noch ohne --auto-update. Kommt es aus PopWin.exe, ist es trotzdem ein Update."""
    try:
        import psutil
        parent = psutil.Process().parent()
        while parent is not None and parent.name().lower() == "popwin setup.exe":  # der Starter der Ein-Datei-EXE
            parent = parent.parent()
        return parent is not None and parent.name().lower() == EXE_NAME.lower()
    except Exception:
        return False


def main() -> int:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PopWin.Setup")
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    if "--silent" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--test") + 1])
        worker = InstallThread(target, {"test": True})
        worker.done.connect(app.quit)
        worker.failed.connect(lambda _message, _admin: app.exit(1))
        worker.start()
        return app.exec()
    elevated = Path(sys.argv[sys.argv.index("--elevated") + 1]) if "--elevated" in sys.argv else None
    wizard = Wizard("--uninstall" in sys.argv, elevated)
    automatic = (AUTO_UPDATE in sys.argv or started_by_popwin()) and not elevated and getattr(wizard, "updating", False)
    if automatic:  # vor PopWins Leisten, damit man sieht, dass etwas passiert
        wizard.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    wizard.show()
    if automatic:
        wizard.raise_()
        wizard.activateWindow()
        QTimer.singleShot(400, wizard.auto_update)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
