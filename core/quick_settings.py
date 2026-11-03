"""Nicht stören, Benachrichtigungen und Fokus-Timer für die Schnelleinstellungen."""
from __future__ import annotations

import ctypes
import logging
import time
import winreg
from ctypes import wintypes
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QTimer, Signal

from core import win_api

log = logging.getLogger(__name__)

_PUSH = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
# Windows hat dafür keine offizielle Schnittstelle; diese Statuskennung nutzt das System selbst.
_QUIET_HOURS_STATE = 0x0D83063EA3BF1C75
QUIET_OFF, QUIET_PRIORITY_ONLY = 0, 1

_ntdll = ctypes.WinDLL("ntdll")


def format_remaining(seconds: int) -> str:
    minutes, rest = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{rest:02d}"


def do_not_disturb() -> bool:
    state = ctypes.c_ulonglong(_QUIET_HOURS_STATE)
    stamp, size = wintypes.ULONG(0), wintypes.ULONG(4)
    value = wintypes.DWORD(0)
    status = _ntdll.ZwQueryWnfStateData(ctypes.byref(state), None, None, ctypes.byref(stamp),
                                        ctypes.byref(value), ctypes.byref(size))
    return status == 0 and value.value != QUIET_OFF


def set_do_not_disturb(enabled: bool) -> bool:
    state = ctypes.c_ulonglong(_QUIET_HOURS_STATE)
    value = wintypes.DWORD(QUIET_PRIORITY_ONLY if enabled else QUIET_OFF)
    status = _ntdll.ZwUpdateWnfStateData(ctypes.byref(state), ctypes.byref(value), 4, None, None, 0, 0)
    if status != 0:
        log.warning("Nicht stören nicht setzbar: %#x", status & 0xFFFFFFFF)
    return status == 0


def notifications_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PUSH) as key:
            return bool(winreg.QueryValueEx(key, "ToastEnabled")[0])
    except OSError:
        return True


def set_notifications_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PUSH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ToastEnabled", 0, winreg.REG_DWORD, 1 if enabled else 0)
    win_api.broadcast_setting_change("Notifications")


class FocusTimer(QObject):
    """Fokus-Sitzung von Windows; ohne Unterstützung Nicht stören plus eigener Countdown."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._end = 0.0
        self._session = None
        self._fallback = False
        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._on_tick)

    @property
    def active(self) -> bool:
        return self._end > 0

    @property
    def remaining(self) -> int:
        return max(0, round(self._end - time.monotonic())) if self.active else 0

    @staticmethod
    def _manager():
        from winsdk.windows.ui.shell import FocusSessionManager
        return FocusSessionManager.get_default() if FocusSessionManager.is_supported else None

    def start(self, minutes: int) -> None:
        self.stop()
        try:
            manager = self._manager()
            end = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            self._session = manager.try_start_focus_session(end) if manager else None
        except Exception as exc:
            log.warning("Fokus-Sitzung nicht startbar: %s", exc)
            self._session = None
        self._fallback = self._session is None
        if self._fallback:
            set_do_not_disturb(True)
        self._end = time.monotonic() + minutes * 60
        self._tick.start()
        self.changed.emit()

    def stop(self) -> None:
        if not self.active:
            return
        if self._fallback:
            set_do_not_disturb(False)
        else:
            try:
                self._manager().deactivate_focus()
            except Exception as exc:
                log.warning("Fokus-Sitzung nicht beendbar: %s", exc)
        self._reset()

    def _reset(self) -> None:
        self._end = 0.0
        self._session = None
        self._fallback = False
        self._tick.stop()
        self.changed.emit()

    def _on_tick(self) -> None:
        if self.remaining == 0:
            if self._fallback:
                set_do_not_disturb(False)
            self._reset()
            return
        if not self._fallback and not self._still_running():
            self._reset()  # in der Uhr-App oder den Windows-Einstellungen beendet
            return
        self.changed.emit()

    def _still_running(self) -> bool:
        try:
            return self._manager().is_focus_active
        except Exception:
            return True
