"""Alle direkten Windows-Aufrufe: Blur, AppBar, Darkmode, Papierkorb, Taskleiste, Fenster, Energie."""
from __future__ import annotations

import ctypes
import functools
import logging
import os
import re
import subprocess
import time
import uuid
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowExW.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetClassLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageW.restype = LRESULT
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageTimeoutW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPVOID, wintypes.UINT, wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t))
user32.SendMessageTimeoutW.restype = LRESULT
user32.SetWindowPos.argtypes = (
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsZoomed.argtypes = (wintypes.HWND,)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.c_void_p)
shell32.SHAppBarMessage.argtypes = (wintypes.DWORD, ctypes.c_void_p)
shell32.SHAppBarMessage.restype = ctypes.c_size_t
dwmapi.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)


# --- Fensterstile, Blur ------------------------------------------------------
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", wintypes.DWORD), ("AnimationId", ctypes.c_int)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t)]


ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND, DWMWCP_ROUND = 1, 2


def _abgr(rgba_hex: str) -> int:
    """#RRGGBBAA -> 0xAABBGGRR, wie es die Accent-API erwartet."""
    value = rgba_hex.lstrip("#").ljust(8, "0")
    r, g, b, a = (int(value[i:i + 2], 16) for i in (0, 2, 4, 6))
    return (a << 24) | (b << 16) | (g << 8) | r


def set_blur(hwnd: int, mode: str, tint: str = "#00000000") -> None:
    state = {"acrylic": ACCENT_ENABLE_ACRYLICBLURBEHIND, "blur": ACCENT_ENABLE_BLURBEHIND}.get(mode, ACCENT_DISABLED)
    accent = ACCENT_POLICY(state, 2 if state else 0, _abgr(tint), 0)
    data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.pointer(accent), ctypes.sizeof(accent))
    fn = user32.SetWindowCompositionAttribute
    fn.argtypes = (wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA))
    fn(hwnd, ctypes.byref(data))


def set_rounded(hwnd: int, rounded: bool) -> None:
    pref = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref))


HWND_TOP, HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 0, 1, -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_HIDEWINDOW = 0x1, 0x2, 0x4, 0x10, 0x80


