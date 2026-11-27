"""Eigener Installer im PopWin-Stil. Installiert ohne Administratorrechte in den Benutzerordner.

Aufrufe: PopWin Setup.exe                      führt durch die Installation
         PopWin Setup.exe --uninstall          entfernt PopWin wieder
         PopWin Setup.exe --silent --test ZIEL  entpackt nur, für den Build-Test
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import winreg
import zipfile
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

VERSION = "4.5.0"  # bei jeder neuen Ausgabe erhöhen, daran erkennt das Setup ein Update
APP_NAME = "PopWin"
EXE_NAME = "PopWin.exe"
UNINSTALLER = "Deinstallieren.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PopWin"
RUN_LINK = "Microsoft/Windows/Start Menu/Programs/Startup/PopWin.lnk"  # früherer Weg, wird beim Update abgelöst
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
SERIALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize"
NO_DELAY = {"StartupDelayInMSec": 0, "WaitForIdleState": 0}
NO_DELAY_MARK = "PopWinSetThis"  # nur was PopWin selbst gesetzt hat, nimmt es beim Deinstallieren wieder weg
LOGON_TASK = "PopWin"
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

CSIDL_PROGRAMS = 0x0002
CSIDL_PERSONAL = 0x0005
CSIDL_STARTUP = 0x0007
CSIDL_DESKTOPDIRECTORY = 0x0010
CSIDL_APPDATA = 0x001A
CSIDL_LOCAL_APPDATA = 0x001C

STYLE = """
QWidget#Root { background: #131722; border: 1px solid rgba(176, 198, 240, 0.16); border-radius: 12px; }
QWidget { color: #F1F5FD; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; outline: none; }
QLabel#Title { font-family: "Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"; font-size: 26px; font-weight: 600; color: #F1F5FD; }
QLabel#HeaderTitle { font-weight: 600; color: #F1F5FD; }
QLabel#VersionTag { color: #8896B3; font-size: 12px; }
QLabel#Muted { color: #8896B3; }
QLabel#Danger { color: #FB7185; }
QLineEdit { background: #1A2030; color: #F1F5FD; border: 1px solid rgba(176, 198, 240, 0.24); border-radius: 8px; padding: 7px 10px; }
QLineEdit:focus { border-color: #4EE2C0; }
QPushButton { background: rgba(255, 255, 255, 0.10); color: #F1F5FD; border: 1px solid rgba(176, 198, 240, 0.14); border-radius: 8px; padding: 8px 20px; }
QPushButton:hover { background: rgba(148, 174, 226, 0.18); }
QPushButton:pressed { background: rgba(148, 174, 226, 0.26); }
QPushButton:disabled { color: rgba(241, 245, 253, 0.36); background: rgba(255, 255, 255, 0.05); }
QPushButton#Primary { background: #4EE2C0; color: #0B1218; border: none; border-radius: 8px; font-weight: 600; }
QPushButton#Primary:hover { background: #6BF0D1; }
QPushButton#Primary:pressed { background: #36CFAF; }
QPushButton#Close { background: transparent; border: none; border-radius: 6px; padding: 4px 10px; font-size: 15px; color: #B8C3D9; }
QPushButton#Close:hover { background: #E11D48; color: #FFFFFF; }
QCheckBox { spacing: 10px; padding: 4px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; background: #1A2030; border: 1px solid rgba(176, 198, 240, 0.24); }
QCheckBox::indicator:hover { border-color: #4EE2C0; }
QCheckBox::indicator:checked { background: #4EE2C0; border-color: #4EE2C0; }
QProgressBar { background: rgba(255, 255, 255, 0.10); border: none; border-radius: 4px; max-height: 8px; }
QProgressBar::chunk { background: #4EE2C0; border-radius: 4px; }
QMessageBox { background: #131722; color: #F1F5FD; }
"""


def _bundle_asset(name: str) -> Path:
    """Sucht Assets erst im PyInstaller-Bundle, beim Entwickeln im Repo-Ordner assets/."""
    direct = BUNDLE / name
    return direct if direct.is_file() else BUNDLE.parent / "assets" / name


def known_folder(csidl: int) -> Path | None:
    """Ermittelt einen Windows-Systemordner über SHGetFolderPathW."""
    try:
        shell32 = ctypes.WinDLL("shell32")
        fn = shell32.SHGetFolderPathW
        fn.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR)
        fn.restype = ctypes.c_long
        buffer = ctypes.create_unicode_buffer(260)
        if fn(None, csidl, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    return None


def local_app_data() -> Path:
    base = (os.environ.get("LOCALAPPDATA") or "").strip()
    if base:
        return Path(base)
    return known_folder(CSIDL_LOCAL_APPDATA) or (Path.home() / "AppData" / "Local")


def default_folder() -> Path:
    return local_app_data() / "Programs" / APP_NAME


def app_data(relative: str = "") -> Path:
    base = (os.environ.get("APPDATA") or "").strip()
    root = Path(base) if base else (known_folder(CSIDL_APPDATA) or (Path.home() / "AppData" / "Roaming"))
    return root / relative if relative else root


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return Path(os.path.abspath(str(path.expanduser())))


def normalize_folder(raw: str) -> Path:
    """Bereinigt Benutzereingaben für Zielordner und löst relative Pfade oder ~ auf."""
    cleaned = raw.strip().strip('"\'').strip()
    return _resolve_path(Path(cleaned) if cleaned else default_folder())


def is_safe_install_folder(folder: Path) -> bool:
    """Schützt Laufwerks-Roots, Windows-Verzeichnisse und Benutzer-Basisordner vor versehentlichem Löschen."""
    resolved = _resolve_path(folder)
    if resolved == resolved.parent:
        return False
    norm_target = os.path.normcase(str(resolved))

    for env_name in ("WINDIR", "SystemRoot"):
        raw_win = (os.environ.get(env_name) or (r"C:\Windows" if env_name == "WINDIR" else "")).strip()
        if raw_win:
            win_norm = os.path.normcase(str(_resolve_path(Path(raw_win)))).rstrip("\\/")
            if norm_target == win_norm or norm_target.startswith(win_norm + os.sep):
                return False

    protected: list[Path] = [
        Path.home(),
        Path.home() / "Desktop",
        Path.home() / "Downloads",
        Path.home() / "Documents",
        local_app_data(),
        local_app_data() / "Programs",
        app_data(),
    ]
    for csidl in (CSIDL_DESKTOPDIRECTORY, CSIDL_PERSONAL, CSIDL_APPDATA, CSIDL_LOCAL_APPDATA):
        special = known_folder(csidl)
        if special is not None:
            protected.append(special)
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "APPDATA", "LOCALAPPDATA"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            protected.append(Path(raw))

    protected_norms = {os.path.normcase(str(_resolve_path(path))) for path in protected}
    return norm_target not in protected_norms


def desktop_link() -> Path:
    desktop = known_folder(CSIDL_DESKTOPDIRECTORY)
    if desktop is None:
        try:
            from win32com.client import Dispatch
            special = Dispatch("WScript.Shell").SpecialFolders("Desktop")
            if special:
                desktop = Path(special)
        except Exception:
            desktop = None
    return (desktop or (Path.home() / "Desktop")) / f"{APP_NAME}.lnk"


def start_menu_link() -> Path:
    programs = known_folder(CSIDL_PROGRAMS) or app_data("Microsoft/Windows/Start Menu/Programs")
    return programs / f"{APP_NAME}.lnk"


def restore_link() -> Path:
    return start_menu_link().with_name("Windows-Taskleiste wiederherstellen.lnk")


def startup_link() -> Path:
    startup = known_folder(CSIDL_STARTUP) or app_data("Microsoft/Windows/Start Menu/Programs/Startup")
    return startup / f"{APP_NAME}.lnk"


def write_shortcut(
    link: Path,
    target: Path,
    arguments: str = "",
    description: str = "PopWin Desktop-Oberfläche",
) -> None:
    from win32com.client import Dispatch
    link.parent.mkdir(parents=True, exist_ok=True)
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(link))
    shortcut.TargetPath = str(target)
    if arguments:
        shortcut.Arguments = arguments
    shortcut.WorkingDirectory = str(target.parent)
    shortcut.IconLocation = f"{target},0"
    shortcut.Description = description
    shortcut.Save()


def autostart_on() -> bool:
    if startup_link().exists() or app_data(RUN_LINK).exists():
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, APP_NAME)[0])
    except OSError:
        return False


def _startup_delay_cleared() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY) as key:
            return all(winreg.QueryValueEx(key, name)[0] == 0 for name in NO_DELAY)
    except OSError:
        return False


def _clear_startup_delay() -> None:
    """Hebt die künstliche Windows-Autostart-Verzögerung nur für dieses Konto auf."""
    if _startup_delay_cleared():
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY) as key:
            for name, value in NO_DELAY.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            winreg.SetValueEx(key, NO_DELAY_MARK, 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def _restore_startup_delay() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            winreg.QueryValueEx(key, NO_DELAY_MARK)
            for name in (*NO_DELAY, NO_DELAY_MARK):
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
    except OSError:
        pass


def _remove_logon_task() -> None:
    """Entfernt ggf. eine geplante PopWin-Anmeldeaufgabe still."""
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", LOGON_TASK, "/F"],
            capture_output=True,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _restore_secondary_taskbars() -> None:
    """PopWin schaltet beim Abmelden „Taskleiste auf allen Bildschirmen“ ab und danach still wieder ein.
    Wird es dazwischen deinstalliert, kommt hier der gemerkte Wert zurück, und die Marken von PopWin gehen mit."""
    advanced = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PopWin", 0, winreg.KEY_ALL_ACCESS) as own:
            try:
                saved = int(winreg.QueryValueEx(own, "SavedMMTaskbarEnabled")[0])
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, advanced) as key:
                    if saved == 2:
                        try:
                            winreg.DeleteValue(key, "MMTaskbarEnabled")
                        except OSError:
                            pass
                    else:
                        winreg.SetValueEx(key, "MMTaskbarEnabled", 0, winreg.REG_DWORD, saved)
            except (OSError, ValueError, TypeError):
                pass
    except OSError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\PopWin")
    except OSError:
        pass


def set_autostart(target: Path | None) -> None:
    """Der Run-Schlüssel läuft vor dem Autostart-Ordner, den Windows nach dem Anmelden lange zurückhält."""
    for link in {startup_link(), app_data(RUN_LINK)}:
        try:
            link.unlink(missing_ok=True)
        except OSError:
            pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if target:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{target}" --autostart')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
    try:  # hat man es im Task-Manager einmal abgeschaltet, stünde dort noch die alte Sperre
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass
    (_clear_startup_delay if target else _restore_startup_delay)()


def version_numbers(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def update_kind(installed: str, new: str) -> str:
    """new, update, same oder older: entscheidet, was das Setup anbietet."""
    if not installed:
        return "new"
    old, fresh = version_numbers(installed), version_numbers(new)
    return "update" if fresh > old else "same" if fresh == old else "older"


def _folder_prefix(folder: Path) -> str:
    return os.path.normcase(os.path.abspath(str(folder))).rstrip("\\/") + os.sep


def _own_processes() -> set[int]:
    """Das Setup selbst und seine Eltern: Als --onefile-Exe läuft es als zwei Prozesse mit derselben Datei.
    Liegt der Deinstallierer im Programmordner, darf er seinen eigenen Starter nicht beenden."""
    import psutil
    pids = {os.getpid()}
    try:
        pids.update(parent.pid for parent in psutil.Process().parents())
    except psutil.Error:
        pass
    return pids


def _matching_processes(folder: Path, attrs: list[str]) -> list:
    import psutil
    prefix = _folder_prefix(folder)
    own = _own_processes()
    matches = []
    try:
        for process in psutil.process_iter(attrs):
            try:
                if process.pid in own:
                    continue
                exe = process.info.get("exe") or ""
                if exe and os.path.normcase(os.path.abspath(exe)).startswith(prefix):
                    matches.append(process)
            except (psutil.Error, OSError):
                continue
    except psutil.Error:
        pass
    return matches


def stop_running(folder: Path) -> bool:
    """Beendet PopWin im Installationsordner und sagt, ob es lief; fremde Programme bleiben unberührt.

    Der Wächter bleibt verschont: Er holt die Windows-Taskleiste zurück und beendet sich dann selbst.
    """
    import psutil
    to_stop = []
    was_running = False
    for process in _matching_processes(folder, ["exe", "cmdline"]):
        try:
            arguments = process.info.get("cmdline") or []
        except psutil.Error:
            arguments = []
        if "watchdog" in arguments:
            continue
        was_running = was_running or "--app" not in arguments
        to_stop.append(process)
        try:
            process.terminate()
        except psutil.Error:
            pass
    if to_stop:
        psutil.wait_procs(to_stop, timeout=8)
    return was_running


def end_leftovers(folder: Path) -> None:
    """Der Wächter ist selbst PopWin.exe und hielte die Dateien fest. Er bekommt kurz Zeit, die Taskleiste zurückzuholen."""
    import psutil
    left = _matching_processes(folder, ["exe"])
    if not left:
        return
    _, alive = psutil.wait_procs(left, timeout=WATCHDOG_WAIT)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)


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
    except (AttributeError, OSError):
        return False


def elevated_arguments(folder: Path, options: dict[str, bool], launch: bool) -> str:
    parts = ["--elevated", f'"{folder}"']
    parts += [f"--{name}" for name in ("desktop", "autostart") if options.get(name)]
    return " ".join(parts + ([] if launch else ["--no-launch"]))


def run_as_admin(arguments: str) -> bool:
    """Windows fragt selbst nach; lehnt man ab, kommt ein Wert bis 32 zurück und das Setup bleibt offen."""
    try:
        return int(ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arguments, None, 1) or 0) > 32
    except (AttributeError, OSError):
        return False


def retire_old_nova(folder: Path, local_data: Path, new_memory: Path) -> None:
    """Das frühere Nova verschwindet ganz. Nur ihre Erinnerungen ziehen vorher um, falls das noch nicht passiert ist."""
    for old_dir in ("Nova", "nova"):
        clear_old(folder / old_dir)
    for stale_pattern in ("popwin.pid", "popwin.log*"):
        for stale in folder.glob(stale_pattern):
            clear_old(stale)
    old = local_data / OLD_NOVA_DATA
    memory = old / "memory.json"
    if memory.is_file() and not new_memory.exists():
        new_memory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(memory, new_memory)
    for leftover in (old, new_memory.parent / "webview", new_memory.parent / "EBWebView"):
        try:
            remove_path(leftover)
        except OSError:
            pass


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


def _read_saved_taskbar_state(folder: Path) -> int | None:
    data_dir = (folder / "data") if (folder / "portable.txt").is_file() else app_data(APP_NAME)
    settings_file = data_dir / "settings.json"
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    shell = data.get("shell")
    if not isinstance(shell, dict):
        return None
    val = shell.get("saved_taskbar_state")
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def _restore_native_taskbar_direct(folder: Path | None = None) -> None:
    """Direkter Win32-Fallback zum Wiederherstellen aller Windows-Taskleisten."""
    try:
        user32 = ctypes.WinDLL("user32")
        shell32 = ctypes.WinDLL("shell32")
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowExW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SendMessageTimeoutW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        )
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        shell32.SHAppBarMessage.argtypes = (wintypes.DWORD, ctypes.c_void_p)
        shell32.SHAppBarMessage.restype = ctypes.c_size_t

        saved = _read_saved_taskbar_state(folder) if folder is not None else None
        state = 2 if saved is None else saved  # ABS_ALWAYSONTOP = 2
        tray = user32.FindWindowW("Shell_TrayWnd", None)
        if tray:
            data = _APPBARDATA()
            data.cbSize = ctypes.sizeof(_APPBARDATA)
            data.hWnd = tray
            data.lParam = state
            shell32.SHAppBarMessage(10, ctypes.byref(data))  # ABM_SETSTATE = 10

        for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = None
            while True:
                hwnd = user32.FindWindowExW(None, hwnd, cls, None)
                if not hwnd:
                    break
                user32.ShowWindow(hwnd, 8)  # SW_SHOWNA = 8

        time.sleep(0.2)
        user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200, None)
    except (AttributeError, OSError):
        pass


def restore_taskbar(folder: Path) -> None:
    """Wurde PopWin hart beendet, holt sein Wächter die Windows-Taskleiste zurück; zur Sicherheit auch hier."""
    exe = folder / EXE_NAME
    if exe.is_file():
        try:
            subprocess.run(
                [str(exe), "--app", "watchdog", "--restore"],
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    _restore_native_taskbar_direct(folder)


def register(folder: Path, size_kb: int) -> None:
    uninstaller = folder / UNINSTALLER
    size_kb = max(1, int(size_kb))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (
            ("DisplayName", APP_NAME),
            ("DisplayVersion", VERSION),
            ("Publisher", APP_NAME),
            ("DisplayIcon", f"{folder / EXE_NAME},0"),
            ("InstallLocation", str(folder)),
            ("InstallDate", time.strftime("%Y%m%d")),
            ("UninstallString", f'"{uninstaller}" --uninstall'),
        ):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def installed_version() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return str(winreg.QueryValueEx(key, "DisplayVersion")[0] or "").strip()
    except OSError:
        return ""


def installed_folder() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            raw = str(winreg.QueryValueEx(key, "InstallLocation")[0] or "").strip()
            return Path(raw) if raw else None
    except OSError:
        return None


def cleanup_command(folder: Path) -> str:
    """cmd-Befehl, der nach dem Ende des Deinstallierers die Programmdateien löscht.

    Gelöscht wird nur, was das Setup selbst hingelegt hat. Den Ordner ganz (samt Inhalt) löscht er nur, wenn er
    PopWin heißt; sonst nur, wenn er danach leer ist. So gehen in einem selbst gewählten Ordner keine fremden
    Dateien verloren.
    """
    base = str(folder).rstrip("\\/")
    steps = [
        f'del /f /q "{base}\\{UNINSTALLER}"',
        f'del /f /q "{base}\\{EXE_NAME}"',
        f'rmdir /s /q "{base}\\_internal"',
        f'(for /d %D in ("{base}\\{ASIDE_PREFIX}*") do rmdir /s /q "%D")',
        f'del /f /q "{base}\\{ASIDE_PREFIX}*"',
        f'rmdir /s /q "{base}"' if folder.name.casefold() == APP_NAME.casefold() else f'rmdir "{base}"',
    ]
    once = " & ".join(f"{step} >nul 2>&1" for step in steps)
    wait = "ping 127.0.0.1 -n 3 >nul"
    return f"{wait} & {once} & {wait} & {once}"


def remove_files_later(folder: Path) -> None:
    """Der Deinstallierer liegt im Ordner und kann sich nicht selbst löschen; das erledigt cmd nach dem Beenden."""
    if not is_safe_install_folder(folder):
        return
    try:
        # Als fertige Zeichenkette: Eine Liste würde die Anführungszeichen mit \" maskieren, das versteht cmd nicht.
        # /s: cmd nimmt nur das äußere Paar Anführungszeichen weg, die inneren um die Pfade bleiben.
        subprocess.Popen(
            f'cmd.exe /d /s /c "{cleanup_command(folder)}"',
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=tempfile.gettempdir(),
            close_fds=True,
        )
    except OSError:
        pass


# Was PopWin selbst in die Registry schreibt (core/win_api.py) und nach dem Deinstallieren ins Leere zeigen würde.
SHELL_INTEGRATION_KEYS = (
    r"Software\Classes\Directory\shell\PopWinExplorer",
    r"Software\Classes\Directory\Background\shell\PopWinExplorer",
    r"Software\Classes\PopWin.Editor",
)
OPEN_WITH_ID = "PopWin.Editor"
OPEN_WITH_TYPES = (".txt", ".md", ".log", ".ini", ".json", ".csv", ".ps1", ".xml")


def _delete_key_tree(root: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_key_tree(f"{root}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
    except OSError:
        pass


def remove_shell_integration() -> None:
    """Rechtsklick „Im PopWin Explorer öffnen“ und „Öffnen mit → PopWin Editor“ wieder austragen."""
    for root in SHELL_INTEGRATION_KEYS:
        _delete_key_tree(root)
    for suffix in OPEN_WITH_TYPES:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{suffix}\OpenWithProgids", 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, OPEN_WITH_ID)
        except OSError:
            pass


class InstallThread(QThread):
    progress = Signal(int)
    status = Signal(str)
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
        except zipfile.BadZipFile:
            self.failed.emit("Das Installationspaket ist beschädigt.", False)
            return
        except OSError as exc:
            self.failed.emit(exc.strerror or str(exc), False)
            return
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__, False)
            return
        self.done.emit()

    def install(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            payload = BUNDLE / "payload.zip"
            if not payload.is_file():
                raise FileNotFoundError(f"Installationspaket fehlt: {payload}")
            if not can_write(self.folder):
                raise PermissionError(f"Kein Schreibzugriff auf {self.folder}")

            test_mode = bool(self.options.get("test"))
            if not test_mode:
                previous = installed_folder()
                if previous and previous.exists():
                    try:
                        same_target = previous.resolve() == self.folder.resolve()
                    except OSError:
                        same_target = os.path.normcase(str(previous)) == os.path.normcase(str(self.folder))
                    if not same_target:
                        self.status.emit("Vorherige Installation wird beendet...")
                        self.was_running = stop_running(previous) or self.was_running
                        end_leftovers(previous)

            if self.folder.exists():
                self.status.emit("Laufende Dateien werden vorbereitet...")
                self.was_running = stop_running(self.folder) or self.was_running
                end_leftovers(self.folder)
                sweep_aside(self.folder)
                for name in ("_internal", EXE_NAME):  # alte Dateien dürfen nicht neben den neuen liegen bleiben
                    clear_old(self.folder / name)
                if not test_mode:
                    retire_old_nova(self.folder, local_app_data(), app_data(APP_NAME) / "nova" / "memory.json")

            self.status.emit("Dateien werden entpackt...")
            self.folder.mkdir(parents=True, exist_ok=True)
            root_resolved = self.folder.resolve()
            with zipfile.ZipFile(payload) as archive:
                members = archive.infolist()
                if EXE_NAME not in {member.filename.replace("\\", "/") for member in members}:
                    raise zipfile.BadZipFile(f"{EXE_NAME} fehlt im Installationspaket")
                total = max(len(members), 1)
                for index, member in enumerate(members):
                    target_path = (root_resolved / member.filename).resolve()
                    if not target_path.is_relative_to(root_resolved):
                        raise zipfile.BadZipFile(f"Unsicherer Archivpfad: {member.filename}")
                    archive.extract(member, root_resolved)
                    self.progress.emit((index + 1) * 95 // total)
                size = sum(member.file_size for member in members) // 1024

            if test_mode:
                self.progress.emit(100)
                return

            self.status.emit("Verknüpfungen werden eingerichtet...")
            uninstaller_path = self.folder / UNINSTALLER
            if Path(sys.executable).resolve() != uninstaller_path.resolve():
                clear_old(uninstaller_path)
                shutil.copy2(sys.executable, uninstaller_path)

            target = self.folder / EXE_NAME
            register(self.folder, size)
            write_shortcut(start_menu_link(), target)
            write_shortcut(restore_link(), target, RESTORE_ARGUMENTS, "Holt die Windows-Taskleiste zurück")
            if self.options.get("desktop"):
                write_shortcut(desktop_link(), target)
            set_autostart(target if self.options.get("autostart") else None)
            self.progress.emit(100)
        finally:
            pythoncom.CoUninitialize()


class UninstallThread(QThread):
    done = Signal()
    failed = Signal(str)

    def __init__(self, folder: Path | None, remove_settings: bool):
        super().__init__()
        self.folder = folder
        self.remove_settings = remove_settings

    def run(self) -> None:
        try:
            self._uninstall()
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit()

    def _uninstall(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            if self.folder is not None:
                stop_running(self.folder)
                restore_taskbar(self.folder)
                end_leftovers(self.folder)
            else:
                _restore_native_taskbar_direct(None)
            set_autostart(None)
            _remove_logon_task()
            _restore_secondary_taskbars()
            remove_shell_integration()
            for link in {app_data(START_LINK), start_menu_link(), app_data(RESTORE_LINK), restore_link(), desktop_link()}:
                try:
                    link.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
            except OSError:
                pass
            if self.remove_settings:
                settings_dir = app_data(APP_NAME)
                if settings_dir.exists():
                    try:
                        remove_path(settings_dir)
                    except OSError:
                        pass
            if self.folder is not None and is_safe_install_folder(self.folder) and self.folder.exists():
                for name in ("_internal", EXE_NAME):
                    clear_old(self.folder / name)
        finally:
            pythoncom.CoUninitialize()


def _resolve_uninstall_target() -> Path | None:
    known = installed_folder()
    if known is not None:
        resolved = normalize_folder(str(known)) if str(known).strip() else None
        if resolved is not None and is_safe_install_folder(resolved):
            return resolved
    exe_dir = Path(sys.executable).resolve().parent
    if ((exe_dir / EXE_NAME).is_file() or (exe_dir / UNINSTALLER).is_file()) and is_safe_install_folder(exe_dir):
        return exe_dir
    return None


def _set_note(label: QLabel, text: str, danger: bool = False) -> None:
    label.setText(text)
    target_name = "Danger" if danger else "Muted"
    if label.objectName() != target_name:
        label.setObjectName(target_name)
        label.style().unpolish(label)
        label.style().polish(label)


class Wizard(QWidget):
    def __init__(self, uninstall: bool, elevated: Path | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("PopWin Setup")
        icon_path = _bundle_asset("popwin.ico")
        if icon_path.is_file():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
        self.setFixedSize(640, 440)
        self._drag: QPoint | None = None
        self.worker = None
        self.automatic = False  # aus PopWins Update-Dialog: ohne Klick aktualisieren
        self.target: Path | None = None
        self._uninstall_done = False
        self._scheduled_cleanup = False
        root = QWidget(self, objectName="Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(28, 14, 16, 22)
        head = QHBoxLayout()
        head.setSpacing(8)
        logo = QLabel()
        logo_path = _bundle_asset("popwin.png")
        logo_pixmap = QPixmap(str(logo_path)) if logo_path.is_file() else QPixmap()
        if not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(
                26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            logo.hide()
        close = QPushButton("✕", objectName="Close")
        close.clicked.connect(self.close)
        head.addWidget(logo)
        head.addWidget(QLabel("PopWin Setup", objectName="HeaderTitle"))
        head.addWidget(QLabel(f"v{VERSION}", objectName="VersionTag"))
        head.addStretch(1)
        head.addWidget(close)
        box.addLayout(head)
        self.pages = QStackedWidget()
        box.addWidget(self.pages, 1)
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
        if elevated and not uninstall:
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
        layout = self.page(
            "Willkommen bei PopWin",
            "PopWin bringt eine moderne Desktop-Oberfläche im Aurora-Design auf Windows: mit Topbar, Taskbar, "
            "Home-Menü, Suche, Widgets, der KI-Assistentin Nova sowie eigenen Apps für Explorer, Editor, "
            "Terminal und Studio.\n\n"
            "Es läuft ohne Administratorrechte und lässt sich jederzeit vollständig entfernen.",
        )
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
            chosen = Path(folder)
            if chosen.name.casefold() != APP_NAME.casefold():
                chosen = chosen / APP_NAME
            self.folder.setText(str(chosen))

    def _install(self) -> None:
        folder = normalize_folder(self.folder.text())
        if not is_safe_install_folder(folder):
            QMessageBox.warning(
                self,
                "PopWin Setup",
                f"Der gewählte Ordner ist geschützt oder nicht geeignet:\n{folder}\n\n"
                "Bitte wähle einen eigenen Unterordner für PopWin (z. B. den Standardordner).",
            )
            if not self.updating or self.pages.currentIndex() == 0:
                self.show_page(self.first + 1)
            return
        self.folder.setText(str(folder))
        self.bar.setValue(0)
        _set_note(self.progress_note, "Vorbereitung...")
        self.steps[self.progress_page] = {}
        self.show_page(self.progress_page)
        options = {"desktop": self.desktop.isChecked(), "autostart": self.autostart.isChecked()}
        self.worker = InstallThread(folder, options)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.status.connect(self.progress_note.setText)
        self.worker.failed.connect(self._failed)
        self.worker.done.connect(self._finish if self.automatic else lambda: self.show_page(self.done_page))
        self.worker.start()

    def auto_update(self) -> None:
        """Aus PopWin gestartet: gleich aktualisieren und danach PopWin wieder öffnen, ohne dass jemand klicken muss.
        Das Fenster ging sonst hinter den Leisten unter und wartete dort still auf „Aktualisieren“."""
        self.automatic = True
        self._update()

    def _failed(self, message: str, needs_admin: bool) -> None:
        _set_note(self.progress_note, f"Das hat nicht geklappt: {message}", danger=True)
        self.steps[self.progress_page] = ({"next": "Als Administrator installieren", "action": self._elevate} if needs_admin
                                          else {"next": "Schließen", "action": self.close})
        self.show_page(self.progress_page)

    def _elevate(self) -> None:
        folder = normalize_folder(self.folder.text())
        options = {"desktop": self.desktop.isChecked(), "autostart": self.autostart.isChecked()}
        if run_as_admin(elevated_arguments(folder, options, self.launch.isChecked())):
            self.close()
        else:
            _set_note(
                self.progress_note,
                "Ohne Administratorrechte geht es in diesen Ordner nicht. Der Standardordner braucht keine.",
                danger=True,
            )

    def _finish(self) -> None:
        if self.launch.isChecked():
            folder = normalize_folder(self.folder.text())
            target = folder / EXE_NAME
            if target.is_file():
                # Über den Explorer: PopWin soll normal laufen, auch wenn das Setup Administrator ist.
                command = ["explorer.exe", str(target)] if is_admin() else [str(target)]
                try:
                    subprocess.Popen(command, cwd=str(folder), close_fds=True)
                except OSError:
                    pass
        self.close()

    def _build_uninstall(self) -> None:
        layout = self.page("PopWin entfernen", "PopWin wird beendet, die Windows-Taskleiste kommt zurück, und alle "
                                               "Programmdateien und Verknüpfungen werden gelöscht.")
        self.remove_settings = QCheckBox("Auch Einstellungen und Editor-Entwürfe löschen")
        layout.addWidget(self.remove_settings)
        layout.addStretch(1)
        layout = self.page("PopWin wird entfernt", "Einen Moment bitte.")
        self.uninstall_bar = QProgressBar()
        self.uninstall_bar.setRange(0, 0)
        self.uninstall_bar.setTextVisible(False)
        layout.addWidget(self.uninstall_bar)
        self.uninstall_note = QLabel("Laufende Prozesse werden beendet...", objectName="Muted")
        self.uninstall_note.setWordWrap(True)
        layout.addWidget(self.uninstall_note)
        layout.addStretch(1)
        layout = self.page("Fertig", "PopWin wurde entfernt.")
        layout.addStretch(1)
        self.steps = [{"next": "Entfernen", "action": self._uninstall}, {}, {"next": "Schließen", "action": self._after_uninstall}]
        self.show_page(0)

    def _uninstall(self) -> None:
        self._uninstall_done = False
        self._scheduled_cleanup = False
        self.target = _resolve_uninstall_target()
        self.uninstall_bar.setRange(0, 0)
        _set_note(self.uninstall_note, "Laufende Prozesse und Verknüpfungen werden entfernt...")
        self.steps[1] = {}
        self.show_page(1)
        self.worker = UninstallThread(self.target, self.remove_settings.isChecked())
        self.worker.failed.connect(self._uninstall_failed)
        self.worker.done.connect(self._on_uninstall_done)
        self.worker.start()

    def _on_uninstall_done(self) -> None:
        self._uninstall_done = True
        self.show_page(2)

    def _uninstall_failed(self, message: str) -> None:
        self.uninstall_bar.setRange(0, 1)
        self.uninstall_bar.setValue(0)
        _set_note(self.uninstall_note, f"Deinstallation fehlgeschlagen: {message}", danger=True)
        self.steps[1] = {"next": "Schließen", "action": self.close}
        self.show_page(1)

    def _schedule_uninstall_cleanup(self) -> None:
        if not self._scheduled_cleanup and self.target is not None:
            self._scheduled_cleanup = True
            remove_files_later(self.target)

    def _after_uninstall(self) -> None:
        self._schedule_uninstall_cleanup()
        self.close()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "PopWin Setup", "Bitte warte kurz, bis der Schritt fertig ist.")
            event.ignore()
            return
        if self._uninstall_done:
            self._schedule_uninstall_cleanup()


def _arg_value(args: list[str], flag: str) -> str | None:
    prefix = f"{flag}="
    for index, item in enumerate(args):
        if item == flag:
            if index + 1 < len(args):
                value = args[index + 1].strip()
                return value or None
            return None
        if item.startswith(prefix):
            value = item[len(prefix):].strip()
            return value or None
    return None


def started_by_popwin() -> bool:
    """Ältere PopWins starten das Setup noch ohne --auto-update. Kommt es aus PopWin.exe, ist es trotzdem ein Update."""
    try:
        import psutil
        parent = psutil.Process().parent()
        while parent is not None and parent.name().lower() in ("popwin setup.exe", "popwin dev setup.exe"):  # der Starter der Ein-Datei-EXE
            parent = parent.parent()
        return parent is not None and parent.name().lower() == EXE_NAME.lower()
    except Exception:
        return False


def main() -> int:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PopWin.Setup")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    if "--silent" in sys.argv:
        raw_target = _arg_value(sys.argv, "--test")
        if not raw_target:
            return 2
        worker = InstallThread(normalize_folder(raw_target), {"test": True})
        worker.done.connect(app.quit)
        worker.failed.connect(lambda _message, _admin: app.exit(1))
        worker.start()
        return app.exec()
    raw_elevated = _arg_value(sys.argv, "--elevated")
    elevated = normalize_folder(raw_elevated) if raw_elevated else None
    wizard = Wizard("--uninstall" in sys.argv, elevated)
    automatic = ((AUTO_UPDATE in sys.argv or started_by_popwin()) and not elevated
                 and "--uninstall" not in sys.argv and getattr(wizard, "updating", False))
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
