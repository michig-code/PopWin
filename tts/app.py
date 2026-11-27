# --- [Antigravity Start] TexttoSpeech for Windows Overlay-Anwendung ---
"""Eigenständiges, rahmenloses Always-on-Top Cyber-/PopWin-Overlay-Fenster für TexttoSpeech for Windows."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)
import shiboken6

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.config_manager import ConfigManager  # noqa: E402
from core import i18n, theme_maker, win_api  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402
from core.shell_hooks import parse_hotkey  # noqa: E402
from core.theme_manager import ThemeManager  # noqa: E402
from tts.audio import (  # noqa: E402
    STYLES,
    RobotVoiceEngine,
    capture_default_audio_names,
    is_scream_text,
    restore_normal_audio,
    unlock_virtual_microphone,
)

log = logging.getLogger("tts.app")

APP_TITLE = "TexttoSpeech for Windows"
APP_AUMID = "PopWin.TexttoSpeechForWindows"
IPC_SERVER_NAME = "PopWin.TTSForWindows.IPC"
MUTEX_NAME = "PopWin.TTSForWindows.Mutex"
POS_STATE_FILE = DATA_DIR / "tts_for_windows_pos.json"

# Win32-Konstanten & sauber typisierte User32/Kernel32-Aufrufe
HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
WM_SYSCOMMAND = 0x0112
SC_MAXIMIZE = 0xF030
WM_NCLBUTTONDBLCLK = 0x00A3
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0xBEEF
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

_user32 = ctypes.windll.user32
_user32.RegisterHotKey.argtypes = (wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT)
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, wintypes.INT)
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    wintypes.INT,
    wintypes.INT,
    wintypes.INT,
    wintypes.INT,
    wintypes.UINT,
)
_user32.SetWindowPos.restype = wintypes.BOOL

_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.GetLastError.argtypes = ()
_kernel32.GetLastError.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL

DEFAULT_CONFIG: dict[str, object] = {
    "voice": "auto",
    "robot_style": "cyber",
    "rate": 0,
    "ui_theme": "cyber",
    "hotkey": "alt+y",
    "scream_auto": True,
    "scream_boost": 280,
    "virtual_volume": 100,
    "headphone_device": "default",
    "headphone_volume": 85,
    "virtual_device": "auto",
    "mic_passthrough": False,
    "input_device": "auto",
    "respect_fullscreen": False,
    "window_pos": [],
    "preferred_input": "",
    "preferred_output": "",
    "theme": "pop_basic",
    "mode": "dark",
    "language": "auto",
}

_LEGACY_TO_CANONICAL_KEYS: tuple[tuple[str, str], ...] = (
    ("voice_id", "voice"),
    ("speech_rate", "rate"),
    ("normal_mic_volume", "virtual_volume"),
    ("mic_device", "input_device"),
)


def _normalize_canonical_config(raw: dict, current: dict[str, object] | None = None) -> dict[str, object]:
    """Normalisiert optionale Legacy-Settings-Keys auf kanonische Keys, wobei kanonische Keys immer Vorrang haben."""
    out: dict[str, object] = dict(raw)
    for legacy_key, canon_key in _LEGACY_TO_CANONICAL_KEYS:
        if canon_key in out or legacy_key not in out:
            continue
        if current is None or current.get(canon_key) == DEFAULT_CONFIG.get(canon_key):
            out[canon_key] = out[legacy_key]
    return out

_BUILTIN_SURFACES: dict[str, tuple[str, str]] = {
    "pop_basic": ("#242424", "#1b1b1b"),
    "clean": ("#161616", "#101010"),
    "minimalist": ("#101010", "#090909"),
    "liquid_glass": ("#1a202d", "#111620"),
    "glass_clear": ("#18202c", "#0f141c"),
    "glass_smoke": ("#1c1c22", "#0b0b0e"),
    "tiles_graphite": ("#1e2228", "#101216"),
    "tiles_obsidian": ("#111317", "#07080a"),
    "tiles_frost": ("#243042", "#121824"),
    "neon_night": ("#120c24", "#0b0816"),
}


@dataclass(frozen=True)
class ThemePalette:
    bg_top: QColor
    bg_bottom: QColor
    border_a: QColor
    border_b: QColor
    border_c: QColor
    glow_color: QColor
    eq_bg: QColor
    eq_border: QColor
    eq_top: QColor
    eq_bottom: QColor
    input_bg: str
    input_focus_bg: str
    input_fg: str
    input_border: str
    input_focus_border: str
    selection_bg: str
    selection_fg: str
    btn_bg: str
    btn_hover: str
    btn_fg: str
    btn_border: str
    scream_hover_border: str
    send_bg: str
    send_hover: str
    send_fg: str
    corner_radius: float
    is_light: bool


class _ThemeConfigView:
    """Leichte Proxy-Ansicht auf das In-Memory-Config-Dict für ThemeManager."""

    def __init__(self, config: dict):
        self._config = config

    def get(self, key: str, default=None):
        return self._config.get(key, default)


def _rgba_css(color: QColor, alpha: float) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha:.3f})"


def _is_bright(color: QColor) -> bool:
    return (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) > 155


def _color_from_tint(tint: str, fallback: str, alpha: int) -> QColor:
    raw = (tint or "").lstrip("#")
    if len(raw) >= 6:
        try:
            col = QColor(f"#{raw[:6]}")
            if col.isValid():
                col.setAlpha(alpha)
                return col
        except Exception:
            pass
    col = QColor(fallback)
    col.setAlpha(alpha)
    return col


def _resolve_palette(
    ui_theme: str,
    theme_mgr: ThemeManager,
    popwin_theme: str,
    mode: str,
    scream: bool,
) -> ThemePalette:
    """Berechnet die komplette Farbpalette für 'cyber' oder das aktive PopWin-Theme."""
    light = ui_theme == "popwin" and mode == "light"

    if ui_theme != "popwin":
        if scream:
            return ThemePalette(
                bg_top=QColor(19, 13, 24, 248),
                bg_bottom=QColor(11, 8, 16, 252),
                border_a=QColor(255, 30, 86, 238),
                border_b=QColor(255, 120, 0, 235),
                border_c=QColor(255, 190, 11, 235),
                glow_color=QColor(255, 42, 84, 220),
                eq_bg=QColor("#181224"),
                eq_border=QColor("#ff2a54"),
                eq_top=QColor("#ffbe0b"),
                eq_bottom=QColor("#ff1e56"),
                input_bg="rgba(16, 23, 38, 0.92)",
                input_focus_bg="rgba(20, 29, 48, 0.98)",
                input_fg="#f0f6fc",
                input_border="rgba(255, 60, 90, 0.55)",
                input_focus_border="#ff2a54",
                selection_bg="#ff2a54",
                selection_fg="#090d16",
                btn_bg="rgba(25, 34, 54, 0.85)",
                btn_hover="rgba(40, 52, 80, 0.95)",
                btn_fg="#b8c7e0",
                btn_border="rgba(255, 255, 255, 0.12)",
                scream_hover_border="rgba(255, 120, 80, 0.55)",
                send_bg="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff1e56, stop:1 #ff8800)",
                send_hover="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff4775, stop:1 #ffa433)",
                send_fg="#ffffff",
                corner_radius=19.0,
                is_light=False,
            )
        return ThemePalette(
            bg_top=QColor(13, 19, 33, 248),
            bg_bottom=QColor(8, 12, 22, 252),
            border_a=QColor(0, 240, 255, 225),
            border_b=QColor(58, 134, 255, 215),
            border_c=QColor(131, 56, 236, 225),
            glow_color=QColor(0, 240, 255, 210),
            eq_bg=QColor("#10192b"),
            eq_border=QColor("#00f0ff"),
            eq_top=QColor("#00f0ff"),
            eq_bottom=QColor("#8338ec"),
            input_bg="rgba(16, 23, 38, 0.92)",
            input_focus_bg="rgba(20, 29, 48, 0.98)",
            input_fg="#f0f6fc",
            input_border="rgba(0, 224, 255, 0.32)",
            input_focus_border="#00f0ff",
            selection_bg="#00d4ff",
            selection_fg="#090d16",
            btn_bg="rgba(25, 34, 54, 0.85)",
            btn_hover="rgba(40, 52, 80, 0.95)",
            btn_fg="#b8c7e0",
            btn_border="rgba(255, 255, 255, 0.12)",
            scream_hover_border="rgba(255, 120, 80, 0.50)",
            send_bg="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #00f0ff, stop:1 #5e60ce)",
            send_hover="qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #56f6ff, stop:1 #7b7df0)",
            send_fg="#070b14",
            corner_radius=19.0,
            is_light=False,
        )

    # PopWin-Theme-Farben aus ThemeManager + theme_maker auslesen
    meta = theme_mgr.meta(popwin_theme)
    custom_spec = None
    if theme_maker.is_custom(popwin_theme):
        custom_spec = theme_maker.load_spec(popwin_theme) or theme_maker.DEFAULT_SPEC
    elif popwin_theme == "neon_night":
        custom_spec = theme_maker.NEON_SPEC

    if custom_spec and not light:
        accent1 = QColor(custom_spec["accent"])
        accent2 = QColor(custom_spec["accent2"])
        bg_top = QColor(custom_spec["bg2"])
        bg_bottom = QColor(custom_spec["bg"])
        bg_top.setAlpha(242)
        bg_bottom.setAlpha(248)
        corner = float(max(8, min(20, int(custom_spec.get("corner", 18)))))
    elif light:
        accent1 = QColor(meta.get("ind_light") or "#0078B8")
        accent2 = QColor(meta.get("ind_active_light") or accent1.name())
        if custom_spec:
            accent1 = QColor(custom_spec["accent"])
            accent2 = QColor(custom_spec["accent2"])
        bg_top = QColor(248, 249, 252, 246)
        bg_bottom = QColor(235, 239, 246, 250)
        corner = float(max(8, min(20, int(meta.get("corner") or 16) or 16)))
    else:
        accent1 = QColor(meta.get("ind") or "#48B9C7")
        accent2 = QColor(meta.get("ind_active") or accent1.name())
        if accent2.name().lower() == "#ffffff" and accent1.name().lower() != "#ffffff":
            accent2 = accent1.lighter(118)
        base_top, base_bot = _BUILTIN_SURFACES.get(popwin_theme, ("#1c202a", "#12151d"))
        tint = meta.get("tint", "")
        bg_top = _color_from_tint(tint, base_top, 242) if popwin_theme not in _BUILTIN_SURFACES else QColor(base_top)
        bg_bottom = _color_from_tint(tint, base_bot, 248) if popwin_theme not in _BUILTIN_SURFACES else QColor(base_bot)
        bg_top.setAlpha(242)
        bg_bottom.setAlpha(248)
        raw_corner = int(meta.get("corner") or 0)
        corner = float(max(8, min(20, raw_corner or (16 if meta.get("rounded") == "1" else 11))))

    if scream:
        accent1 = QColor("#ff1e56")
        accent2 = QColor("#ff7b00")
        border_mid = QColor("#ff5238")
    else:
        border_mid = QColor(
            (accent1.red() + accent2.red()) // 2,
            (accent1.green() + accent2.green()) // 2,
            (accent1.blue() + accent2.blue()) // 2,
        )

    send_fg = "#090d16" if _is_bright(accent1) and not scream else "#ffffff"
    send_hover_a = accent1.lighter(115).name()
    send_hover_b = accent2.lighter(115).name()

    if light:
        return ThemePalette(
            bg_top=bg_top,
            bg_bottom=bg_bottom,
            border_a=QColor(accent1.red(), accent1.green(), accent1.blue(), 220),
            border_b=QColor(border_mid.red(), border_mid.green(), border_mid.blue(), 210),
            border_c=QColor(accent2.red(), accent2.green(), accent2.blue(), 220),
            glow_color=QColor(accent1.red(), accent1.green(), accent1.blue(), 150),
            eq_bg=QColor(255, 255, 255, 235),
            eq_border=accent1,
            eq_top=accent1,
            eq_bottom=accent2,
            input_bg="rgba(255, 255, 255, 0.94)",
            input_focus_bg="#ffffff",
            input_fg="#14171d",
            input_border=_rgba_css(accent1, 0.35),
            input_focus_border=accent1.name(),
            selection_bg=accent1.name(),
            selection_fg=send_fg,
            btn_bg="rgba(20, 23, 29, 0.06)",
            btn_hover="rgba(20, 23, 29, 0.13)",
            btn_fg="#1e232d",
            btn_border="rgba(20, 23, 29, 0.14)",
            scream_hover_border="rgba(255, 65, 95, 0.55)",
            send_bg=f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {accent1.name()}, stop:1 {accent2.name()})",
            send_hover=f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {send_hover_a}, stop:1 {send_hover_b})",
            send_fg=send_fg,
            corner_radius=corner,
            is_light=True,
        )

    return ThemePalette(
        bg_top=bg_top,
        bg_bottom=bg_bottom,
        border_a=QColor(accent1.red(), accent1.green(), accent1.blue(), 220),
        border_b=QColor(border_mid.red(), border_mid.green(), border_mid.blue(), 210),
        border_c=QColor(accent2.red(), accent2.green(), accent2.blue(), 220),
        glow_color=QColor(accent1.red(), accent1.green(), accent1.blue(), 185),
        eq_bg=bg_bottom.lighter(130),
        eq_border=accent1,
        eq_top=accent1,
        eq_bottom=accent2,
        input_bg="rgba(255, 255, 255, 0.08)",
        input_focus_bg="rgba(255, 255, 255, 0.13)",
        input_fg="#f2f6fc",
        input_border=_rgba_css(accent1, 0.34),
        input_focus_border=accent1.name(),
        selection_bg=accent1.name(),
        selection_fg=send_fg,
        btn_bg="rgba(255, 255, 255, 0.08)",
        btn_hover="rgba(255, 255, 255, 0.17)",
        btn_fg="#dce5f2",
        btn_border="rgba(255, 255, 255, 0.14)",
        scream_hover_border="rgba(255, 110, 80, 0.55)",
        send_bg=f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {accent1.name()}, stop:1 {accent2.name()})",
        send_hover=f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {send_hover_a}, stop:1 {send_hover_b})",
        send_fg=send_fg,
        corner_radius=corner,
        is_light=False,
    )


def create_cyber_icon(
    size: int = 64,
    scream: bool = False,
    c1: str = "#00f0ff",
    c2: str = "#8338ec",
) -> QIcon:
    """Zeichnet ein prozedurales Cyber-Roboter-Icon für Taskleiste und System-Tray."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    grad = QLinearGradient(0, 0, size, size)
    if scream:
        grad.setColorAt(0.0, QColor("#ff1e56"))
        grad.setColorAt(1.0, QColor("#ff9f1c"))
    else:
        grad.setColorAt(0.0, QColor(c1))
        grad.setColorAt(1.0, QColor(c2))

    margin = size * 0.08
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    p.setBrush(QColor("#0d1322"))
    p.setPen(QPen(grad, max(2.0, size * 0.07)))
    p.drawRoundedRect(rect, size * 0.24, size * 0.24)

    visor_rect = QRectF(size * 0.22, size * 0.30, size * 0.56, size * 0.20)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(visor_rect, size * 0.08, size * 0.08)

    bar_w = size * 0.07
    heights = (0.10, 0.18, 0.12, 0.18, 0.10)
    start_x = size * 0.24
    gap = size * 0.115
    for idx, h_ratio in enumerate(heights):
        bh = size * h_ratio
        bx = start_x + idx * gap
        by = size * 0.66 - bh / 2
        p.drawRoundedRect(QRectF(bx, by, bar_w, bh), bar_w / 2, bar_w / 2)

    p.end()
    return QIcon(pix)