def place_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Setzt echte Pixel - unabhängig von Qts Skalierung pro Monitor."""
    user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def set_zorder(hwnd: int, after: int) -> None:
    user32.SetWindowPos(hwnd, wintypes.HWND(after), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def bring_to_front(hwnd: int) -> None:
    """Für einen Moment ganz nach vorn und dann normal. Windows verweigert Hintergrund-Programmen sonst oft den Vordergrund."""
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    force_foreground(hwnd)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def force_foreground(hwnd: int) -> None:
    """Holt ein eigenes Fenster nach vorn, auch wenn gerade ein anderes Programm aktiv ist."""
    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return
    other = user32.GetWindowThreadProcessId(foreground, None)
    this = kernel32.GetCurrentThreadId()
    attached = bool(other) and other != this and user32.AttachThreadInput(this, other, True)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(this, other, False)


# --- Monitore ----------------------------------------------------------------
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def rank_monitors(devices: dict[tuple, str]) -> dict[tuple, int]:
    """Nummern ohne Lücken wie in den Windows-Einstellungen: DISPLAY1, 2, 3, 5 werden zu 1, 2, 3, 4."""
    def device_number(rect: tuple) -> int:
        digits = re.search(r"(\d+)$", devices[rect])
        return int(digits.group(1)) if digits else 0
    return {rect: rank + 1 for rank, rect in enumerate(sorted(devices, key=device_number))}


def monitor_numbers() -> dict[tuple[int, int, int, int], int]:
    """Windows-Nummer jedes Monitors, nach seinem Rechteck in echten Pixeln."""
    devices: dict[tuple, str] = {}

    def callback(handle, _hdc, rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = rect.contents
            devices[(r.left, r.top, r.right, r.bottom)] = info.szDevice
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return rank_monitors(devices)


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """Alle Monitore in echten Pixeln als (links, oben, rechts, unten)."""
    rects: list[tuple[int, int, int, int]] = []

    def callback(_hmon, _hdc, rect, _lparam):
        r = rect.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return rects


def work_areas() -> list[tuple[int, int, int, int]]:
    """Wie monitor_rects, aber ohne Taskleisten."""
    areas: list[tuple[int, int, int, int]] = []

    def callback(handle, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcWork
            areas.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return areas


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


user32.GetWindowPlacement.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
user32.MonitorFromRect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
user32.MonitorFromRect.restype = wintypes.HMONITOR
MONITOR_DEFAULTTONEAREST = 2


def _monitor_handle(hwnd: int):
    # Minimierte Fenster parkt Windows bei -32000; maßgeblich ist dann ihre Position vor dem Minimieren.
    if user32.IsIconic(hwnd):
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(placement)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            return user32.MonitorFromRect(ctypes.byref(placement.rcNormalPosition), MONITOR_DEFAULTTONEAREST)
    return user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)


user32.SetWindowPlacement.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))


def fix_normal_position(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    """Windows rechnet die Normalposition in Arbeitsflächen-Koordinaten, also ohne Leisten am Rand.
    Qt trägt beim Maximieren rahmenloser Fenster Bildschirmkoordinaten ein; jedes Wiederherstellen rutschte dann um
    die Höhe der Topbar nach unten. Hier wird die gemerkte Position richtig umgerechnet eingetragen."""
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(placement)
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(placement)) or not user32.GetMonitorInfoW(
            user32.MonitorFromRect(ctypes.byref(wintypes.RECT(*rect)), MONITOR_DEFAULTTONEAREST), ctypes.byref(info)):
        return False
    dx, dy = info.rcWork.left - info.rcMonitor.left, info.rcWork.top - info.rcMonitor.top
    wanted = (rect[0] - dx, rect[1] - dy, rect[2] - dx, rect[3] - dy)
    normal = placement.rcNormalPosition
    if (normal.left, normal.top, normal.right, normal.bottom) == wanted:
        return False
    placement.rcNormalPosition = wintypes.RECT(*wanted)
    return bool(user32.SetWindowPlacement(hwnd, ctypes.byref(placement)))


def window_monitor(hwnd: int) -> tuple[int, int, int, int] | None:
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if user32.GetMonitorInfoW(_monitor_handle(hwnd), ctypes.byref(info)):
        r = info.rcMonitor
        return r.left, r.top, r.right, r.bottom
    return None


# --- AppBar: reserviert Platz am Bildschirmrand, ohne das Hintergrundbild anzufassen -----
class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS, ABM_GETSTATE = 0, 1, 2, 3, 4
ABM_SETSTATE = 10
ABN_POSCHANGED = 1
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
APPBAR_EDGES = {"left": ABE_LEFT, "top": ABE_TOP, "right": ABE_RIGHT, "bottom": ABE_BOTTOM}
ABS_AUTOHIDE, ABS_ALWAYSONTOP = 1, 2


def _appbar_data(hwnd: int = 0, callback: int = 0, edge: int = 0, rect=None, lparam: int = 0) -> APPBARDATA:
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    data.hWnd = hwnd
    data.uCallbackMessage = callback
    data.uEdge = edge
    data.lParam = lparam
    if rect:
        data.rc = wintypes.RECT(*rect)
    return data


def appbar_register(hwnd: int, callback_message: int) -> None:
    shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(_appbar_data(hwnd, callback_message)))


def appbar_remove(hwnd: int) -> None:
    shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(_appbar_data(hwnd)))


def appbar_set_pos(hwnd: int, edge: str, monitor: tuple[int, int, int, int], thickness: int,
                   current: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int]:
    """Handelt mit Windows die Position aus; der Arbeitsbereich schrumpft um `thickness`.

    ABM_SETPOS benachrichtigt alle anderen Leisten. Ist das Ergebnis schon `current`, entfällt
    der Aufruf - sonst würden sich zwei Leisten endlos gegenseitig neu auslösen.
    """
    code = APPBAR_EDGES[edge]
    data = _appbar_data(hwnd, edge=code, rect=monitor)
    shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(data))
    if code == ABE_TOP:
        data.rc.bottom = data.rc.top + thickness
    elif code == ABE_BOTTOM:
        data.rc.top = data.rc.bottom - thickness
    elif code == ABE_LEFT:
        data.rc.right = data.rc.left + thickness
    else:
        data.rc.left = data.rc.right - thickness
    wanted = (data.rc.left, data.rc.top, data.rc.right, data.rc.bottom)
    if wanted == current:
        return wanted
    shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(data))
    r = data.rc
    return r.left, r.top, r.right, r.bottom


# --- Native Taskleiste -------------------------------------------------------
SW_SHOWNA, SW_RESTORE = 8, 9


def _native_taskbars() -> list[int]:
    bars: list[int] = []
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        hwnd = None
        while True:
            hwnd = user32.FindWindowExW(None, hwnd, cls, None)
            if not hwnd:
                break
            bars.append(hwnd)
    return bars


def taskbar_state() -> int:
    return int(shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(_appbar_data())))


def _set_taskbar_state(state: int) -> None:
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    if tray:
        shell32.SHAppBarMessage(ABM_SETSTATE, ctypes.byref(_appbar_data(tray, lparam=state)))


def hide_native_taskbar() -> None:
    _set_taskbar_state(ABS_AUTOHIDE)
    reinforce_taskbar_hidden()


def reinforce_taskbar_hidden() -> None:
    """Der Explorer blendet Zweitleisten nach dem Wechsel auf Auto-Hide noch einmal ein -
    deshalb muss nachgefasst werden, ohne den Zustand erneut zu setzen."""
    for hwnd in _native_taskbars():
        if user32.IsWindowVisible(hwnd):
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def native_taskbar_visible() -> bool:
    return any(user32.IsWindowVisible(h) for h in _native_taskbars())


def show_native_taskbar(saved_state: int | None = None) -> None:
    _set_taskbar_state(ABS_ALWAYSONTOP if saved_state is None else saved_state)
    for hwnd in _native_taskbars():
        user32.ShowWindow(hwnd, SW_SHOWNA)
    # Versteckte Zweitleisten kommen sonst im hellen Design zurück, auch wenn Dark Mode an ist.
    time.sleep(0.2)
    broadcast_setting_change("ImmersiveColorSet")


# --- Hell/Dunkel -------------------------------------------------------------
_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WM_SETTINGCHANGE, HWND_BROADCAST, SMTO_ABORTIFHUNG = 0x001A, 0xFFFF, 0x0002


def system_mode() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE) as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "dark"


def set_system_mode(mode: str) -> None:
    value = 1 if mode == "light" else 0
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
    broadcast_setting_change("ImmersiveColorSet")


def broadcast_setting_change(area: str) -> None:
    text = ctypes.create_unicode_buffer(area)
    user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, ctypes.addressof(text),
                               SMTO_ABORTIFHUNG, 200, None)


# --- Papierkorb --------------------------------------------------------------
class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


FO_DELETE = 3
FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_WANTNUKEWARNING = 0x0010, 0x0040, 0x4000
shell32.SHFileOperationW.argtypes = (ctypes.POINTER(SHFILEOPSTRUCTW),)
shell32.SHEmptyRecycleBinW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD)
shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
shell32.SHQueryRecycleBinW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO))
shell32.SHQueryRecycleBinW.restype = ctypes.c_long


def move_to_recycle_bin(paths: list[str]) -> bool:
    """Verschiebt in den Papierkorb. Passt etwas nicht hinein (zu groß, Netzlaufwerk),
    fragt Windows nach, statt endgültig zu löschen (FOF_WANTNUKEWARNING)."""
    absolute = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not absolute:
        return False
    buffer = ctypes.create_unicode_buffer("\0".join(absolute) + "\0\0")
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buffer, wintypes.LPCWSTR)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def restore_from_recycle_bin(paths: list[str]) -> int:
    """Holt Dateien aus dem Papierkorb zurück und gibt die Anzahl zurück."""
    from win32com.client import Dispatch
    wanted = {os.path.normcase(p) for p in paths}
    stems = {os.path.normcase(os.path.splitext(p)[0]) for p in paths}  # Windows blendet Endungen oft aus
    recycle_bin = Dispatch("Shell.Application").Namespace(10)
    restored = 0
    for item in list(recycle_bin.Items()):
        original = os.path.normcase(os.path.join(recycle_bin.GetDetailsOf(item, 1), item.Name))
        if original in wanted or original in stems:
            item.InvokeVerb("undelete")
            restored += 1
    return restored


def recycle_bin_info() -> tuple[int, int]:
    """(Anzahl Elemente, Größe in Bytes)"""
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64NumItems), int(info.i64Size)


def empty_recycle_bin(hwnd: int = 0) -> bool:
    """Flags 0: Windows zeigt seine eigene Sicherheitsabfrage."""
    return shell32.SHEmptyRecycleBinW(hwnd or None, None, 0) == 0


# --- Fenster -----------------------------------------------------------------
GWL_STYLE, GWL_EXSTYLE, GWLP_HWNDPARENT = -16, -20, -8
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x40000, 0x08000000
GW_OWNER = 4
DWMWA_CLOAKED = 14
WM_CLOSE, WM_COMMAND, WM_GETICON = 0x0010, 0x0111, 0x007F
GCLP_HICON, GCLP_HICONSM = -14, -34
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    pid: int
    app_pid: int = 0  # das Hauptprogramm, wenn das Fenster zu einem Helfer-Prozess gehört
    popwin_app: str = ""  # "explorer", "settings" usw.; sonst wäre es nur irgendein Python-Fenster
    app_id: str = ""  # bei Web-Apps wie Crunchyroll: Sie laufen in msedge.exe, sind aber eigene Apps
    aumid: str = ""  # die App-Kennung, die das Fenster selbst angibt; nach ihr wählt Windows das Icon

    @property
    def main_pid(self) -> int:
        return self.app_pid or self.pid

    @property
    def from_helper(self) -> bool:
        return self.main_pid != self.pid


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def exe_of(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(handle)


kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))


def _is_cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return value.value != 0


def _real_exe(hwnd: int, pid: int) -> str:
    """Store-Apps laufen im ApplicationFrameHost; die echte App steckt im Kindfenster."""
    exe = exe_of(pid)
    if not exe.lower().endswith("applicationframehost.exe"):
        return exe
    found: list[str] = []

    def callback(child, _lparam):
        child_pid = _pid(child)
        if child_pid != pid:
            found.append(exe_of(child_pid))
            return False
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    return found[0] if found and found[0] else exe


_main_processes: dict[int, tuple[str, int]] = {}


def _main_process(exe: str, pid: int) -> tuple[str, int]:
    """Das eigentliche Programm. Steam zeigt seine Fenster über steamwebhelper.exe, ein Kind von steam.exe."""
    if "helper" not in os.path.basename(exe).lower():
        return exe, pid
    if pid not in _main_processes:
        import psutil
        found = (exe, pid)
        for _ in range(4):
            try:
                parent = psutil.Process(found[1]).ppid()
            except psutil.Error:
                break
            parent_exe = exe_of(parent)
            # Nur innerhalb des eigenen Programmordners, sonst wäre am Ende der Explorer das "Programm".
            if not parent_exe or not exe.lower().startswith(os.path.dirname(parent_exe).lower() + "\\"):
                break
            found = (parent_exe, parent)
            if "helper" not in os.path.basename(parent_exe).lower():
                break
        _main_processes[pid] = found
    return _main_processes[pid]


_popwin_apps: dict[int, str] = {}
INTERNAL_APPS = ("settings", "widgets")  # Fenster von PopWin selbst: "Task beenden" träfe ganz PopWin
_POPWIN_ROOT = str(Path(__file__).resolve().parent.parent).lower()


def popwin_app_of(arguments: list[str]) -> str:
    """Welche PopWin-App ein Prozess ist, erkennbar am gestarteten Skript oder, installiert, an --app."""
    if arguments[:1] == ["--app"] and len(arguments) > 1:
        return arguments[1]
    for argument in arguments:
        path = argument.lower().replace("/", "\\")
        for app in ("explorer", "editor", "terminal", "studio", "stream"):
            if path == f"{_POPWIN_ROOT}\\{app}\\app.py":
                return app
    return ""


def _popwin_app(exe: str, pid: int) -> str:
    if not os.path.basename(exe).lower().startswith(("python", "popwin")):
        return ""
    if pid not in _popwin_apps:
        import psutil
        try:
            _popwin_apps[pid] = popwin_app_of(psutil.Process(pid).cmdline()[1:])
        except psutil.Error:
            return ""
    return _popwin_apps[pid]


def _is_task_window(hwnd: int) -> bool:
    """Dieselben Regeln wie die Windows-Taskleiste. Fenster ohne Titel zeigt sie auch, etwa manche Spiele,
    solange sie wirklich eine Fläche haben."""
    if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
        return False
    if user32.GetWindowTextLengthW(hwnd) == 0:
        left, top, right, bottom = window_rect(hwnd)
        if right - left < 2 or bottom - top < 2:
            return False
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if ex & WS_EX_APPWINDOW:
        return True
    if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
        return False
    return not user32.GetWindow(hwnd, GW_OWNER)


BROWSERS = ("msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe")


def is_web_app_id(app_id: str) -> bool:
    """Store-Web-Apps tragen "Paket!App", installierte Seiten "_crx_". Der Browser selbst heißt schlicht MSEdge oder Chrome."""
    return "!" in app_id or "_crx_" in app_id.lower()


def window_app_id(hwnd: int) -> str:
    """Die AppUserModelID, die ein Fenster selbst setzt; Store-Apps und viele Programme tun das."""
    try:
        from win32com.propsys import propsys, pscon
        return propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore).GetValue(pscon.PKEY_AppUserModel_ID).GetValue() or ""
    except Exception:  # das Fenster kann gerade verschwunden sein
        return ""


def web_app_id(exe: str, aumid: str) -> str:
    if os.path.basename(exe).lower() not in BROWSERS:
        return ""
    return aumid if aumid and is_web_app_id(aumid) else ""


# Programme, die fremden Code ausführen: Ihr eigenes Icon sagt nichts über das Fenster, etwa Minecraft in javaw.exe.
HOST_EXES = ("java.exe", "javaw.exe", "python.exe", "pythonw.exe", "py.exe", "pyw.exe", "rundll32.exe", "dllhost.exe", "mmc.exe",
             "wscript.exe", "cscript.exe", "node.exe", "applicationframehost.exe", "autohotkey.exe", "autohotkey64.exe")
SHGFI_USEFILEATTRIBUTES, FILE_ATTRIBUTE_NORMAL = 0x10, 0x80
_generic_icons: dict[str, bool] = {}


def _system_icon_index(path: str, attributes_only: bool = False) -> int:
    info = SHFILEINFOW()
    flags = SHGFI_SYSICONINDEX | (SHGFI_USEFILEATTRIBUTES if attributes_only else 0)
    shell32.SHGetFileInfoW(ctypes.create_unicode_buffer(path), FILE_ATTRIBUTE_NORMAL if attributes_only else 0,
                           ctypes.byref(info), ctypes.sizeof(info), flags)
    return info.iIcon


def has_generic_icon(exe: str) -> bool:
    """Hat die .exe nur das leere Standard-Programmsymbol von Windows? Dann ist das Fenster-Icon besser."""
    key = exe.lower()
    if key not in _generic_icons:
        _generic_icons[key] = _system_icon_index(exe) == _system_icon_index("x.exe", attributes_only=True)
    return _generic_icons[key]


def is_host_exe(exe: str) -> bool:
    return os.path.basename(exe).lower() in HOST_EXES


def list_windows() -> list[WindowInfo]:
    """Die Fenster, die auch die normale Taskleiste zeigen würde."""
    hwnds: list[int] = []

    def callback(hwnd, _lparam):
        if _is_task_window(hwnd):
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    own = os.getpid()
    result = []
    for hwnd in hwnds:
        pid = _pid(hwnd)
        if pid != own:
            exe, app_pid = _main_process(_real_exe(hwnd, pid), pid)
            aumid = window_app_id(hwnd)
            result.append(WindowInfo(hwnd, window_title(hwnd), exe, pid, app_pid, _popwin_app(exe, pid), web_app_id(exe, aumid), aumid))
    return result


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def covers_monitor(window: tuple[int, int, int, int], monitor: tuple[int, int, int, int]) -> bool:
    return (window[0] <= monitor[0] and window[1] <= monitor[1]
            and window[2] >= monitor[2] and window[3] >= monitor[3])


def contains_center(window: tuple[int, int, int, int], monitor: tuple[int, int, int, int]) -> bool:
    x, y = (window[0] + window[2]) // 2, (window[1] + window[3]) // 2
    return monitor[0] <= x < monitor[2] and monitor[1] <= y < monitor[3]


def foreground_on(monitor: tuple[int, int, int, int]) -> bool:
    """Liegt das aktive Fenster auf diesem Monitor?"""
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd) and contains_center(window_rect(hwnd), monitor)


def is_fullscreen_rect(window: tuple[int, int, int, int], monitor: tuple[int, int, int, int], zoomed: bool) -> bool:
    """Maximierte Fenster ragen ein paar Pixel über den Rand, echtes Vollbild passt genau.
    Discord und andere Electron-Apps melden auch im Vollbild "maximiert", daher zählt die genaue Größe."""
    return covers_monitor(window, monitor) and (not zoomed or window == monitor)


def overlaps(window: tuple[int, int, int, int], monitor: tuple[int, int, int, int]) -> bool:
    return window[0] < monitor[2] and window[2] > monitor[0] and window[1] < monitor[3] and window[3] > monitor[1]


def _counts_for_fullscreen(hwnd: int) -> bool:
    return bool(hwnd) and _pid(hwnd) != os.getpid() and class_name(hwnd) not in (
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def _fills(hwnd: int, monitor: tuple[int, int, int, int]) -> bool:
    return is_fullscreen_rect(window_rect(hwnd), monitor, bool(user32.IsZoomed(hwnd)))


def fullscreen_on(monitor: tuple[int, int, int, int]) -> bool:
    """Liegt auf diesem Monitor ein Vollbild-Fenster (Spiel, Video, F11 im Browser) ganz oben?

    Nicht nur das aktive Fenster zählt: Mit Discord im Vollbild auf Monitor 2 kann man auf Monitor 1 arbeiten.
    """
    hwnd = user32.GetForegroundWindow()
    if _counts_for_fullscreen(hwnd) and _fills(hwnd, monitor):
        return True
    found = [False]

    def callback(candidate, _lparam):  # EnumWindows liefert von oben nach unten
        if not _counts_for_fullscreen(candidate) or not _is_task_window(candidate):
            return True
        if not overlaps(window_rect(candidate), monitor):
            return True
        found[0] = _fills(candidate, monitor)
        return False

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0]


def is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def terminate_process(pid: int) -> bool:
    """Wie „Task beenden“ im Task-Manager: beendet sofort, ungespeicherte Daten gehen verloren."""
    import psutil
    if pid == os.getpid():  # die Einstellungen laufen in PopWin selbst
        return False
    try:
        psutil.Process(pid).kill()
        return True
    except psutil.Error as exc:
        log.warning("Prozess %s nicht beendbar: %s", pid, exc)
        return False


def arrange_side_by_side(hwnds: list[int], area: tuple[int, int, int, int]) -> None:
    """Verteilt Fenster gleichmäßig nebeneinander im Arbeitsbereich (links, oben, rechts, unten)."""
    left, top, right, bottom = area
    width = (right - left) // max(1, len(hwnds))
    for index, hwnd in enumerate(hwnds):
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetWindowPos(hwnd, None, left + index * width, top, width, bottom - top, SWP_NOZORDER | SWP_NOACTIVATE)


# --- Live-Vorschau: DWM spiegelt das Fenster, auch minimierte zeigen ihr letztes Bild -----------
class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wintypes.DWORD), ("rcDestination", wintypes.RECT), ("rcSource", wintypes.RECT),
                ("opacity", ctypes.c_ubyte), ("fVisible", wintypes.BOOL), ("fSourceClientAreaOnly", wintypes.BOOL)]


DWM_TNP_RECTDESTINATION, DWM_TNP_OPACITY, DWM_TNP_VISIBLE = 0x1, 0x4, 0x8
dwmapi.DwmRegisterThumbnail.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))
dwmapi.DwmUnregisterThumbnail.argtypes = (ctypes.c_void_p,)
dwmapi.DwmUpdateThumbnailProperties.argtypes = (ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES))
dwmapi.DwmQueryThumbnailSourceSize.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.SIZE))


def thumbnail_register(destination: int, source: int) -> int:
    handle = ctypes.c_void_p()
    if dwmapi.DwmRegisterThumbnail(destination, source, ctypes.byref(handle)) != 0:
        return 0
    return handle.value or 0


def thumbnail_show(handle: int, rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """Zeigt die Vorschau seitenrichtig im Rechteck und gibt die belegte Größe zurück."""
    size = wintypes.SIZE()
    dwmapi.DwmQueryThumbnailSourceSize(handle, ctypes.byref(size))
    left, top, right, bottom = rect
    scale = min((right - left) / max(1, size.cx), (bottom - top) / max(1, size.cy), 1.0)
    width, height = round(size.cx * scale), round(size.cy * scale)
    x = left + (right - left - width) // 2
    props = DWM_THUMBNAIL_PROPERTIES()
    props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY
    props.rcDestination = wintypes.RECT(x, top, x + width, top + height)
    props.opacity = 255
    props.fVisible = True
    dwmapi.DwmUpdateThumbnailProperties(handle, ctypes.byref(props))
    return width, height


def thumbnail_unregister(handle: int) -> None:
    dwmapi.DwmUnregisterThumbnail(handle)


def foreground_title() -> str:
    import pygetwindow
    return pygetwindow.getActiveWindowTitle() or ""


def activate_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    force_foreground(hwnd)


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def minimize_window(hwnd: int) -> None:
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def window_hicon(hwnd: int) -> int:
    result = ctypes.c_size_t(0)
    for icon_type in (1, 0):  # ICON_BIG, ICON_SMALL
        if user32.SendMessageTimeoutW(hwnd, WM_GETICON, icon_type, 0, SMTO_ABORTIFHUNG, 50,
                                      ctypes.byref(result)) and result.value:
            return result.value
    return user32.GetClassLongPtrW(hwnd, GCLP_HICON) or user32.GetClassLongPtrW(hwnd, GCLP_HICONSM)


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


SHGFI_ICON, SHGFI_PIDL, SHGFI_SYSICONINDEX, SHGFI_DISPLAYNAME = 0x100, 0x8, 0x4000, 0x200
SHIL_EXTRALARGE, ILD_TRANSPARENT = 2, 1
IID_IMAGE_LIST = uuid.UUID("46EB5926-582E-4017-9FDF-E8998DAA0950").bytes_le
shell32.SHGetImageList.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))
shell32.SHGetImageList.restype = ctypes.c_long
shell32.SHParseDisplayName.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
shell32.SHGetFileInfoW.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW),
                                   wintypes.UINT, wintypes.UINT)
shell32.SHGetFileInfoW.restype = ctypes.c_size_t
ole32 = ctypes.WinDLL("ole32")
ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
user32.DestroyIcon.argtypes = (wintypes.HICON,)


def shell_item_hicon(target: str) -> int:
    """Icon einer Store-App (shell:AppsFolder\\...); der Aufrufer gibt es mit destroy_icon frei."""
    pidl = ctypes.c_void_p()
    if shell32.SHParseDisplayName(target, None, ctypes.byref(pidl), 0, None) != 0 or not pidl:
        return 0
    info = SHFILEINFOW()
    ok = shell32.SHGetFileInfoW(pidl, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_ICON | SHGFI_PIDL)
    ole32.CoTaskMemFree(pidl)
    return (info.hIcon or 0) if ok else 0


def shell_name(path: str) -> str:
    """Der Name, den Windows zeigt, zum Beispiel „Google Drive (G:)“."""
    info = SHFILEINFOW()
    name = ctypes.create_unicode_buffer(os.path.normpath(path))
    shell32.SHGetFileInfoW(name, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_DISPLAYNAME)
    return info.szDisplayName or path


def file_hicon(path: str) -> int:
    """48-px-Icon einer Datei ohne die Zusatzsymbole von Google Drive, OneDrive und Verknüpfungen.

    Die liegen in der Systemliste getrennt vom Icon; QFileIconProvider rechnet sie immer mit ein.
    Der Aufrufer gibt das Icon mit destroy_icon frei.
    """
    info = SHFILEINFOW()
    name = ctypes.create_unicode_buffer(os.path.normpath(path))  # Laufwerke kommen von Qt als "C:/"
    if not shell32.SHGetFileInfoW(name, 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_SYSICONINDEX):
        return 0
    images = ctypes.c_void_p()
    if shell32.SHGetImageList(SHIL_EXTRALARGE, IID_IMAGE_LIST, ctypes.byref(images)) != 0 or not images:
        return 0
    methods = ctypes.cast(images, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    get_icon = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, wintypes.UINT,
                                  ctypes.POINTER(wintypes.HICON))(methods[10])
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(methods[2])
    handle = wintypes.HICON()
    get_icon(images, info.iIcon, ILD_TRANSPARENT, ctypes.byref(handle))
    release(images)
    return handle.value or 0


def destroy_icon(handle: int) -> None:
    user32.DestroyIcon(handle)


# --- Desktop (Befehls-IDs stammen aus dem Menü-Resource von shell32.dll) ---------
DESKTOP_ICON_LARGE, DESKTOP_ICON_MEDIUM, DESKTOP_ICON_SMALL = 0x704F, 0x704E, 0x7050
DESKTOP_REFRESH = 0x7103
DESKTOP_TOGGLE_ICONS, DESKTOP_AUTO_ARRANGE, DESKTOP_SNAP_TO_GRID = 0x7402, 0x7071, 0x7074
LVS_AUTOARRANGE, LVS_EX_SNAPTOGRID, LVM_GETEXTENDEDLISTVIEWSTYLE = 0x100, 0x80000, 0x1037
DESKTOP_SORT = {"name": 0x7602, "size": 0x7603, "type": 0x7604, "date": 0x7605}


def desktop_view() -> int:
    progman = user32.FindWindowW("Progman", None)
    view = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if view:
        return view
    worker = None
    while True:
        worker = user32.FindWindowExW(None, worker, "WorkerW", None)
        if not worker:
            return 0
        view = user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)
        if view:
            return view


GA_ROOT = 2


def own_by_desktop(hwnd: int) -> bool:
    """Ein Fenster, das dem Desktop gehört, übersteht „Desktop anzeigen“ (Win+D): Windows räumt alles weg und holt den
    Desktop nach vorn, und was ihm gehört, liegt immer direkt über ihm."""
    view = desktop_view()
    owner = user32.GetAncestor(view, GA_ROOT) if view else user32.FindWindowW("Progman", None)
    if not owner:
        return False
    user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner)
    return True


WM_SPAWN_WORKER, SPI_SETDESKWALLPAPER = 0x052C, 20
WS_EX_LAYERED, LWA_ALPHA, SWP_SHOWWINDOW = 0x80000, 0x2, 0x40


def wallpaper_host() -> int:
    """Das Fenster zwischen Hintergrundbild und Symbolen. Windows legt es erst auf diese Nachricht hin an.
    Seit Windows 11 24H2 liegt es in Progman, davor als eigenes Fenster hinter den Symbolen."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(progman, WM_SPAWN_WORKER, 0xD, 1, SMTO_ABORTIFHUNG, 1000, ctypes.byref(result))
    if user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None):
        return user32.FindWindowExW(progman, None, "WorkerW", None) or 0
    worker = None
    while True:
        worker = user32.FindWindowExW(None, worker, "WorkerW", None)
        if not worker:
            return 0
        if user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None):
            return user32.FindWindowExW(None, worker, "WorkerW", None) or 0


