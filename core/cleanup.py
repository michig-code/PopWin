"""Cache leeren: alte temporäre Dateien und der Standby-Speicher von Windows.

Standby-Speicher ist kein verschwendeter RAM: Windows hält dort Dateien bereit und gibt ihn sofort her, wenn ein Programm mehr braucht.
Leeren schafft freien Speicher auf dem Papier, danach lädt Windows manches wieder von der Platte. Deshalb nur auf Knopfdruck.

Als eigener Teil `cleanup` läuft diese Datei mit Administratorrechten, die der Standby-Speicher verlangt.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

MIN_AGE_SECONDS = 24 * 3600  # was jünger ist, gehört vermutlich zu einem laufenden Programm
SE_PRIVILEGE_ENABLED, TOKEN_ADJUST, TOKEN_QUERY = 0x2, 0x20, 0x8
SYSTEM_MEMORY_LIST_INFORMATION, MEMORY_PURGE_STANDBY_LIST = 80, 4


def temp_folders() -> list[Path]:
    folders = {Path(os.environ[name]).resolve() for name in ("TEMP", "TMP") if os.environ.get(name)}
    return sorted(folders)


def old_files(folder: Path, now: float, min_age: int = MIN_AGE_SECONDS):
    """(Pfad, Größe) alter Dateien. Verknüpfte Ordner werden nicht betreten: Sie führen aus dem Temp-Ordner heraus."""
    for root, folders, files in os.walk(folder, followlinks=False):
        folders[:] = [name for name in folders if not os.path.isjunction(os.path.join(root, name))
                      and not os.path.islink(os.path.join(root, name))]
        for name in files:
            path = os.path.join(root, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if now - info.st_mtime >= min_age:
                yield path, info.st_size


def scan(folders: list[Path], now: float | None = None) -> tuple[int, int]:
    moment = time.time() if now is None else now
    count = size = 0
    for folder in folders:
        for _path, length in old_files(folder, moment):
            count += 1
            size += length
    return count, size


def remove_emptied(folder: Path, deleted: list[str]) -> None:
    """Nur Ordner, die durch das Löschen leer wurden. Andere leere Ordner hat vielleicht gerade ein Programm angelegt."""
    touched: set[Path] = set()
    for path in deleted:
        parent = Path(path).parent
        while parent != folder and folder in parent.parents:
            touched.add(parent)
            parent = parent.parent
    for place in sorted(touched, key=lambda item: len(item.parts), reverse=True):
        try:
            os.rmdir(place)  # geht nur bei leeren Ordnern
        except OSError:
            pass


def clean(folders: list[Path], now: float | None = None) -> tuple[int, int, int]:
    """(gelöscht, Bytes, übersprungen). Was gerade benutzt wird, bleibt liegen."""
    moment = time.time() if now is None else now
    removed = size = skipped = 0
    for folder in folders:
        deleted = []
        for path, length in list(old_files(folder, moment)):
            try:
                os.unlink(path)
                deleted.append(path)
                removed += 1
                size += length
            except OSError:
                skipped += 1
        remove_emptied(folder, deleted)
    return removed, size, skipped


class CacheJob(QThread):
    finished_with = Signal(tuple)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work

    def run(self) -> None:
        self.finished_with.emit(self.work())


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]


def purge_standby() -> int:
    """Leert den Standby-Speicher. Liefert 0 bei Erfolg, sonst den Fehlercode von Windows."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_ADJUST | TOKEN_QUERY, ctypes.byref(token)):
        return ctypes.get_last_error()
    privilege = TOKEN_PRIVILEGES(1, LUID(), SE_PRIVILEGE_ENABLED)
    if not advapi.LookupPrivilegeValueW(None, "SeProfileSingleProcessPrivilege", ctypes.byref(privilege.Luid)):
        return ctypes.get_last_error()
    advapi.AdjustTokenPrivileges(token, False, ctypes.byref(privilege), 0, None, None)
    if ctypes.get_last_error():
        return ctypes.get_last_error()
    command = ctypes.c_int(MEMORY_PURGE_STANDBY_LIST)
    return ctypes.windll.ntdll.NtSetSystemInformation(SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(command), ctypes.sizeof(command))


def main() -> int:
    return 0 if purge_standby() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
