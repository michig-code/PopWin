"""Lautstärke pro App wie im Windows-Mixer. Mehrere Fenster einer App zählen als eine Zeile."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

SESSION_EXPIRED = 2
SYSTEM_KEY = "system"
SYSTEM_NAME = "Systemsounds"
COM_ERRORS = (OSError, ValueError)


def app_name(process_name: str) -> str:
    stem = os.path.splitext(process_name)[0]
    return stem[:1].upper() + stem[1:]


@dataclass
class AppAudio:
    """Bis zur nächsten Liste gehalten: Der Pegel wird zehnmal pro Sekunde gelesen und darf nichts neu suchen."""
    key: str
    name: str
    exe: str = ""
    controls: list = field(default_factory=list)
    meters: list = field(default_factory=list)

    def volume(self) -> int:
        try:
            return round(self.controls[0].GetMasterVolume() * 100)
        except COM_ERRORS:
            return 0

    def set_volume(self, percent: int) -> None:
        self._each(lambda control: control.SetMasterVolume(max(0, min(100, percent)) / 100, None))

    def muted(self) -> bool:
        try:
            return bool(self.controls[0].GetMute())
        except COM_ERRORS:
            return False

    def set_muted(self, muted: bool) -> None:
        self._each(lambda control: control.SetMute(int(muted), None))

    def _each(self, change) -> None:
        for control in self.controls:
            try:
                change(control)
            except COM_ERRORS as exc:  # die App wurde gerade beendet
                log.debug("Audio-Sitzung weg: %s", exc)

    def peak(self) -> float:
        """0 bis 1: wie laut die App gerade wirklich ist, ohne den Regler von Windows."""
        level = 0.0
        for meter in self.meters:
            try:
                level = max(level, meter.GetPeakValue())
            except COM_ERRORS:
                pass
        return level


def read_apps() -> list[AppAudio]:
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    groups: dict[str, AppAudio] = {}
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as exc:  # z. B. kein Ausgabegerät angeschlossen
        log.debug("Audio-Sitzungen nicht lesbar: %s", exc)
        return []
    for session in sessions:
        try:
            if session.State == SESSION_EXPIRED:
                continue
            process = session.Process
            name = process.name() if process else ""
            key = name.lower() or SYSTEM_KEY
            group = groups.get(key)
            if group is None:
                exe = process.exe() if process else ""
                group = groups[key] = AppAudio(key, app_name(name) if name else SYSTEM_NAME, exe)
            group.controls.append(session.SimpleAudioVolume)
            group.meters.append(session._ctl.QueryInterface(IAudioMeterInformation))
        except Exception as exc:  # die App verschwindet oft mitten im Lesen
            log.debug("Audio-Sitzung übersprungen: %s", exc)
    return sorted(groups.values(), key=lambda app: (app.key == SYSTEM_KEY, app.name.lower()))


def device_meter():
    """Pegel des ganzen Geräts, für die Zeile ganz oben."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    try:
        return AudioUtilities.GetSpeakers().EndpointVolume.QueryInterface(IAudioMeterInformation)
    except Exception as exc:
        log.debug("Gerätepegel nicht lesbar: %s", exc)
        return None