def place_behind_icons(hwnd: int, host: int, rect: tuple[int, int, int, int]) -> None:
    """Seit Windows 11 24H2 erscheinen Kinder des Hosts nur als Layered-Fenster, sonst bleibt die Fläche leer."""
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_NOACTIVATE)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    corner = wintypes.POINT(rect[0], rect[1])
    user32.ScreenToClient(host, ctypes.byref(corner))
    user32.SetWindowPos(hwnd, HWND_TOP, corner.x, corner.y, rect[2] - rect[0], rect[3] - rect[1], SWP_NOACTIVATE | SWP_SHOWWINDOW)


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def redraw_wallpaper() -> None:
    """Zeigt das normale Hintergrundbild wieder sauber, wenn das Video weg ist."""
    user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, None, 3)


def foreground_is_fullscreen() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd or is_desktop_window(hwnd):
        return False
    return window_rect(hwnd) == window_monitor(hwnd)


@functools.lru_cache(maxsize=256)  # die Taskbar fragt das bei jeder Aktualisierung, das Manifest ändert sich nie
def packaged_app_target(exe: str) -> str:
    """Store-Apps tragen ihr Logo im Paket, die .exe hat oft nur das leere Standardsymbol. Liefert ihren Eintrag im App-Ordner."""
    match = re.search(r"\\WindowsApps\\([^\\_]+)_[^\\]*?__([^\\]+)\\", exe, re.I)
    if not match:
        return ""
    root = exe[:match.end()]
    try:
        manifest = (Path(root) / "AppxManifest.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    application = re.search(r"<Application\s[^>]*?\bId=\"([^\"]+)\"", manifest)
    return f"shell:AppsFolder\\{match[1]}_{match[2]}!{application[1]}" if application else ""