def _format_hotkey_display(hotkey: str) -> str:
    parts = [p.strip() for p in (hotkey or "").split("+") if p.strip()]
    if not parts:
        return "—"
    pretty = {"alt": "Alt", "ctrl": "Strg", "shift": "Umschalt", "win": "Win", "space": "Leertaste"}
    return "+".join(pretty.get(p.lower(), p.upper() if len(p) <= 3 else p.capitalize()) for p in parts)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        _kernel32.CloseHandle(handle)


def _is_pos_on_screen(x: int, y: int, w: int, h: int) -> bool:
    center = QPoint(x + w // 2, y + h // 2)
    for screen in QGuiApplication.screens():
        geo = screen.availableGeometry()
        if geo.contains(center) and y >= geo.y() - 8:
            return True
    return False


def _send_local_socket_restore(server_name: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(800):
        return False
    try:
        sock.write(b'{"cmd": "restore"}\n')
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True
    except Exception:
        return False


class _EngineBridge(QObject):
    """Thread-sichere Brücke zwischen Audio-Worker-Callbacks und dem Qt-UI-Thread."""

    level_received = Signal(float, bool)
    speaking_received = Signal(bool)
    error_received = Signal(str)


class _IpcBridge(QObject):
    """Liefert JSON-Zeilen aus Stdin sofort über Qt-Signal in den UI-Thread."""

    command_received = Signal(dict)


class EqualizerBadge(QWidget):
    """Animierter 5-Balken Equalizer links im Textfeld-Fenster."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(38, 38)
        self.level = 0.0
        self.speaking = False
        self.scream = False
        self.phase = 0.0
        self._bg = QColor("#10192b")
        self._border = QColor("#00f0ff")
        self._bar_top = QColor("#00f0ff")
        self._bar_bottom = QColor("#8338ec")

    def apply_palette(self, palette: ThemePalette) -> None:
        self._bg = QColor(palette.eq_bg)
        self._border = QColor(palette.eq_border)
        self._bar_top = QColor(palette.eq_top)
        self._bar_bottom = QColor(palette.eq_bottom)
        self.update()

    def set_state(self, level: float, speaking: bool, scream: bool, phase: float) -> None:
        if (
            self.level == level
            and self.speaking == speaking
            and self.scream == scream
            and self.phase == phase
        ):
            return
        self.level = level
        self.speaking = speaking
        self.scream = scream
        self.phase = phase
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        p.setBrush(self._bg)
        border_col = QColor(self._border)
        border_col.setAlpha(165 if self.speaking else 85)
        p.setPen(QPen(border_col, 1.3))
        p.drawRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 11, 11)

        grad = QLinearGradient(0, 4, 0, h - 4)
        grad.setColorAt(0.0, self._bar_top)
        grad.setColorAt(1.0, self._bar_bottom)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)

        bar_count = 5
        bar_w = 3.4
        gap = 2.6
        total_w = bar_count * bar_w + (bar_count - 1) * gap
        start_x = (w - total_w) / 2.0

        base_profile = (0.24, 0.42, 0.30, 0.42, 0.24)
        for i in range(bar_count):
            if self.speaking:
                wave = 0.5 + 0.5 * math.sin(self.phase * 4.5 + i * 1.1)
                ratio = min(1.0, 0.18 + self.level * (0.45 + 0.55 * wave))
            else:
                ratio = base_profile[i]
            bh = max(4.0, (h - 14.0) * ratio)
            bx = start_x + i * (bar_w + gap)
            by = (h - bh) / 2.0
            p.drawRoundedRect(QRectF(bx, by, bar_w, bh), 1.7, 1.7)


class _SmokeNoopEngine:
    """Leichtgewichtiger Stub für POPWIN_SMOKE ohne Hardware-/COM-Blockade."""

    def speak(self, _text: str, force_scream: bool = False) -> None:
        return None

    def update_config(self, _cfg: dict | None = None) -> None:
        return None

    def clear_cache(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


class CompactTTSWindow(QWidget):
    """Das rahmenlose, nicht vergrößerbare Always-on-Top Overlay 'TexttoSpeech for Windows'."""

    FIXED_W = 540
    FIXED_H = 74

    def __init__(
        self,
        initial_config: dict | None = None,
        parent_pid: int = 0,
        ipc_stdout: bool = False,
        smoke: bool = False,
    ):
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint,
        )
        self.setWindowTitle(APP_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.FIXED_W, self.FIXED_H)

        self._smoke = smoke
        self._parent_pid = parent_pid
        self._ipc_stdout = ipc_stdout
        self._config: dict[str, object] = dict(DEFAULT_CONFIG)
        self._config["window_pos"] = []
        if initial_config:
            clean_init = {k: v for k, v in initial_config.items() if v is not None}
            self._config.update(_normalize_canonical_config(clean_init))

        self._theme_proxy = _ThemeConfigView(self._config)
        self._theme_mgr = ThemeManager(self._theme_proxy)
        self._theme_mgr.changed.connect(self._invalidate_and_style)

        self._drag_offset: QPoint | None = None
        self._placed_by_user = False
        self._phase = 0.0
        self._speaking = False
        self._audio_level = 0.0
        self._force_scream = False
        self._typing_scream = False
        self._last_topmost: bool | None = None
        self._registered_hotkey = ""
        self._closing = False
        self._backdrop_cache: QPixmap | None = None
        self._backdrop_key: tuple | None = None
        self._palette: ThemePalette | None = None

        self._normal_icon = create_cyber_icon(64, scream=False)
        self._scream_icon = create_cyber_icon(64, scream=True)
        self.setWindowIcon(self._normal_icon)

        # Thread-sichere Audio-Engine-Brücke
        self._engine_bridge = _EngineBridge(self)
        self._engine_bridge.level_received.connect(self._on_level)
        self._engine_bridge.speaking_received.connect(self._on_speaking)
        self._engine_bridge.error_received.connect(self._on_engine_error)

        if self._smoke:
            self._engine = _SmokeNoopEngine()
        else:
            self._engine = RobotVoiceEngine(
                self._engine_config,
                on_level=self._emit_level,
                on_speaking=self._emit_speaking,
                on_error=self._emit_error,
            )

        # IPC-Brücke & LocalServer
        self._ipc_bridge = _IpcBridge(self)
        self._ipc_bridge.command_received.connect(self._handle_ipc_command)
        self._local_server: QLocalServer | None = None

        # Debounce-Timer für Fensterposition (400 ms)
        self._pos_save_timer = QTimer(self, singleShot=True, interval=400)
        self._pos_save_timer.timeout.connect(self._persist_window_pos)

        # Animations-Timer (35 ms) - startet AUSSCHLIESSLICH während _speaking == True
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(35)
        self._anim_timer.timeout.connect(self._tick)

        # Leichter 2-Sekunden-Check nur für Parent-PID & optional Fullscreen-Prüfung
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(2000)
        self._watchdog_timer.timeout.connect(self._watchdog_tick)

        self.tray: QSystemTrayIcon | None = None
        self._act_restore = None
        self._act_min = None
        self._act_passthrough = None
        self._act_force_scream = None
        self._act_quit = None

        self._build_ui()
        self._place_initial()
        self._setup_tray()
        self._apply_theme_and_styles()

        if not self._smoke:
            self._register_global_hotkey(str(self._config.get("hotkey", "alt+y")))
            self._start_local_server()
            self._start_stdin_reader()
            if self._parent_pid > 0 or bool(self._config.get("respect_fullscreen", False)):
                self._watchdog_timer.start()
            if self._parent_pid == 0:
                threading.Thread(
                    target=self._standalone_audio_init,
                    name="tts-standalone-audio-init",
                    daemon=True,
                ).start()

    def _standalone_audio_init(self) -> None:
        try:
            if not self._config.get("preferred_input") and not self._config.get("preferred_output"):
                pref_in, pref_out = capture_default_audio_names()
                self._config["preferred_input"] = str(pref_in or "")
                self._config["preferred_output"] = str(pref_out or "")
        except Exception:
            pass
        try:
            unlock_virtual_microphone()
        except Exception:
            pass

    # --- Engine-Callbacks (Hintergrundthread -> Qt-Signal) -----------------
    def _emit_level(self, level: float, scream: bool | None = None) -> None:
        if not self._closing and shiboken6.isValid(self._engine_bridge):
            self._engine_bridge.level_received.emit(
                float(level),
                bool(self.is_scream_active if scream is None else scream),
            )

    def _emit_speaking(self, speaking: bool) -> None:
        if not self._closing and shiboken6.isValid(self._engine_bridge):
            self._engine_bridge.speaking_received.emit(bool(speaking))

    def _emit_error(self, message: str) -> None:
        if not self._closing and shiboken6.isValid(self._engine_bridge):
            self._engine_bridge.error_received.emit(str(message))

    def _engine_config(self) -> dict[str, object]:
        cfg = dict(self._config)
        rate = int(cfg.get("rate", 0) or 0)
        vvol = int(cfg.get("virtual_volume", 100) or 100)
        cfg["voice_id"] = str(cfg.get("voice", "auto") or "auto")
        cfg["speech_rate"] = rate
        cfg["normal_boost"] = vvol
        cfg["normal_volume"] = vvol
        cfg["mic_volume"] = vvol
        cfg["mic_device"] = cfg.get("input_device", "auto")
        cfg["passthrough_device"] = cfg.get("input_device", "auto")
        cfg["output_device"] = cfg.get("headphone_device", "default")
        return cfg

    # --- UI-Aufbau & Theme-Styling -----------------------------------------
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.eq_badge = EqualizerBadge(self)
        self.eq_badge.setToolTip("TexttoSpeech for Windows — Ziehen zum Verschieben")
        layout.addWidget(self.eq_badge)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Roboter-Text tippen & Enter drücken… (CAPS / ! = Schreien)")
        self.input.setFixedHeight(40)
        font = QFont("Segoe UI", 10)
        font.setWeight(QFont.Weight.Medium)
        font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
        self.input.setFont(font)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self.send_text)
        layout.addWidget(self.input, 1)

        self.scream_btn = QPushButton("📢", self)
        self.scream_btn.setCheckable(True)
        self.scream_btn.setFixedSize(36, 38)
        self.scream_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scream_btn.setToolTip(
            "Schrei-Overdrive dauerhaft an/aus\n"
            "(Tipp: Text in GROSSBUCHSTABEN oder mit '!' schreit automatisch im VC!)"
        )
        self.scream_btn.toggled.connect(self._on_scream_toggled)
        layout.addWidget(self.scream_btn)

        self.send_btn = QPushButton("↵", self)
        self.send_btn.setFixedSize(44, 38)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setToolTip("Absenden & mit Roboter-Stimme sprechen (Enter)")
        self.send_btn.clicked.connect(self.send_text)
        layout.addWidget(self.send_btn)

        self.min_btn = QPushButton("—", self)
        self.min_btn.setFixedSize(32, 38)
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        layout.addWidget(self.min_btn)

        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.activated.connect(self.showMinimized)

        self._retranslate_ui()

    def _update_hotkey_tooltips(self) -> None:
        hk = _format_hotkey_display(str(self._config.get("hotkey", "alt+y")))
        self.min_btn.setToolTip(
            i18n.translate("Minimieren (Wiederherstellen über Taskleiste oder {})").format(hk)
        )
        if self.tray is not None:
            self.tray.setToolTip(
                i18n.translate("TexttoSpeech for Windows ({} zum Ein-/Ausblenden)").format(hk)
            )

    def _apply_theme_and_styles(self) -> None:
        scream = self.is_scream_active
        ui_theme = str(self._config.get("ui_theme", "cyber") or "cyber")
        pop_theme = str(self._config.get("theme", "pop_basic") or "pop_basic")
        mode = str(self._config.get("mode", "dark") or "dark")

        pal = _resolve_palette(ui_theme, self._theme_mgr, pop_theme, mode, scream)
        self._palette = pal

        self.eq_badge.apply_palette(pal)
        self.input.setStyleSheet(
            f"""
            QLineEdit {{
                background: {pal.input_bg};
                color: {pal.input_fg};
                border: 1.5px solid {pal.input_border};
                border-radius: 12px;
                padding: 0 13px;
                selection-background-color: {pal.selection_bg};
                selection-color: {pal.selection_fg};
            }}
            QLineEdit:focus {{
                border: 1.5px solid {pal.input_focus_border};
                background: {pal.input_focus_bg};
            }}
            """
        )
        self.scream_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {pal.btn_bg};
                color: {pal.btn_fg};
                border: 1px solid {pal.btn_border};
                border-radius: 11px;
                font-size: 15px;
            }}
            QPushButton:hover {{
                background: {pal.btn_hover};
                border: 1px solid {pal.scream_hover_border};
            }}
            QPushButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff1e56, stop:1 #ff8800);
                color: #ffffff;
                border: 1px solid #ffbe0b;
            }}
            """
        )
        self.send_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {pal.send_bg};
                color: {pal.send_fg};
                border: none;
                border-radius: 12px;
                font-size: 20px;
                font-weight: 800;
                padding-bottom: 2px;
            }}
            QPushButton:hover {{
                background: {pal.send_hover};
            }}
            QPushButton:pressed {{
                padding-top: 2px;
            }}
            """
        )
        self.min_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {pal.btn_bg};
                color: {pal.btn_fg};
                border: 1px solid {pal.btn_border};
                border-radius: 11px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {pal.btn_hover};
                color: {pal.input_fg};
            }}
            """
        )

        # Fenster- & Tray-Icon aktualisieren
        if ui_theme == "popwin" and not scream:
            icon = create_cyber_icon(64, scream=False, c1=pal.eq_top.name(), c2=pal.eq_bottom.name())
        else:
            icon = self._scream_icon if scream else self._normal_icon
        self.setWindowIcon(icon)
        if self.tray is not None:
            self.tray.setIcon(icon)

        self._backdrop_cache = None
        self.update()

    def _invalidate_and_style(self) -> None:
        if str(self._config.get("ui_theme", "cyber") or "cyber") == "popwin":
            self._apply_theme_and_styles()

    def _get_backdrop_pixmap(self) -> QPixmap:
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pal = self._palette
        key = (
            self.width(),
            self.height(),
            round(dpr, 2),
            str(self._config.get("ui_theme", "cyber")),
            str(self._config.get("theme", "pop_basic")),
            str(self._config.get("mode", "dark")),
            self.is_scream_active,
        )
        if self._backdrop_cache is not None and self._backdrop_key == key:
            return self._backdrop_cache

        w, h = self.width(), self.height()
        pix = QPixmap(round(w * dpr), round(h * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, pal.border_a)
        grad.setColorAt(0.5, pal.border_b)
        grad.setColorAt(1.0, pal.border_c)

        # Sanfter statischer Außen-Glow
        glow_rect = QRectF(3.0, 3.0, w - 6.0, h - 6.0)
        p.setPen(QPen(grad, 5.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setOpacity(0.24 if pal.is_light else 0.30)
        p.drawRoundedRect(glow_rect, pal.corner_radius + 2.0, pal.corner_radius + 2.0)

        # Haupt-Gehäuse
        p.setOpacity(1.0)
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, pal.bg_top)
        bg_grad.setColorAt(1.0, pal.bg_bottom)
        body_rect = QRectF(4.5, 4.5, w - 9.0, h - 9.0)
        p.setBrush(bg_grad)
        p.setPen(QPen(grad, 2.0))
        p.drawRoundedRect(body_rect, pal.corner_radius, pal.corner_radius)
        p.end()

        self._backdrop_cache = pix
        self._backdrop_key = key
        return pix

    # --- Positionierung & Speicher-Debounce ---------------------------------
    def _load_fallback_pos(self) -> list[int] | None:
        try:
            if POS_STATE_FILE.exists():
                data = json.loads(POS_STATE_FILE.read_text(encoding="utf-8"))
                pos = data.get("window_pos") if isinstance(data, dict) else None
                if isinstance(pos, (list, tuple)) and len(pos) == 2:
                    return [int(pos[0]), int(pos[1])]
        except Exception:
            pass
        return None

    def _place_initial(self) -> None:
        pos = self._config.get("window_pos")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 2):
            pos = self._load_fallback_pos()
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
                if _is_pos_on_screen(x, y, self.FIXED_W, self.FIXED_H):
                    self.move(x, y)
                    return
            except (TypeError, ValueError):
                pass
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.FIXED_W) // 2
            y = geo.y() + geo.height() - self.FIXED_H - 78
            self.move(x, y)

    def _emit_stdout_event(self, event: str, payload: dict) -> None:
        if not self._ipc_stdout or self._smoke:
            return
        try:
            sys.stdout.write(json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _persist_window_pos(self) -> None:
        if self._smoke:
            return
        pos = [int(self.x()), int(self.y())]
        self._config["window_pos"] = pos
        if self._ipc_stdout:
            self._emit_stdout_event("window_pos", {"pos": pos})
        else:
            try:
                POS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                POS_STATE_FILE.write_text(json.dumps({"window_pos": pos}), encoding="utf-8")
            except Exception:
                pass

    # --- System-Tray --------------------------------------------------------
    def _setup_tray(self) -> None:
        if self._smoke or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._normal_icon, self)
        menu = QMenu()
        self._act_restore = menu.addAction(i18n.translate("Fenster einblenden"))
        self._act_restore.triggered.connect(self.restore_and_focus)
        self._act_min = menu.addAction(i18n.translate("Minimieren"))
        self._act_min.triggered.connect(self.showMinimized)
        menu.addSeparator()

        self._act_passthrough = menu.addAction(i18n.translate("Echtes Mikrofon durchschleifen"))
        self._act_passthrough.setCheckable(True)
        self._act_passthrough.setChecked(bool(self._config.get("mic_passthrough", False)))
        self._act_passthrough.toggled.connect(self._on_tray_passthrough)

        self._act_force_scream = menu.addAction(i18n.translate("Schrei-Modus erzwingen"))
        self._act_force_scream.setCheckable(True)
        self._act_force_scream.setChecked(self._force_scream)
        self._act_force_scream.toggled.connect(self._on_tray_force_scream)

        menu.addSeparator()
        self._act_quit = menu.addAction(i18n.translate("Beenden"))
        self._act_quit.triggered.connect(self._quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_visible()

    def _on_tray_passthrough(self, checked: bool) -> None:
        self._config["mic_passthrough"] = bool(checked)
        self._engine.update_config(self._engine_config())
        if self._ipc_stdout:
            self._emit_stdout_event("config_patch", {"data": {"mic_passthrough": bool(checked)}})

    def _on_tray_force_scream(self, checked: bool) -> None:
        if self.scream_btn.isChecked() != checked:
            self.scream_btn.setChecked(checked)

    # --- Globaler Hotkey & Topmost ------------------------------------------
    def _unregister_global_hotkey(self) -> None:
        if not self._registered_hotkey:
            return
        try:
            _user32.UnregisterHotKey(int(self.winId()), HOTKEY_ID)
        except Exception:
            pass
        self._registered_hotkey = ""

    def _register_global_hotkey(self, hotkey_str: str) -> None:
        clean = (hotkey_str or "").strip().lower()
        if clean == self._registered_hotkey:
            return
        self._unregister_global_hotkey()
        if not clean:
            return
        parsed = parse_hotkey(clean)
        if parsed is None:
            return
        mods, vk = parsed
        hwnd = int(self.winId())
        try:
            ok = bool(_user32.RegisterHotKey(hwnd, HOTKEY_ID, mods | MOD_NOREPEAT, vk))
            if not ok:
                ok = bool(_user32.RegisterHotKey(hwnd, HOTKEY_ID, mods, vk))
            if ok:
                self._registered_hotkey = clean
        except Exception:
            pass

    def _should_be_topmost(self) -> bool:
        if self._smoke or self.isMinimized() or not self.isVisible():
            return False
        if not bool(self._config.get("respect_fullscreen", False)):
            return True
        hwnd = int(self.winId())
        if self.isActiveWindow() or win_api.foreground_window() == hwnd:
            return True
        try:
            monitor = win_api.window_monitor(hwnd)
            if monitor and win_api.fullscreen_window(monitor):
                return False
        except Exception:
            pass
        return True

    def _sync_topmost(self, force: bool = False) -> None:
        if self._smoke or self._closing or not self.isVisible() or self.isMinimized():
            return
        want = self._should_be_topmost()
        if not force and want == self._last_topmost:
            return
        try:
            _user32.SetWindowPos(
                int(self.winId()),
                HWND_TOPMOST if want else HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
            self._last_topmost = want
        except Exception:
            pass

    def _toggle_visible(self) -> None:
        if self.isMinimized() or not self.isVisible():
            self.restore_and_focus()
        else:
            self.showMinimized()

    def restore_and_focus(self) -> None:
        """Stellt das Fenster aus dem minimierten Zustand wieder her und fokussiert das Textfeld."""
        if self._smoke:
            return
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        try:
            win_api.force_foreground(int(self.winId()))
        except Exception:
            pass
        self._sync_topmost(force=True)
        self.input.setFocus()

    # --- IPC & Watchdog -----------------------------------------------------
    def _start_local_server(self) -> None:
        QLocalServer.removeServer(IPC_SERVER_NAME)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_local_connection)
        if server.listen(IPC_SERVER_NAME):
            self._local_server = server

    def _on_local_connection(self) -> None:
        if self._local_server is None:
            return
        while self._local_server.hasPendingConnections():
            sock = self._local_server.nextPendingConnection()
            if sock is None:
                continue
            sock.readyRead.connect(lambda s=sock: self._read_local_socket(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read_local_socket(self, sock: QLocalSocket) -> None:
        raw = bytes(sock.readAll()).decode("utf-8", errors="ignore")
        for line in raw.splitlines():
            clean = line.strip()
            if not clean:
                continue
            if clean == "restore":
                self._handle_ipc_command({"cmd": "restore"})
                continue
            try:
                payload = json.loads(clean)
                if isinstance(payload, dict):
                    self._handle_ipc_command(payload)
            except ValueError:
                pass

    def _start_stdin_reader(self) -> None:
        if sys.stdin is None or getattr(sys.stdin, "closed", True):
            return

        def _reader() -> None:
            try:
                for raw_line in sys.stdin:
                    if self._closing:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and not self._closing and shiboken6.isValid(self._ipc_bridge):
                        self._ipc_bridge.command_received.emit(payload)
            except Exception:
                pass

        threading.Thread(target=_reader, name="tts-stdin-ipc", daemon=True).start()

    def _handle_ipc_command(self, payload: dict) -> None:
        cmd = str(payload.get("cmd", "")).strip().lower()
        if cmd == "config":
            data = payload.get("data")
            if isinstance(data, dict):
                self._apply_config_update(data)
        elif cmd == "restore":
            self.restore_and_focus()
        elif cmd == "minimize":
            self.showMinimized()
        elif cmd == "test_voice":
            phrase = i18n.translate("TexttoSpeech for Windows ist bereit!")
            self._engine.speak(phrase, force_scream=False)
        elif cmd == "unlock_mic":
            if not self._smoke:
                threading.Thread(target=unlock_virtual_microphone, daemon=True).start()
        elif cmd == "clear_cache":
            self._engine.clear_cache()
        elif cmd == "quit":
            self._quit_app()

    def _apply_config_update(self, data: dict) -> None:
        old_lang = str(self._config.get("language", "auto"))
        old_hotkey = str(self._config.get("hotkey", "alt+y"))
        old_theme_key = (
            self._config.get("ui_theme"),
            self._config.get("theme"),
            self._config.get("mode"),
        )
        old_respect_fs = bool(self._config.get("respect_fullscreen", False))

        normalized = _normalize_canonical_config(data, self._config)
        for key, value in normalized.items():
            if key == "window_pos" and self._placed_by_user:
                continue
            self._config[key] = value

        # Falls window_pos per initialem Config-Push kommt und Nutzer noch nicht verschoben hat
        if "window_pos" in data and not self._placed_by_user and self._drag_offset is None:
            pos = data["window_pos"]
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                try:
                    x, y = int(pos[0]), int(pos[1])
                    if _is_pos_on_screen(x, y, self.FIXED_W, self.FIXED_H):
                        self.move(x, y)
                except (TypeError, ValueError):
                    pass

        if self._act_passthrough is not None:
            want_pass = bool(self._config.get("mic_passthrough", False))
            if self._act_passthrough.isChecked() != want_pass:
                self._act_passthrough.blockSignals(True)
                self._act_passthrough.setChecked(want_pass)
                self._act_passthrough.blockSignals(False)

        new_hotkey = str(self._config.get("hotkey", "alt+y"))
        if new_hotkey != old_hotkey:
            if not self._smoke:
                self._register_global_hotkey(new_hotkey)
            self._update_hotkey_tooltips()

        new_lang = str(self._config.get("language", "auto"))
        if new_lang != old_lang:
            app = QApplication.instance()
            if app is not None:
                i18n.install(app, new_lang)
                self._retranslate_ui()

        new_theme_key = (
            self._config.get("ui_theme"),
            self._config.get("theme"),
            self._config.get("mode"),
        )
        if new_theme_key != old_theme_key:
            self._apply_theme_and_styles()

        new_respect_fs = bool(self._config.get("respect_fullscreen", False))
        if new_respect_fs != old_respect_fs:
            if not self._smoke:
                if new_respect_fs or self._parent_pid > 0:
                    if not self._watchdog_timer.isActive():
                        self._watchdog_timer.start()
                elif self._watchdog_timer.isActive():
                    self._watchdog_timer.stop()
            self._sync_topmost(force=True)

        # Schrei-Status für laufendes Textfeld nachziehen
        self._on_text_changed(self.input.text())
        self._engine.update_config(self._engine_config())

    def _retranslate_ui(self) -> None:
        self.input.setPlaceholderText(i18n.translate("Roboter-Text tippen & Enter drücken… (CAPS / ! = Schreien)"))
        self.eq_badge.setToolTip(i18n.translate("TexttoSpeech for Windows — Ziehen zum Verschieben"))
        self.scream_btn.setToolTip(
            i18n.translate(
                "Schrei-Overdrive dauerhaft an/aus\n"
                "(Tipp: Text in GROSSBUCHSTABEN oder mit '!' schreit automatisch im VC!)"
            )
        )
        self.send_btn.setToolTip(i18n.translate("Absenden & mit Roboter-Stimme sprechen (Enter)"))
        if self._act_restore is not None:
            self._act_restore.setText(i18n.translate("Fenster einblenden"))
        if self._act_min is not None:
            self._act_min.setText(i18n.translate("Minimieren"))
        if self._act_passthrough is not None:
            self._act_passthrough.setText(i18n.translate("Echtes Mikrofon durchschleifen"))
        if self._act_force_scream is not None:
            self._act_force_scream.setText(i18n.translate("Schrei-Modus erzwingen"))
        if self._act_quit is not None:
            self._act_quit.setText(i18n.translate("Beenden"))
        self._update_hotkey_tooltips()

    def _watchdog_tick(self) -> None:
        if self._closing:
            return
        if self._parent_pid > 0 and not _is_pid_alive(self._parent_pid):
            self._quit_app()
            return
        if bool(self._config.get("respect_fullscreen", False)):
            self._sync_topmost(force=False)

    # --- Sprach- & Animationslogik ------------------------------------------
    @property
    def is_scream_active(self) -> bool:
        return self._force_scream or self._typing_scream

    def _sync_anim_timer(self) -> None:
        should_run = (
            not self._smoke
            and not self._closing
            and self._speaking
            and self.isVisible()
            and not self.isMinimized()
        )
        if should_run and not self._anim_timer.isActive():
            self._anim_timer.start()
        elif not should_run and self._anim_timer.isActive():
            self._anim_timer.stop()

    def _on_text_changed(self, text: str) -> None:
        auto_scream = bool(self._config.get("scream_auto", True))
        new_scream = is_scream_text(text, False) if auto_scream else False
        if new_scream != self._typing_scream:
            self._typing_scream = new_scream
            self._apply_theme_and_styles()
            self.eq_badge.set_state(self._audio_level, self._speaking, self.is_scream_active, self._phase)

    def _on_scream_toggled(self, checked: bool) -> None:
        self._force_scream = checked
        if self._act_force_scream is not None and self._act_force_scream.isChecked() != checked:
            self._act_force_scream.blockSignals(True)
            self._act_force_scream.setChecked(checked)
            self._act_force_scream.blockSignals(False)
        self._apply_theme_and_styles()
        self.eq_badge.set_state(self._audio_level, self._speaking, self.is_scream_active, self._phase)
        self.input.setFocus()

    def _on_level(self, level: float, scream: bool) -> None:
        self._audio_level = level
        if scream != self._typing_scream and not self._force_scream:
            self._typing_scream = scream
            self._apply_theme_and_styles()

    def _on_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        if not speaking:
            self._audio_level = 0.0
            if not self.input.text():
                self._typing_scream = False
                self._apply_theme_and_styles()
            self.eq_badge.set_state(0.0, False, self.is_scream_active, self._phase)
            self.update()
        self._sync_anim_timer()

    def _on_engine_error(self, message: str) -> None:
        log.warning("TTS-Audiofehler: %s", message)
        if message:
            self.eq_badge.setToolTip(message)

    def _tick(self) -> None:
        if not self._speaking or not self.isVisible() or self.isMinimized():
            self._sync_anim_timer()
            return
        self._phase += 0.14
        self.eq_badge.set_state(self._audio_level, self._speaking, self.is_scream_active, self._phase)
        self.update()

    def send_text(self) -> None:
        """Liest den Text, leert das Textfeld sofort und spricht ihn über RobotVoiceEngine aus."""
        text = self.input.text().strip()
        if not text:
            return
        force_scream = self._force_scream
        self.input.clear()
        self._engine.speak(text, force_scream=force_scream)

    # --- Qt-Events (Malen, Ziehen, Maximierschutz, Topmost) -----------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.drawPixmap(0, 0, self._get_backdrop_pixmap())

        # Nur beim aktiven Sprechen leicht pulsierender Rand-Impuls
        if self._speaking and self._palette is not None:
            pal = self._palette
            pulse = 0.25 + 0.35 * (0.5 + 0.5 * math.sin(self._phase * 1.4)) + min(0.35, self._audio_level * 0.35)
            p.setOpacity(min(0.85, pulse))
            p.setPen(QPen(pal.glow_color, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(3.5, 3.5, self.width() - 7.0, self.height() - 7.0), pal.corner_radius + 1.0, pal.corner_radius + 1.0)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._placed_by_user = True
            self._pos_save_timer.start()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        # Doppelklick darf NIEMALS maximieren
        event.accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_anim_timer()
        if not self._smoke:
            QTimer.singleShot(0, lambda: self._sync_topmost(force=True))

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_anim_timer()

    def changeEvent(self, event) -> None:
        if self.windowState() & Qt.WindowState.WindowMaximized:
            self.setWindowState(Qt.WindowState.WindowNoState)
            self.setFixedSize(self.FIXED_W, self.FIXED_H)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_anim_timer()
            if not self.isMinimized() and not self._smoke:
                QTimer.singleShot(25, lambda: self._sync_topmost(force=True))
        super().changeEvent(event)

    def event(self, ev) -> bool:
        ev_type = ev.type()
        if ev_type in (QEvent.Type.WindowDeactivate, QEvent.Type.WindowActivate) and not self._smoke:
            QTimer.singleShot(0, lambda: self._sync_topmost(force=True))
        return super().event(ev)

    def nativeEvent(self, eventType, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_SYSCOMMAND and (msg.wParam & 0xFFF0) == SC_MAXIMIZE:
                return True, 0
            if msg.message == WM_NCLBUTTONDBLCLK:
                return True, 0
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self._toggle_visible()
                return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _quit_app(self) -> None:
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def cleanup(self) -> None:
        """Beendet Timer, Hotkey, Tray, IPC und Audio-Engine idempotent."""
        if self._closing:
            return
        self._closing = True
        self._anim_timer.stop()
        self._watchdog_timer.stop()
        if self._pos_save_timer.isActive():
            self._pos_save_timer.stop()
            self._persist_window_pos()

        self._unregister_global_hotkey()
        if self._local_server is not None:
            try:
                self._local_server.close()
            except Exception:
                pass
            self._local_server = None

        if self.tray is not None:
            try:
                self.tray.hide()
            except Exception:
                pass

        try:
            self._engine.shutdown()
        except Exception:
            pass

        if not self._smoke:
            try:
                restore_normal_audio(
                    mute_virtual_speaker=True,
                    preferred_input=str(self._config.get("preferred_input", "") or ""),
                    preferred_output=str(self._config.get("preferred_output", "") or ""),
                )
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        self.cleanup()
        super().closeEvent(event)
        QTimer.singleShot(0, lambda: QApplication.instance() and QApplication.instance().quit())


def _load_initial_config() -> dict:
    """Liest die PopWin-Konfiguration einmalig beim Start ohne spätere Disk-Polls."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["window_pos"] = list(DEFAULT_CONFIG["window_pos"]) if isinstance(DEFAULT_CONFIG.get("window_pos"), list) else []
    try:
        cm = ConfigManager()
        ext = cm.get("extensions.tts_for_windows")
        if isinstance(ext, dict):
            cfg.update(_normalize_canonical_config(ext))
        cfg["theme"] = cm.get("theme", "pop_basic")
        cfg["mode"] = cm.get("mode", "dark")
        cfg["language"] = cm.get("language", "auto")
    except Exception:
        pass
    # Sicherstellen, dass robot_style gültig ist
    if cfg.get("robot_style") not in STYLES:
        cfg["robot_style"] = "cyber"
    return cfg


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--ipc-stdout", action="store_true")
    parser.add_argument("--standalone", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    return args


def main() -> int:
    """Startet das eigenständige Always-on-Top Fenster 'TexttoSpeech for Windows'."""
    smoke = bool(os.environ.get("POPWIN_SMOKE"))
    args = _parse_args(sys.argv[1:])

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)
    except Exception:
        pass

    mutex = None
    if not smoke:
        mutex = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if _kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            # Zweite Instanz: laufende Instanz über QLocalSocket einblenden und sofort beenden
            app = QApplication.instance() or QApplication(sys.argv)
            _send_local_socket_restore(IPC_SERVER_NAME)
            if mutex:
                _kernel32.CloseHandle(mutex)
            return 0

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    font = app.font()
    font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
    app.setFont(font)

    initial_cfg = _load_initial_config()
    i18n.install(app, str(initial_cfg.get("language", "auto")))

    window = CompactTTSWindow(
        initial_config=initial_cfg,
        parent_pid=args.parent_pid,
        ipc_stdout=args.ipc_stdout,
        smoke=smoke,
    )

    if smoke:
        QTimer.singleShot(1500, lambda: (window.close(), app.quit()))
        code = app.exec()
        window.cleanup()
        return code

    window.show()
    window.restore_and_focus()
    code = app.exec()
    window.cleanup()
    if mutex:
        _kernel32.CloseHandle(mutex)
    return code


if __name__ == "__main__":
    sys.exit(main())
# --- [Antigravity Ende] ---
