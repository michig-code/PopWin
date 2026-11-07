"""Globale Eingaben: Hotkeys, Rechtsklick auf den Desktop, optional Win+S.

Läuft in einem eigenen Thread mit eigener Nachrichtenschleife. So bleibt die Maus flüssig,
auch wenn die Oberfläche kurz beschäftigt ist.
"""
from __future__ import annotations

import ctypes
import gc
import logging
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal

from core import win_api

log = logging.getLogger(__name__)

user32 = win_api.user32
kernel32 = win_api.kernel32
LRESULT = win_api.LRESULT
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT, WM_HOTKEY = 0x0012, 0x0312
WM_PAUSE_MOUSE = 0x8000 + 0x51  # WM_APP: wParam 1 = Maus-Hook ab, 0 = wieder an
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
VK_S, VK_LWIN, VK_RWIN, VK_DUMMY = 0x53, 0x5B, 0x5C, 0xE8
VK_SHIFT, VK_CONTROL, VK_ALT = 0x10, 0x11, 0x12
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
UIA_LIST_ITEM = 50007
HOTKEY_LAUNCHER, HOTKEY_KILL, HOTKEY_NOVA = 1, 2, 3
FALLBACK_HOTKEYS = ("alt+shift+space", "ctrl+shift+space")

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_MODIFIERS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """'alt+space' -> (Modifier, virtueller Tastencode); None bei ungültiger Eingabe."""
    mods, key = 0, None
    for part in (p.strip().lower() for p in text.split("+")):
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif part in _KEYS and key is None:
            key = _KEYS[part]
        elif len(part) == 1 and part.isalnum() and key is None:
            key = ord(part.upper())
        else:
            return None
    return (mods, key) if key is not None else None


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class ShellHooks(QThread):
    launcher_requested = Signal()
    kill_requested = Signal()
    desktop_right_click = Signal()
    win_key_tapped = Signal()
    nova_requested = Signal()
    hotkey_active = Signal(str)  # die tatsächlich belegte Kombination, leer wenn keine frei war

    def __init__(self, hotkey: str, desktop_menu: bool, override_win_s: bool, remap_win_key: bool,
                 nova_hotkey: str = "", parent=None):
        super().__init__(parent)
        self.hotkey = hotkey
        self.desktop_menu = desktop_menu
        self.override_win_s = override_win_s
        self.remap_win_key = remap_win_key
        self.nova_hotkey = nova_hotkey
        self._win_down = False
        self._win_alone = False
        self._thread_id = 0
        self._swallow_up = False
        self._uia = None
        self._mouse_hook = None
        self._mouse_proc = None
        self._module = None
        self.mouse_paused = False  # im Vollbild-Spiel: kein Maus-Hook, siehe pause_mouse

    def pause_mouse(self, paused: bool) -> None:
        """Nimmt den Maus-Hook weg, solange ein Vollbild-Spiel den Fokus hat.

        Ein Low-Level-Maus-Hook läuft für jede Mausbewegung durch Python. Spiele mit 1000-Hz-Mäusen
        (Fortnite) schicken tausend Bewegungen pro Sekunde, und jede wartet, bis Python frei ist -
        die Maus ruckelt oder hängt. Den Desktop-Rechtsklick braucht man im Spiel ohnehin nicht.
        """
        if paused == self.mouse_paused:
            return
        self.mouse_paused = paused
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_PAUSE_MOUSE, int(paused), 0)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.wait(2000)

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = self._register_hotkeys()
        module = self._module = kernel32.GetModuleHandleW(None)
        self._mouse_proc, key_proc = HOOKPROC(self._on_mouse), HOOKPROC(self._on_key)
        hooks = []
        if self.desktop_menu:
            self._init_uia()
            self._set_mouse_hook(not self.mouse_paused)
        if self.override_win_s or self.remap_win_key:
            hooks.append(user32.SetWindowsHookExW(WH_KEYBOARD_LL, key_proc, module, 0))

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                {HOTKEY_LAUNCHER: self.launcher_requested, HOTKEY_KILL: self.kill_requested,
                 HOTKEY_NOVA: self.nova_requested}[msg.wParam].emit()
            elif msg.message == WM_PAUSE_MOUSE and self.desktop_menu:
                self._set_mouse_hook(not self.mouse_paused)  # der neueste Stand zählt, nicht die Reihenfolge der Meldungen

        self._set_mouse_hook(False)
        for hook in hooks:
            if hook:
                user32.UnhookWindowsHookEx(hook)
        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)
        self._release_uia()

    def _set_mouse_hook(self, on: bool) -> None:
        """Nur im Hook-Thread aufrufen: Windows bindet den Hook an den Thread, der ihn setzt."""
        if on and not self._mouse_hook:
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, self._module, 0) or None
        elif not on and self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
            self._swallow_up = False  # ein halber Rechtsklick darf nach dem Spiel nicht hängen bleiben

    def _release_uia(self) -> None:
        # COM-Objekte gehören diesem Thread und müssen hier freigegeben werden, nicht beim
        # späteren Aufräumen im Hauptthread - das führt sonst zu einer Zugriffsverletzung.
        if self._uia is not None:
            import comtypes
            self._uia = None
            gc.collect()
            comtypes.CoUninitialize()

    def _register_hotkeys(self) -> list[int]:
        registered = []
        active = ""
        # Ein anderes Programm kann das Kürzel schon belegt haben - der Launcher muss trotzdem erreichbar sein.
        for text in dict.fromkeys((self.hotkey, *FALLBACK_HOTKEYS)):
            parsed = parse_hotkey(text)
            if parsed and user32.RegisterHotKey(None, HOTKEY_LAUNCHER, parsed[0] | MOD_NOREPEAT, parsed[1]):
                registered.append(HOTKEY_LAUNCHER)
                active = text
                break
        if active != self.hotkey:
            log.warning("Hotkey %s nicht verfügbar, nutze %r", self.hotkey, active)
        self.hotkey_active.emit(active)
        nova = parse_hotkey(self.nova_hotkey) if self.nova_hotkey else None
        if nova and user32.RegisterHotKey(None, HOTKEY_NOVA, nova[0] | MOD_NOREPEAT, nova[1]):
            registered.append(HOTKEY_NOVA)
        elif nova:
            log.warning("Nova-Hotkey %s ist belegt", self.nova_hotkey)
        mods, key = parse_hotkey("ctrl+alt+shift+k")
        if user32.RegisterHotKey(None, HOTKEY_KILL, mods | MOD_NOREPEAT, key):
            registered.append(HOTKEY_KILL)
        return registered

    def _init_uia(self) -> None:
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
            self._uia = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            self._point_type = uia.tagPOINT
        except Exception as exc:
            log.warning("UI Automation nicht verfügbar: %s", exc)
            self._uia = None

    def _empty_desktop_at(self, x: int, y: int) -> bool:
        """Nur freie Fläche gehört uns; auf einem Symbol bleibt das Windows-Menü."""
        if not win_api.is_desktop_window(user32.WindowFromPoint(wintypes.POINT(x, y))):
            return False
        if self._uia is None:
            return False
        try:
            element = self._uia.ElementFromPoint(self._point_type(x, y))
            return element.CurrentControlType != UIA_LIST_ITEM
        except Exception as exc:
            log.debug("ElementFromPoint: %s", exc)
            return False

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        if code == 0 and wparam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if wparam == WM_RBUTTONDOWN:
                self._swallow_up = self._empty_desktop_at(info.pt.x, info.pt.y)
                if self._swallow_up:
                    return 1
            elif self._swallow_up:
                self._swallow_up = False
                self.desktop_right_click.emit()
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    @staticmethod
    def _win_held() -> bool:
        return any(user32.GetAsyncKeyState(key) & 0x8000 for key in (VK_LWIN, VK_RWIN))

    @staticmethod
    def _other_modifier_held() -> bool:
        return any(user32.GetAsyncKeyState(key) & 0x8000 for key in (VK_SHIFT, VK_CONTROL, VK_ALT))

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            key = info.vkCode
            if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                if key in (VK_LWIN, VK_RWIN):
                    if not self._win_down:  # gehaltene Tasten wiederholen sich
                        self._win_down = self._win_alone = True
                elif self._win_down and not self._win_held():
                    # Das Loslassen kam nie an, zum Beispiel weil Win+L den Sperrbildschirm geöffnet hat.
                    # Ohne diese Probe zählte danach jedes S als Win+S und wäre nicht mehr tippbar.
                    self._win_down = self._win_alone = False
                elif self._win_down and key != VK_DUMMY:
                    self._win_alone = False  # Kombination wie Win+D gehört Windows
                    # Nur das reine Win+S: Win+Umschalt+S ist das Screenshot-Werkzeug von Windows.
                    if key == VK_S and self.override_win_s and not self._other_modifier_held():
                        # Neutraler Tastendruck, sonst öffnet das Loslassen von Win das Startmenü.
                        win_api.send_keys(VK_DUMMY)
                        self.launcher_requested.emit()
                        return 1
            elif key in (VK_LWIN, VK_RWIN) and self._win_down:
                self._win_down = False
                if self._win_alone and self.remap_win_key:
                    win_api.send_keys(VK_DUMMY)
                    self.win_key_tapped.emit()
        return user32.CallNextHookEx(None, code, wparam, lparam)