def desktop_command(command: int) -> None:
    view = desktop_view()
    if view:
        user32.PostMessageW(view, WM_COMMAND, command, 0)


def desktop_icon_state() -> dict[str, bool]:
    """Was gerade eingeschaltet ist; direkt von der Symbolliste, die Registry hinkt hinterher."""
    icons = user32.FindWindowExW(desktop_view(), None, "SysListView32", None)
    if not icons:
        return {"visible": False, "arrange": False, "grid": False}
    extended = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(icons, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0, SMTO_ABORTIFHUNG, 50,
                               ctypes.byref(extended))
    return {"visible": bool(user32.IsWindowVisible(icons)),
            "arrange": bool(user32.GetWindowLongPtrW(icons, GWL_STYLE) & LVS_AUTOARRANGE),
            "grid": bool(extended.value & LVS_EX_SNAPTOGRID)}


LVM_GETITEMCOUNT, LVM_SETITEMPOSITION, LVM_GETITEMPOSITION, LVM_GETITEMSPACING = 0x1004, 0x100F, 0x1010, 0x1033
PROCESS_VM = 0x0008 | 0x0010 | 0x0020 | 0x0400  # OPERATION, READ, WRITE, QUERY_INFORMATION
MEM_COMMIT, MEM_RELEASE, PAGE_READWRITE = 0x1000, 0x8000, 0x04


