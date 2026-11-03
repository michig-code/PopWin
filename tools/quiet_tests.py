"""Startet ein Python-Skript oder die Tests auf einem eigenen, unsichtbaren Windows-Desktop.

Fenster, Fokus und Mausklicks bleiben dort, der Bildschirm des Nutzers merkt nichts. Der Desktop gehört zur selben Sitzung, deshalb
schaltet tests/test_logic.py zusätzlich alles ab, was über Desktops hinweg wirkt, etwa das Reservieren von Platz am Bildschirmrand.

    python tools/quiet_tests.py LOG -m unittest tests.test_logic
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

DESKTOP = "PopWinQuiet"
GENERIC_ALL = 0x10000000
INFINITE = 0xFFFFFFFF
NO_WINDOW = 0x08000000
HEAP_KB = 256 * 1024  # die Tests bauen hunderte Fenster, ein neuer Desktop bekommt ab Werk viel zu wenig dafür


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


def run(log: str, arguments: list[str]) -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopExW.restype = wintypes.HANDLE
    user32.CreateDesktopExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                        wintypes.ULONG, ctypes.c_void_p)
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    kernel32.CreateProcessW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION))
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    desktop = user32.CreateDesktopExW(DESKTOP, None, None, 0, GENERIC_ALL, None, HEAP_KB, None)
    if not desktop:
        raise OSError(ctypes.get_last_error(), "Eigener Desktop nicht möglich")
    inner = subprocess.list2cmdline([sys.executable, *arguments])
    command = f'cmd /c "set POPWIN_QUIET=1&& set PYTHONIOENCODING=utf-8&& {inner} > "{log}" 2>&1"'
    info = STARTUPINFOW()
    info.cb = ctypes.sizeof(info)
    info.lpDesktop = DESKTOP
    process = PROCESS_INFORMATION()
    if not kernel32.CreateProcessW(None, command, None, None, False, NO_WINDOW, None, str(Path.cwd()), ctypes.byref(info), ctypes.byref(process)):
        raise OSError(ctypes.get_last_error(), "Prozess nicht gestartet")
    kernel32.WaitForSingleObject(process.hProcess, INFINITE)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(process.hProcess)
    kernel32.CloseHandle(process.hThread)
    user32.CloseDesktop(desktop)
    return code.value


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2:]))
