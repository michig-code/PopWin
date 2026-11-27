# --- [Antigravity Start] TexttoSpeech for Windows Erweiterung (PopWin-Wrapper) ---
"""Erweiterung TexttoSpeech for Windows: Ultraleichter PopWin-Wrapper für das Always-on-Top TTS-Overlay."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

try:
    from core.paths import FROZEN, ROOT, app_command, pythonw_path
    from extensions import Extension
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.paths import FROZEN, ROOT, app_command, pythonw_path
    from extensions import Extension

APP_TITLE = "TexttoSpeech for Windows"
IPC_SERVER_NAME = "PopWin.TTSForWindows.IPC"

TTS_DEFAULTS: dict[str, object] = {
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
}

_LEGACY_KEY_ALIASES: dict[str, str] = {
    "voice": "voice_id",
    "rate": "speech_rate",
    "virtual_volume": "normal_mic_volume",
    "input_device": "mic_device",
}

_STYLE_LABELS: dict[str, str] = {
    "cyber": "Cyber-Roboter (Ausgewogen & Sci-Fi)",
    "droid": "Kampf-Droid (Starker Metall- & Ring-Effekt)",
    "classic": "Klassisch TTS (Klare Computer-Stimme)",
    "clean": "Klar ohne Roboter-Effekt",
}

_RISKY_MIC_TAGS = (
    "steam streaming",
    "cable output",
    "voicemeeter",
    "hands-free",
    "telephony",
    "ag audio",
)


def _safe_audio_devices(fetcher) -> list[tuple[str, str]]:
    """Liest Audiogeräte robust als [(Anzeigename, voller_Name)], ohne in Tests abzustürzen."""
    try:
        items = list(fetcher() or [])
    except Exception:
        return []

    raw_entries: list[tuple[str, str]] = []
    short_counts: dict[str, int] = {}
    for dev in items:
        if isinstance(dev, str):
            short = full = dev
        else:
            full = str(getattr(dev, "full_name", "") or "")
            short = str(getattr(dev, "name", "") or "")
            if not short:
                short = full
            if not full:
                full = short
        if not full:
            continue
        raw_entries.append((short, full))
        short_counts[short] = short_counts.get(short, 0) + 1

    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for short, full in raw_entries:
        if full in seen:
            continue
        seen.add(full)
        label = full if short_counts.get(short, 0) > 1 else short
        result.append((label, full))
    return result


def _is_risky_input_name(value: str) -> bool:
    lower = (value or "").lower()
    return any(tag in lower for tag in _RISKY_MIC_TAGS)


def _send_local_socket_restore() -> bool:
    try:
        from PySide6.QtNetwork import QLocalSocket

        sock = QLocalSocket()
        sock.connectToServer(IPC_SERVER_NAME)
        if not sock.waitForConnected(500):
            return False
        sock.write(b'{"cmd": "restore"}\n')
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    except Exception:
        return False


class TextToSpeechExtension(Extension):
    """PopWin-Erweiterung: TexttoSpeech for Windows."""

    key = "tts_for_windows"
    title = APP_TITLE
    description = (
        "Öffnet ein kompaktes, rahmenloses Always-on-Top Textfeld. Eingegebener Text verschwindet beim "
        "Absenden (Enter / ↵) sofort und wird per Roboter-Stimme über Kopfhörer und virtuelles Mikrofon "
        "ausgegeben – inklusive automatischem Schrei-Overdrive bei GROSSBUCHSTABEN oder '!'."
    )
    AUTO_START = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self.process: subprocess.Popen | None = None
        self._pipe_lock = threading.Lock()
        self._has_run = False
        self._preferred_input = ""
        self._preferred_output = ""
        self._stdout_bridge = None

        config = getattr(ctx, "config", None)
        changed = getattr(config, "changed", None)
        if changed is not None:
            changed.connect(self._on_config_changed)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def option(self, name: str, default=None):
        fallback = TTS_DEFAULTS.get(name) if default is None else default
        val = self.ctx.config.get(f"extensions.tts_for_windows.{name}")
        if val is None and name in _LEGACY_KEY_ALIASES:
            val = self.ctx.config.get(f"extensions.tts_for_windows.{_LEGACY_KEY_ALIASES[name]}")
        if val is None:
            return list(fallback) if isinstance(fallback, list) else fallback
        return val

    def set_option(self, name: str, value) -> None:
        self.ctx.config.set(f"extensions.tts_for_windows.{name}", value)

    def _build_config_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            name: self.option(name, default) for name, default in TTS_DEFAULTS.items()
        }
        payload["preferred_input"] = self._preferred_input
        payload["preferred_output"] = self._preferred_output
        payload["theme"] = self.ctx.config.get("theme", "pop_basic")
        payload["mode"] = self.ctx.config.get("mode", "dark")
        payload["language"] = self.ctx.config.get("language", "auto")
        return payload

    def _send_command(self, cmd: str, data: dict | None = None) -> bool:
        proc = self.process
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False
        msg: dict[str, object] = {"cmd": cmd}
        if data is not None:
            msg["data"] = data
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._pipe_lock:
            try:
                if proc.stdin.closed:
                    return False
                proc.stdin.write(line)
                proc.stdin.flush()
                return True
            except Exception:
                return False

    def _on_config_changed(self, key: str) -> None:
        if not self.running:
            return
        if key.startswith("extensions.tts_for_windows.") or key in ("theme", "mode", "language"):
            self._send_command("config", self._build_config_payload())

    def _ensure_stdout_bridge(self):
        if self._stdout_bridge is not None:
            return self._stdout_bridge
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            event_received = Signal(dict)

        bridge = _Bridge()
        bridge.event_received.connect(self._on_subprocess_event)
        self._stdout_bridge = bridge
        return bridge

    def _on_subprocess_event(self, payload: dict) -> None:
        ev = payload.get("event")
        if ev == "window_pos":
            pos = payload.get("pos")
            if isinstance(pos, list) and len(pos) == 2:
                self.ctx.config.set("extensions.tts_for_windows.window_pos", pos, notify=False)
        elif ev == "config_patch":
            data = payload.get("data")
            if isinstance(data, dict):
                for key, val in data.items():
                    self.ctx.config.set(f"extensions.tts_for_windows.{key}", val, notify=False)

    def _start_stdout_reader(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        bridge = self._ensure_stdout_bridge()

        def _reader() -> None:
            try:
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        bridge.event_received.emit(payload)
            except Exception:
                pass

        threading.Thread(target=_reader, name="tts-ext-stdout", daemon=True).start()

    def _launch_command(self) -> list[str]:
        pid_args = ["--parent-pid", str(os.getpid()), "--ipc-stdout"]
        if FROZEN:
            return app_command("tts_for_windows", *pid_args)
        app_script = ROOT / "tts" / "app.py"
        if app_script.is_file():
            return [pythonw_path(), str(app_script), *pid_args]
        return [pythonw_path(), str(Path(__file__).resolve()), "--standalone", *pid_args]

    def _capture_defaults_once(self) -> None:
        if self._preferred_input or self._preferred_output or os.environ.get("POPWIN_SMOKE"):
            return
        try:
            from tts.audio import capture_default_audio_names

            pref_in, pref_out = capture_default_audio_names()
            self._preferred_input = str(pref_in or "")
            self._preferred_output = str(pref_out or "")
        except Exception:
            pass

    def start(self) -> None:
        self._has_run = True
        if self.running:
            if not self._send_command("restore"):
                _send_local_socket_restore()
            return

        self._capture_defaults_once()
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self._launch_command(),
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
        self._start_stdout_reader(self.process)
        self._send_command("config", self._build_config_payload())

        if not os.environ.get("POPWIN_SMOKE"):
            def _unlock() -> None:
                try:
                    from tts.audio import unlock_virtual_microphone

                    unlock_virtual_microphone()
                except Exception:
                    pass

            threading.Thread(target=_unlock, name="tts-unlock-mic", daemon=True).start()

    def stop(self) -> None:
        process, self.process = self.process, None
        was_active = self._has_run or process is not None
        self._has_run = False

        if process is not None and process.poll() is None:
            with self._pipe_lock:
                try:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.write(json.dumps({"cmd": "quit"}, ensure_ascii=False) + "\n")
                        process.stdin.flush()
                        process.stdin.close()
                except Exception:
                    pass
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except Exception:
                    pass

        if was_active and not os.environ.get("POPWIN_SMOKE"):
            try:
                from tts.audio import restore_normal_audio

                restore_normal_audio(
                    mute_virtual_speaker=True,
                    preferred_input=self._preferred_input,
                    preferred_output=self._preferred_output,
                )
            except Exception:
                pass

    def settings(self, form, _window) -> None:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QSlider,
            QWidget,
        )
        from core import i18n, system_status
        from core.shell_hooks import parse_hotkey
        from ui.base import resettable

        try:
            from tts.audio import STYLES, find_virtual_output_device, list_available_voices
        except Exception:
            STYLES = ("cyber", "droid", "classic", "clean")
            find_virtual_output_device = None
            list_available_voices = None

        def _make_combo(opt_name: str, items: list[tuple[str, object]], default_val: object) -> QComboBox:
            combo = QComboBox()
            for label, value in items:
                combo.addItem(label, value)
            current = self.option(opt_name, default_val)
            if opt_name == "voice" and current == "":
                current = "auto"
            idx = combo.findData(current)
            if idx < 0 and isinstance(current, str) and current:
                combo.addItem(current, current)
                idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))
            combo.currentIndexChanged.connect(lambda _i, c=combo, k=opt_name: self.set_option(k, c.currentData()))
            return combo

        def _reset_combo(combo: QComboBox, opt_name: str, default_val: object) -> None:
            idx = max(0, combo.findData(default_val))
            combo.setCurrentIndex(idx)
            self.set_option(opt_name, combo.currentData())

        def _make_debounced_slider(
            opt_name: str,
            low: int,
            high: int,
            default_val: int,
            formatter,
        ) -> QWidget:
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            slider = QSlider(Qt.Orientation.Horizontal, host)
            slider.setRange(low, high)
            current = int(self.option(opt_name, default_val))
            current = max(low, min(high, current))
            slider.setValue(current)

            val_lbl = QLabel(formatter(current), host, objectName="Muted")
            val_lbl.setMinimumWidth(44)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            timer = QTimer(host)
            timer.setSingleShot(True)
            timer.setInterval(150)
            timer.timeout.connect(lambda s=slider, k=opt_name: self.set_option(k, int(s.value())))

            def on_changed(val: int) -> None:
                val_lbl.setText(formatter(val))
                timer.start()

            slider.valueChanged.connect(on_changed)
            row.addWidget(slider, 1)
            row.addWidget(val_lbl)

            def do_reset() -> None:
                timer.stop()
                slider.setValue(default_val)
                val_lbl.setText(formatter(default_val))
                self.set_option(opt_name, default_val)

            return resettable(host, do_reset)

        # 1. Stimme
        voice_items: list[tuple[str, object]] = []
        seen_auto = False
        if list_available_voices is not None:
            try:
                for entry in list_available_voices() or []:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        vid, vlabel = str(entry[0]), str(entry[1])
                    else:
                        vid = vlabel = str(entry)
                    if vid == "auto":
                        seen_auto = True
                    if vid:
                        voice_items.append((vlabel or vid, vid))
            except Exception:
                pass
        if not seen_auto:
            voice_items.insert(0, ("Automatisch (Windows Standard)", "auto"))
        voice_combo = _make_combo("voice", voice_items, "auto")
        form.addRow("Stimme", resettable(voice_combo, lambda: _reset_combo(voice_combo, "voice", "auto")))

        # 2. Roboter-Stil
        style_items = [(_STYLE_LABELS.get(s, s.capitalize()), s) for s in STYLES]
        style_combo = _make_combo("robot_style", style_items, "cyber")
        form.addRow("Roboter-Stil", resettable(style_combo, lambda: _reset_combo(style_combo, "robot_style", "cyber")))

        # 3. Sprechtempo (-4 bis +4)
        form.addRow(
            "Sprechtempo",
            _make_debounced_slider(
                "rate",
                -4,
                4,
                0,
                lambda v: f"{v:+d}" if v != 0 else "0",
            ),
        )

        # 4. Fenster-Design
        ui_theme_combo = _make_combo(
            "ui_theme",
            [("Cyber-Neon (Leuchtend)", "cyber"), ("Wie PopWin-Theme", "popwin")],
            "cyber",
        )
        form.addRow(
            "Fenster-Design",
            resettable(ui_theme_combo, lambda: _reset_combo(ui_theme_combo, "ui_theme", "cyber")),
        )

        # 5. Tastenkürzel (Ein-/Ausblenden)
        hotkey_edit = QLineEdit(str(self.option("hotkey", "alt+y")))
        hotkey_edit.setPlaceholderText("z. B. alt+y")

        def reset_hotkey() -> None:
            hotkey_edit.setText("alt+y")
            self.set_option("hotkey", "alt+y")

        form.addRow("Tastenkürzel (Ein-/Ausblenden)", resettable(hotkey_edit, reset_hotkey))

        # 6. Schreien bei GROSSBUCHSTABEN oder '!'
        scream_box = QCheckBox("Schreien bei GROSSBUCHSTABEN oder '!'")
        scream_box.setChecked(bool(self.option("scream_auto", True)))
        scream_box.toggled.connect(lambda on: self.set_option("scream_auto", bool(on)))
        form.addRow(scream_box)

        # 7. Schrei-Boost (Virtuelles Mic)
        form.addRow(
            "Schrei-Boost (Virtuelles Mic)",
            _make_debounced_slider("scream_boost", 120, 400, 280, lambda v: f"{v} %"),
        )

        # 8. Normale Lautstärke (Virtuelles Mic)
        form.addRow(
            "Normale Lautstärke (Virtuelles Mic)",
            _make_debounced_slider("virtual_volume", 60, 140, 100, lambda v: f"{v} %"),
        )

        # Audiogeräte einmalig für Dropdowns abfragen
        out_devices = _safe_audio_devices(system_status.audio_outputs)
        in_devices = _safe_audio_devices(system_status.audio_inputs)

        # 9. Kopfhörer-Ausgang (Mithören)
        hp_items: list[tuple[str, object]] = [
            ("Standard-Ausgabegerät", "default"),
            ("Aus (nur Virtuelles Mic)", "off"),
            *out_devices,
        ]
        hp_dev_combo = _make_combo("headphone_device", hp_items, "default")
        form.addRow(
            "Kopfhörer-Ausgang (Mithören)",
            resettable(hp_dev_combo, lambda: _reset_combo(hp_dev_combo, "headphone_device", "default")),
        )

        # 10. Kopfhörer-Lautstärke (0-100 %)
        form.addRow(
            "Kopfhörer-Lautstärke",
            _make_debounced_slider("headphone_volume", 0, 100, 85, lambda v: f"{v} %"),
        )

        # 11. Virtuelles Mikrofon-Kabel
        vm_items: list[tuple[str, object]] = [
            ("Automatisch erkennen", "auto"),
            ("Aus", "off"),
            *out_devices,
        ]
        vm_dev_combo = _make_combo("virtual_device", vm_items, "auto")
        form.addRow(
            "Virtuelles Mikrofon-Kabel",
            resettable(vm_dev_combo, lambda: _reset_combo(vm_dev_combo, "virtual_device", "auto")),
        )

        # 12. Echtes Mikrofon durchschleifen + Eingabe-Mikrofon
        pass_box = QCheckBox("Echtes Mikrofon live in das virtuelle Mikrofon durchschleifen")
        pass_box.setChecked(bool(self.option("mic_passthrough", False)))
        pass_box.toggled.connect(lambda on: self.set_option("mic_passthrough", bool(on)))
        form.addRow(pass_box)

        in_items: list[tuple[str, object]] = [
            ("Automatisch (ohne Feedback)", "auto"),
            *in_devices,
        ]
        in_dev_combo = _make_combo("input_device", in_items, "auto")
        form.addRow(
            "Eingabe-Mikrofon",
            resettable(in_dev_combo, lambda: _reset_combo(in_dev_combo, "input_device", "auto")),
        )

        feedback_warn = QLabel(
            "Hinweis: Ein virtuelles oder Hands-Free-Mikrofon als Eingang kann Rückkopplung oder "
            "den dumpfen Bluetooth-Anrufmodus auslösen.",
            wordWrap=True,
        )
        feedback_warn.setStyleSheet("color: #ff9f1c; font-size: 12px;")
        feedback_warn.setVisible(_is_risky_input_name(str(in_dev_combo.currentData() or "")))
        in_dev_combo.currentIndexChanged.connect(
            lambda _i: feedback_warn.setVisible(_is_risky_input_name(str(in_dev_combo.currentData() or "")))
        )
        form.addRow(feedback_warn)

        # 13. Bei Vollbildspielen im Hintergrund bleiben
        fs_box = QCheckBox("Bei Vollbildspielen im Hintergrund bleiben")
        fs_box.setChecked(bool(self.option("respect_fullscreen", False)))
        fs_box.toggled.connect(lambda on: self.set_option("respect_fullscreen", bool(on)))
        form.addRow(fs_box)

        # 14. Status-Anzeige & Aktions-Knöpfe
        vm_name = "Nicht gefunden"
        if find_virtual_output_device is not None:
            try:
                _idx, detected = find_virtual_output_device(str(self.option("virtual_device", "auto")))
                if detected:
                    vm_name = str(detected)
            except Exception:
                pass

        status_lbl = QLabel(
            i18n.translate("Erkanntes virtuelles Mikrofon-Kabel: {}").format(i18n.translate(vm_name)),
            objectName="Muted",
            wordWrap=True,
        )
        tip_lbl = QLabel(
            "Tipp für Discord / Spiele: Wähle als Eingabegerät „Mikrofon (Steam Streaming Microphone)“ "
            "oder „CABLE Output“ aus.",
            objectName="Muted",
            wordWrap=True,
        )
        form.addRow(status_lbl)
        form.addRow(tip_lbl)

        def commit_hotkey() -> None:
            raw = hotkey_edit.text().strip().lower()
            if raw and parse_hotkey(raw) is None:
                status_lbl.setText(i18n.translate("Ungültiges Tastenkürzel (z. B. alt+y oder ctrl+shift+t)."))
                hotkey_edit.setText(str(self.option("hotkey", "alt+y")))
                return
            self.set_option("hotkey", raw)
            status_lbl.setText(
                i18n.translate("Erkanntes virtuelles Mikrofon-Kabel: {}").format(i18n.translate(vm_name))
            )

        hotkey_edit.editingFinished.connect(commit_hotkey)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 4, 0, 0)
        row1.setSpacing(6)
        btn_focus = QPushButton("Fenster einblenden / starten", objectName="Primary")
        btn_test = QPushButton("Test-Sprache abspielen", objectName="Secondary")
        row1.addWidget(btn_focus, 1)
        row1.addWidget(btn_test, 1)
        form.addRow(row1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        btn_unlock = QPushButton("Virtuelles Mikrofon entsperren", objectName="Secondary")
        btn_restore = QPushButton("Normalen Audio-Modus wiederherstellen", objectName="Secondary")
        row2.addWidget(btn_unlock, 1)
        row2.addWidget(btn_restore, 1)
        form.addRow(row2)

        def on_focus_clicked() -> None:
            if not self.running:
                self.start()
            else:
                if not self._send_command("restore"):
                    _send_local_socket_restore()

        def on_test_clicked() -> None:
            if not self.running:
                self.start()
            self._send_command("test_voice")

        def on_unlock_clicked() -> None:
            try:
                from tts.audio import unlock_virtual_microphone

                _ok, msg = unlock_virtual_microphone()
                status_lbl.setText(str(msg))
            except Exception as exc:
                status_lbl.setText(i18n.translate("Fehler beim Entsperren: {}").format(exc))
            if self.running:
                self._send_command("unlock_mic")

        def on_restore_clicked() -> None:
            try:
                from tts.audio import restore_normal_audio

                _ok, msg = restore_normal_audio(
                    mute_virtual_speaker=not self.running,
                    preferred_input=self._preferred_input,
                    preferred_output=self._preferred_output,
                )
                status_lbl.setText(str(msg))
            except Exception as exc:
                status_lbl.setText(i18n.translate("Audio-Reset Fehler: {}").format(exc))

        btn_focus.clicked.connect(on_focus_clicked)
        btn_test.clicked.connect(on_test_clicked)
        btn_unlock.clicked.connect(on_unlock_clicked)
        btn_restore.clicked.connect(on_restore_clicked)


EXTENSION = TextToSpeechExtension


def main() -> int:
    """Einstiegspunkt für den frozen App-Start (--app tts_for_windows) oder direkten Fallback."""
    from tts.app import main as _run_tts_app

    return _run_tts_app()


if __name__ == "__main__":
    sys.exit(main())
# --- [Antigravity Ende] ---