def _desktop_icons_window() -> int:
    return user32.FindWindowExW(desktop_view(), None, "SysListView32", None) or 0


def desktop_icon_positions() -> tuple[list[tuple[int, int]], tuple[int, int]]:
    """Ecke jedes Desktop-Symbols in Bildschirmpixeln, dazu die Rastergröße. Der Speicher muss im Explorer liegen,
    denn dort schreibt die Liste ihre Antwort hin."""
    icons = _desktop_icons_window()
    if not icons:
        return [], (0, 0)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(icons, ctypes.byref(pid))
    process = kernel32.OpenProcess(PROCESS_VM, False, pid.value)
    if not process:
        return [], (0, 0)
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD)
    kernel32.VirtualFreeEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD)
    kernel32.ReadProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SendMessageW.restype = ctypes.c_ssize_t
    remote = kernel32.VirtualAllocEx(process, None, ctypes.sizeof(wintypes.POINT), MEM_COMMIT, PAGE_READWRITE)
    positions = []
    try:
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(icons, ctypes.byref(origin))
        for index in range(user32.SendMessageW(icons, LVM_GETITEMCOUNT, 0, 0)):
            point = wintypes.POINT()
            if user32.SendMessageW(icons, LVM_GETITEMPOSITION, index, remote) and kernel32.ReadProcessMemory(
                    process, remote, ctypes.byref(point), ctypes.sizeof(point), None):
                positions.append((point.x + origin.x, point.y + origin.y))
            else:
                positions.append(None)
        spacing = user32.SendMessageW(icons, LVM_GETITEMSPACING, 0, 0)
    finally:
        kernel32.VirtualFreeEx(process, remote, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)
    return positions, (spacing & 0xFFFF, (spacing >> 16) & 0xFFFF)


def move_desktop_icon(index: int, x: int, y: int) -> None:
    icons = _desktop_icons_window()
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(icons, ctypes.byref(origin))
    left, top = x - origin.x, y - origin.y
    user32.SendMessageW(icons, LVM_SETITEMPOSITION, index, (left & 0xFFFF) | ((top & 0xFFFF) << 16))


def is_desktop_window(hwnd: int) -> bool:
    """Trifft auf die Desktop-Symbolfläche zu - auch wenn Wallpaper Engine läuft."""
    while hwnd:
        name = class_name(hwnd)
        if name in ("Progman", "WorkerW"):
            return True
        if name not in ("SysListView32", "SHELLDLL_DefView"):
            return False
        hwnd = user32.GetParent(hwnd)
    return False


def desktop_path() -> Path:
    from win32com.shell import shell, shellcon
    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))


# --- Tasten, Programme, Energie -------------------------------------------------
KEYEVENTF_KEYUP = 0x2
VK_LWIN, VK_CONTROL, VK_LEFT, VK_RIGHT, VK_TAB, VK_B, VK_RETURN = 0x5B, 0x11, 0x25, 0x27, 0x09, 0x42, 0x0D
VK_ESCAPE = 0x1B


def toggle_desktop() -> None:
    from win32com.client import Dispatch
    Dispatch("Shell.Application").ToggleDesktop()


def send_keys(*keys: int) -> None:
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def open_tray_overflow() -> None:
    """Win+B fokussiert den Infobereich, Enter klappt die versteckten Symbole aus."""
    send_keys(VK_LWIN, VK_B)
    time.sleep(0.08)
    send_keys(VK_RETURN)


def tray_overflow_window() -> int:
    """Das sichtbare Überlauf-Fenster des Infobereichs, sonst 0."""
    for cls in ("TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow"):
        hwnd = user32.FindWindowW(cls, None)
        if hwnd and user32.IsWindowVisible(hwnd):
            return hwnd
    return 0


def close_tray_overflow() -> None:
    send_keys(VK_ESCAPE)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def move_window(hwnd: int, x: int, y: int) -> None:
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def shell_open(target: str, args: str | None = None, cwd: str | None = None) -> bool:
    return int(shell32.ShellExecuteW(None, "open", target, args, cwd, 1)) > 32


shell32.ShellExecuteW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, ctypes.c_int)
shell32.ShellExecuteW.restype = ctypes.c_void_p


def shortcut_target(path: str) -> str:
    """Ziel einer .lnk-Datei; leer bei Store-Apps und defekten Verknüpfungen."""
    from win32com.client import Dispatch
    try:
        return Dispatch("WScript.Shell").CreateShortcut(path).TargetPath or ""
    except Exception as exc:
        log.debug("Verknüpfung nicht lesbar: %s", exc)
        return ""


# --- Rechtsklickmenü im Windows-Explorer -----------------------------------------------------
_CLASSIC_MENU = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
_POPWIN_VERBS = (r"Software\Classes\Directory\shell\PopWinExplorer",
                 r"Software\Classes\Directory\Background\shell\PopWinExplorer")


def _delete_tree(root: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{root}\\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root)
    except FileNotFoundError:
        pass


def classic_menu_enabled() -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLASSIC_MENU + r"\InprocServer32").Close()
        return True
    except OSError:
        return False


def set_classic_menu(enabled: bool) -> None:
    """Windows-11-Menü aus: Alle Einträge stehen sofort auf einer Ebene. Wirkt nach Explorer-Neustart."""
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _CLASSIC_MENU + r"\InprocServer32") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "")
    else:
        _delete_tree(_CLASSIC_MENU)


def explorer_verbs_enabled() -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, _POPWIN_VERBS[0]).Close()
        return True
    except OSError:
        return False


def set_explorer_verbs(enabled: bool, program: str, arguments: str, icon: str) -> None:
    """Eintrag „Im PopWin Explorer öffnen“ im Rechtsklickmenü von Ordnern."""
    for index, root in enumerate(_POPWIN_VERBS):
        if not enabled:
            _delete_tree(root)
            continue
        argument = "%V" if index else "%1"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Im PopWin Explorer öffnen")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{program}" {arguments} "{argument}"')


_OPEN_WITH_ID = "PopWin.Editor"
OPEN_WITH_TYPES = (".txt", ".md", ".log", ".ini", ".json", ".csv", ".ps1", ".xml")


def open_with_enabled() -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{_OPEN_WITH_ID}").Close()
        return True
    except OSError:
        return False


def set_open_with(enabled: bool, program: str, arguments: str, icon: str) -> None:
    """Trägt den Editor in „Öffnen mit“ ein. Standard-App darf nur der Mensch in den Windows-Einstellungen wählen."""
    root = rf"Software\Classes\{_OPEN_WITH_ID}"
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "PopWin Editor")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{program}" {arguments} "%1"')
    else:
        _delete_tree(root)
    for suffix in OPEN_WITH_TYPES:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{suffix}\OpenWithProgids") as key:
            if enabled:
                winreg.SetValueEx(key, _OPEN_WITH_ID, 0, winreg.REG_NONE, b"")
            else:
                try:
                    winreg.DeleteValue(key, _OPEN_WITH_ID)
                except OSError:
                    pass


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("fMask", wintypes.UINT), ("fType", wintypes.UINT), ("fState", wintypes.UINT),
                ("wID", wintypes.UINT), ("hSubMenu", wintypes.HMENU), ("hbmpChecked", wintypes.HBITMAP),
                ("hbmpUnchecked", wintypes.HBITMAP), ("dwItemData", ctypes.c_size_t), ("dwTypeData", wintypes.LPWSTR),
                ("cch", wintypes.UINT), ("hbmpItem", wintypes.HBITMAP)]


MIIM_STATE, MIIM_ID, MIIM_SUBMENU, MIIM_STRING, MIIM_FTYPE = 0x1, 0x2, 0x4, 0x40, 0x100
MFT_SEPARATOR, MFS_DISABLED = 0x800, 0x3
WM_INITMENUPOPUP = 0x0117
user32.GetMenuItemInfoW.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.BOOL, ctypes.POINTER(MENUITEMINFOW))
user32.GetMenuItemCount.argtypes = (wintypes.HMENU,)


@dataclass
class ShellEntry:
    label: str = ""  # leer: Trennlinie
    command: int = 0
    enabled: bool = True
    children: list = None


def clean_menu_label(text: str) -> str:
    """Ohne Tastenkürzel: „&Öffnen\tStrg+O“ wird zu „Öffnen“."""
    return text.split("\t")[0].replace("&&", "\0").replace("&", "").replace("\0", "&").strip()


class ShellMenu:
    """Das echte Windows-Kontextmenü dieser Dateien als Daten, samt 7-Zip, VS Code und allen anderen Erweiterungen.

    PopWin zeichnet die Einträge selbst; ausführen muss sie aber das Original, deshalb lebt es so lange wie das Menü.
    """

    def __init__(self, hwnd: int, paths: list[str]):
        import win32gui
        from win32com.shell import shell, shellcon
        self.hwnd = hwnd
        pidls = [shell.SHParseDisplayName(path, 0)[0] for path in paths]
        folder = shell.SHGetDesktopFolder().BindToObject(pidls[0][:-1], None, shell.IID_IShellFolder)
        self.context = folder.GetUIObjectOf(hwnd, [pidl[-1:] for pidl in pidls], shell.IID_IContextMenu, 0)[1]
        self.menu = win32gui.CreatePopupMenu()
        self.context.QueryContextMenu(self.menu, 0, 1, 0x7FFF, shellcon.CMF_NORMAL)
        try:
            self.dynamic = self.context.QueryInterface(shell.IID_IContextMenu2)
        except Exception:  # alte Erweiterungen kennen das neuere Protokoll nicht
            self.dynamic = None

    def _fill(self, submenu: int, index: int) -> None:
        """Manche Programme füllen ihr Untermenü erst, wenn es aufklappt."""
        if self.dynamic is not None:
            try:
                self.dynamic.HandleMenuMsg(WM_INITMENUPOPUP, submenu, index)
            except Exception as exc:
                log.debug("Untermenü nicht gefüllt: %s", exc)

    def entries(self, menu: int | None = None) -> list[ShellEntry]:
        menu = self.menu if menu is None else menu
        found = []
        for index in range(user32.GetMenuItemCount(menu)):
            info = MENUITEMINFOW(cbSize=ctypes.sizeof(MENUITEMINFOW))
            info.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING | MIIM_FTYPE
            if not user32.GetMenuItemInfoW(menu, index, True, ctypes.byref(info)):
                continue
            if info.fType & MFT_SEPARATOR:
                found.append(ShellEntry())
                continue
            text = ctypes.create_unicode_buffer(info.cch + 1)
            info.dwTypeData = ctypes.cast(text, wintypes.LPWSTR)
            info.cch += 1
            user32.GetMenuItemInfoW(menu, index, True, ctypes.byref(info))
            label = clean_menu_label(text.value)
            if not label:  # selbst gezeichnete Einträge ohne Text lassen sich nicht nachbauen
                continue
            children = None
            if info.hSubMenu:
                self._fill(info.hSubMenu, index)
                children = self.entries(info.hSubMenu)
            found.append(ShellEntry(label, info.wID, not info.fState & MFS_DISABLED, children))
        return found

    def run(self, command: int) -> None:
        self.context.InvokeCommand((0, self.hwnd, command - 1, None, None, 1, 0, None))

    def close(self) -> None:
        import win32gui
        win32gui.DestroyMenu(self.menu)


def restart_explorer() -> None:
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW)


def shortcut_icon_file(path: str) -> str:
    """Datei mit dem Icon einer Verknüpfung. Die eigene Icon-Angabe zählt vor dem Ziel: Discord startet
    über Update.exe, sein Symbol liegt aber in app.ico."""
    from win32com.client import Dispatch
    try:
        shortcut = Dispatch("WScript.Shell").CreateShortcut(path)
        listed = os.path.expandvars(shortcut.IconLocation.rsplit(",", 1)[0])
        target = shortcut.TargetPath
    except Exception as exc:
        log.debug("Verknüpfung nicht lesbar: %s", exc)
        return ""
    return next((file for file in (listed, target) if file and os.path.exists(file)), "")


def _autostart_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/PopWin.lnk"


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_NAME = "PopWin"
AUTOSTART_FLAG = "--autostart"
LOGON_TASK = "PopWin"
SHELL_WAIT_SECONDS = 60


def _run_value() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except OSError:
        return ""


def autostart_enabled() -> bool:
    return bool(_run_value()) or _autostart_link().exists()


def autostart_command(program: str, arguments: str) -> str:
    return " ".join(part for part in (f'"{program}"', arguments, AUTOSTART_FLAG) if part)


def wait_for_shell() -> None:
    """Der Run-Eintrag kommt früh, oft vor der Taskleiste. PopWin soll sie ersetzen, also muss sie erst da sein."""
    end = time.monotonic() + SHELL_WAIT_SECONDS
    while not user32.FindWindowW("Shell_TrayWnd", None) and time.monotonic() < end:
        time.sleep(0.2)


def logon_task_exists() -> bool:
    return subprocess.run(["schtasks", "/Query", "/TN", LOGON_TASK], capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW, check=False).returncode == 0


def logon_task_arguments(program: str, arguments: str) -> str:
    command = autostart_command(program, arguments).replace('"', '\\"')
    return f'/Create /TN {LOGON_TASK} /TR "{command}" /SC ONLOGON /RL LIMITED /IT /F'


def run_elevated(program: str, arguments: str) -> bool:
    """Windows fragt selbst nach. Lehnt man ab, kommt ein Wert bis 32 zurück und nichts ist passiert."""
    return int(shell32.ShellExecuteW(None, "runas", program, arguments, None, 0) or 0) > 32


def _write_shortcut(link: Path, program: str, arguments: str, icon: str = "") -> None:
    from win32com.client import Dispatch
    shortcut = Dispatch("WScript.Shell").CreateShortcut(str(link))
    shortcut.TargetPath = program
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = str(Path(arguments.strip('"') or program).parent)
    shortcut.Description = "PopWin Desktop-Oberfläche"
    if icon:
        shortcut.IconLocation = icon
    shortcut.Save()


def set_autostart(enabled: bool, program: str, arguments: str) -> None:
    """Über den Run-Schlüssel: Der läuft vor dem Autostart-Ordner, den Windows nach dem Anmelden lange zurückhält."""
    _autostart_link().unlink(missing_ok=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command(program, arguments))
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass
    (clear_startup_delay if enabled else restore_startup_delay)()
    try:  # hat man es im Task-Manager einmal abgeschaltet, stünde dort noch die alte Sperre
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_NAME)
    except OSError:
        pass


SERIALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize"
NO_DELAY = {"StartupDelayInMSec": 0, "WaitForIdleState": 0}
NO_DELAY_MARK = "PopWinSetThis"  # nur was PopWin selbst gesetzt hat, nimmt es beim Abschalten wieder weg


def startup_delay_cleared() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY) as key:
            return all(winreg.QueryValueEx(key, name)[0] == 0 for name in NO_DELAY)
    except OSError:
        return False


def clear_startup_delay() -> None:
    """Windows hält Autostart-Programme nach dem Anmelden mehrere Minuten zurück, gemessen bis 6,5 Minuten.
    Diese beiden Werte gelten nur für dieses Benutzerkonto und lassen sie sofort starten."""
    if startup_delay_cleared():
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY) as key:
        for name, value in NO_DELAY.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
        winreg.SetValueEx(key, NO_DELAY_MARK, 0, winreg.REG_DWORD, 1)


def restore_startup_delay() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SERIALIZE_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            winreg.QueryValueEx(key, NO_DELAY_MARK)
            for name in (*NO_DELAY, NO_DELAY_MARK):
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
    except OSError:  # nicht von PopWin gesetzt: Dann gehört es dem Nutzer und bleibt
        pass


def move_autostart_link(program: str, arguments: str) -> None:
    """Wer PopWin schon im Autostart-Ordner hat, bekommt den schnelleren Weg von selbst."""
    if _autostart_link().exists():
        set_autostart(True, program, arguments)
    elif _run_value():
        clear_startup_delay()  # wer den Autostart schon vor diesem Update eingeschaltet hatte


def start_menu_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/PopWin.lnk"


def ensure_start_menu_entry(program: str, arguments: str, icon: str) -> None:
    """Hält den Eintrag aktuell, auch wenn der Projektordner verschoben wurde."""
    link = start_menu_link()
    if not link.exists() or Path(shortcut_target(str(link))) != Path(program):
        _write_shortcut(link, program, arguments, icon)


def lock_workstation() -> None:
    user32.LockWorkStation()


def sleep_system() -> None:
    ctypes.WinDLL("powrprof").SetSuspendState(False, False, False)


def hibernate_available() -> bool:
    return bool(ctypes.WinDLL("powrprof").IsPwrHibernateAllowed())


def hibernate() -> None:
    ctypes.WinDLL("powrprof").SetSuspendState(True, False, False)


def sign_out() -> None:
    subprocess.Popen(["shutdown", "/l"], creationflags=subprocess.CREATE_NO_WINDOW)


def shutdown() -> None:
    subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)


def restart() -> None:
    subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)


# --- Virtuelle Desktops, Mikrofon ----------------------------------------------
_VDESK = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"


def _session_id() -> int:
    session = wintypes.DWORD(0)
    kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    return session.value


def _registry_value(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def virtual_desktops() -> tuple[int, int]:
    """(Anzahl, Index des aktuellen). Windows 10 legt den aktuellen Desktop pro Sitzung unter SessionInfo ab."""
    ids = _registry_value(_VDESK, "VirtualDesktopIDs")
    if not ids:
        return 1, 0
    current = _registry_value(_VDESK, "CurrentVirtualDesktop") or _registry_value(
        rf"Software\Microsoft\Windows\CurrentVersion\Explorer\SessionInfo\{_session_id()}\VirtualDesktops", "CurrentVirtualDesktop")
    chunks = [ids[i:i + 16] for i in range(0, len(ids), 16)]
    return max(1, len(chunks)), chunks.index(current) if current in chunks else 0


def switch_virtual_desktop(steps: int) -> None:
    arrow = VK_RIGHT if steps > 0 else VK_LEFT
    for _ in range(abs(steps)):
        send_keys(VK_CONTROL, VK_LWIN, arrow)
        time.sleep(0.05)


_MIC = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _mic_users(path: str) -> list[str]:
    users: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            count = winreg.QueryInfoKey(key)[0]
            for index in range(count):
                sub = winreg.EnumKey(key, index)
                if sub == "NonPackaged":
                    users += _mic_users(f"{path}\\{sub}")
                    continue
                with winreg.OpenKey(key, sub) as app:
                    try:
                        if winreg.QueryValueEx(app, "LastUsedTimeStop")[0] == 0:
                            users.append(sub.replace("#", "\\").rsplit("\\", 1)[-1])
                    except OSError:
                        pass
    except OSError:
        pass
    return users


def microphone_users() -> list[str]:
    """Windows vermerkt pro App, wann das Mikrofon zuletzt losgelassen wurde; 0 = läuft gerade."""
    return _mic_users(_MIC)
