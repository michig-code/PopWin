"""Sammelt Systemwerte im Hintergrund, damit Leisten und Widgets nie blockieren."""
from __future__ import annotations

import asyncio
import ctypes
import gc
import logging
import queue
import re
import socket
import time
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QThread, Signal

from core import hardware, net_stats, wallpapers, win_api

log = logging.getLogger(__name__)

SYNC_CLIENTS = {
    "onedrive.exe": "OneDrive",
    "dropbox.exe": "Dropbox",
    "googledrivefs.exe": "Google Drive",
    "icloudservices.exe": "iCloud",
    "icloud.exe": "iCloud",
    "nextcloud.exe": "Nextcloud",
    "pcloud.exe": "pCloud",
    "megasync.exe": "MEGA",
}
_WIFI_HINTS = ("wi-fi", "wifi", "wlan", "wireless")


def active_network() -> tuple[str, str]:
    """(Typ, Adaptername). Der UDP-Socket sendet nichts, er fragt nur die Standardroute ab."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 53))
            local_ip = sock.getsockname()[0]
    except OSError:
        return "none", ""
    for name, addresses in psutil.net_if_addrs().items():
        if any(a.address == local_ip for a in addresses):
            kind = "wifi" if any(h in name.lower() for h in _WIFI_HINTS) else "ethernet"
            return kind, name
    return "ethernet", ""


def internet_reachable() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=1.5).close()
        return True
    except OSError:
        return False


async def _radio(kind: str):
    from winsdk.windows.devices.radios import Radio, RadioKind
    wanted = {"bluetooth": RadioKind.BLUETOOTH, "wifi": RadioKind.WI_FI}[kind]
    for radio in await Radio.get_radios_async():
        if radio.kind == wanted:
            return radio
    return None


async def _radio_state(kind: str) -> bool | None:
    from winsdk.windows.devices.radios import RadioState
    radio = await _radio(kind)
    return None if radio is None else radio.state == RadioState.ON


async def _radio_set(kind: str, enabled: bool) -> None:
    from winsdk.windows.devices.radios import Radio, RadioState
    await Radio.request_access_async()
    radio = await _radio(kind)
    if radio is not None:
        await radio.set_state_async(RadioState.ON if enabled else RadioState.OFF)


def read_volume() -> tuple[int, bool] | None:
    from pycaw.pycaw import AudioUtilities
    try:
        volume = AudioUtilities.GetSpeakers().EndpointVolume
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())
    except Exception as exc:  # z. B. kein Ausgabegerät angeschlossen
        log.debug("Lautstärke nicht lesbar: %s", exc)
        return None


def _mic_volume():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone()).EndpointVolume


def read_mic_volume() -> tuple[int, bool] | None:
    """Pegel und Stummschaltung des Standard-Mikrofons."""
    try:
        volume = _mic_volume()
        return round(volume.GetMasterVolumeLevelScalar() * 100), bool(volume.GetMute())
    except Exception as exc:  # z. B. kein Mikrofon angeschlossen
        log.debug("Mikrofon nicht lesbar: %s", exc)
        return None


def write_mic_volume(percent: int) -> None:
    _mic_volume().SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)


def set_mic_muted(muted: bool) -> None:
    _mic_volume().SetMute(1 if muted else 0, None)


def write_volume(percent: int) -> None:
    from pycaw.pycaw import AudioUtilities
    volume = AudioUtilities.GetSpeakers().EndpointVolume
    volume.SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100, None)
    if percent > 0:
        volume.SetMute(0, None)


def short_device_name(name: str) -> str:
    """„Lautsprecher (Razer Kraken V4 X)“ sagt nur in der Klammer etwas."""
    match = re.match(r"^(?:Lautsprecher|Speakers|Kopfhörer|Headphones|Headset|Mikrofonarray|Microphone Array|Mikrofon|Microphone)\s*\((.+)\)$", name)
    return re.sub(r"^\d+- ", "", match[1]) if match else name


@dataclass(frozen=True)
class AudioOutput:
    id: str
    full_name: str
    active: bool

    @property
    def name(self) -> str:
        return short_device_name(self.full_name)


def audio_outputs() -> list[AudioOutput]:
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    try:
        current = AudioUtilities.GetSpeakers().id
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    except Exception as exc:  # z. B. gar kein Ausgabegerät angeschlossen
        log.debug("Ausgabegeräte nicht lesbar: %s", exc)
        return []
    outputs = [AudioOutput(device.id, device.FriendlyName or "?", device.id == current) for device in devices]
    return sorted(outputs, key=lambda output: (not output.active, output.name.lower()))


def audio_inputs() -> list[AudioOutput]:
    """Mikrofone, gleich aufgebaut wie die Ausgabegeräte."""
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    try:
        current = AudioUtilities.GetMicrophone().GetId()
        devices = AudioUtilities.GetAllDevices(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    except Exception as exc:  # z. B. kein Mikrofon angeschlossen
        log.debug("Eingabegeräte nicht lesbar: %s", exc)
        return []
    inputs = [AudioOutput(device.id, device.FriendlyName or "?", device.id == current) for device in devices]
    return sorted(inputs, key=lambda device: (not device.active, device.name.lower()))


def set_audio_output(device_id: str) -> None:
    """Gilt für alle drei Rollen von Windows, sonst bliebe etwa Discord auf dem alten Gerät."""
    from comtypes import COMError
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities
    try:
        AudioUtilities.SetDefaultDevice(device_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
    except COMError as exc:
        raise OSError(str(exc)) from exc


def toggle_mute() -> None:
    from pycaw.pycaw import AudioUtilities
    volume = AudioUtilities.GetSpeakers().EndpointVolume
    volume.SetMute(0 if volume.GetMute() else 1, None)


class _GpuLoad:
    """Windows-Leistungszähler; AddEnglishCounter macht das sprachunabhängig."""

    def __init__(self):
        self._query = None
        self._counter = None
        self._opened = 0.0

    def _open(self) -> None:
        import win32pdh
        self.close()
        self._query = win32pdh.OpenQuery()
        self._counter = win32pdh.AddEnglishCounter(self._query, r"\GPU Engine(*)\Utilization Percentage")
        win32pdh.CollectQueryData(self._query)
        self._opened = time.monotonic()

    def close(self) -> None:
        if self._query is not None:
            import win32pdh
            win32pdh.CloseQuery(self._query)
            self._query = None

    def read(self) -> float | None:
        import win32pdh
        try:
            # Neue Prozesse tauchen erst nach erneutem Öffnen in der Instanzliste auf.
            if self._query is None or time.monotonic() - self._opened > 30:
                self._open()
                return None
            win32pdh.CollectQueryData(self._query)
            values = win32pdh.GetFormattedCounterArray(self._counter, win32pdh.PDH_FMT_DOUBLE)
            return min(100.0, sum(v for k, v in values.items() if k.endswith("engtype_3D")))
        except Exception as exc:
            log.debug("GPU-Zähler: %s", exc)
            self._query = None
            return None


def _disks() -> list[tuple[str, int, int]]:
    """(Laufwerk, belegt, gesamt) in Bytes; nicht lesbare Laufwerke wie leere Kartenleser fehlen."""
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append((part.mountpoint.rstrip("\\"), usage.used, usage.total))
    return disks


_SYSTEM_PROCESS_INFORMATION, _STATUS_INFO_LENGTH_MISMATCH = 5, 0xC0000004
_PROC_USER_TIME, _PROC_KERNEL_TIME, _PROC_NAME_LENGTH = 40, 48, 56
_PROC_NAME_BUFFER, _PROC_ID, _PROC_WORKING_SET = 64, 80, 144


def snapshot_processes() -> list[tuple[int, str, int, int]]:
    """(PID, Name, CPU-Zeit in 100 ns, Arbeitsspeicher) aller Prozesse mit einem einzigen Aufruf.

    psutil fragt jeden Prozess einzeln und hält dabei das Python-GIL - bei 250 Prozessen
    steht die Oberfläche sekundenlang. Dieser Aufruf gibt das GIL frei und braucht nur Millisekunden.
    """
    query = ctypes.windll.ntdll.NtQuerySystemInformation
    query.restype = ctypes.c_ulong
    size = 1 << 20
    while True:
        buffer = ctypes.create_string_buffer(size)
        needed = ctypes.c_ulong(0)
        status = query(_SYSTEM_PROCESS_INFORMATION, buffer, size, ctypes.byref(needed))
        if status != _STATUS_INFO_LENGTH_MISMATCH:
            break
        size = max(size * 2, needed.value + 65536)
    if status != 0:
        raise OSError(f"NtQuerySystemInformation: {status:#x}")
    processes = []
    data = bytes(buffer)
    base = ctypes.addressof(buffer)
    offset = 0
    while True:
        step = int.from_bytes(data[offset:offset + 4], "little")
        pid = int.from_bytes(data[offset + _PROC_ID:offset + _PROC_ID + 8], "little")
        length = int.from_bytes(data[offset + _PROC_NAME_LENGTH:offset + _PROC_NAME_LENGTH + 2], "little")
        pointer = int.from_bytes(data[offset + _PROC_NAME_BUFFER:offset + _PROC_NAME_BUFFER + 8], "little")
        if pid and pointer:
            name = data[pointer - base:pointer - base + length].decode("utf-16-le", "replace")
            cpu = sum(int.from_bytes(data[offset + field:offset + field + 8], "little")
                      for field in (_PROC_USER_TIME, _PROC_KERNEL_TIME))
            memory = int.from_bytes(data[offset + _PROC_WORKING_SET:offset + _PROC_WORKING_SET + 8], "little")
            processes.append((pid, name, cpu, memory))
        if step == 0:
            return processes
        offset += step


class ProcessLoad:
    """Rechnet die CPU-Zeiten zweier Messungen in Prozent um, summiert pro Programm."""

    def __init__(self):
        self._last: dict[int, int] = {}
        self._time = 0.0

    def read(self, limit: int = 8) -> list[tuple[str, float, float, str]]:
        now = time.monotonic()
        elapsed = max(0.1, now - self._time)
        cores = psutil.cpu_count() or 1
        total_memory = psutil.virtual_memory().total
        totals: dict[str, list[float]] = {}
        pids: dict[str, int] = {}
        current: dict[int, int] = {}
        for pid, name, cpu, memory in snapshot_processes():
            current[pid] = cpu
            pids[name] = pid
            entry = totals.setdefault(name, [0.0, 0.0])
            if pid in self._last:
                entry[0] += max(0, cpu - self._last[pid]) / 1e7 / elapsed / cores * 100
            entry[1] += memory / total_memory * 100
        self._last, self._time = current, now
        ranked = sorted(totals.items(), key=lambda item: (item[1][0], item[1][1]), reverse=True)
        return [(name, cpu, memory, win_api.exe_of(pids[name])) for name, (cpu, memory) in ranked[:limit]]


class SystemStatus(QThread):
    updated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._actions: queue.Queue = queue.Queue()
        self.want_processes = False  # läuft nur, wenn das Widget offen ist
        self.check_online = lambda: True
        self.performance = net_stats.PerformanceLog()
        self._process_load = ProcessLoad()
        self._net_last: tuple[float, int, int] | None = None
        self.state: dict = {
            "net_down": 0.0, "net_up": 0.0, "disks": [], "processes": [], "ram_used": 0, "ram_total": 0,
            "cpu": 0.0, "ram": 0.0, "gpu": None, "mic": [], "net_kind": "none", "net_name": "",
            "online": None, "wallpaper_engine": "", "bluetooth": None, "wifi": None, "battery": None, "hardware": {}, "cloud": [], "volume": None,
            "muted": False,
            "desktops": (1, 0), "recycle": (0, 0),
        }

    def set_bluetooth(self, enabled: bool) -> None:
        self._actions.put(("bluetooth", enabled))

    def set_wifi(self, enabled: bool) -> None:
        self._actions.put(("wifi", enabled))

    def stop(self) -> None:
        self._stop = True
        self.wait(4000)

    def run(self) -> None:
        import comtypes
        comtypes.CoInitialize()
        loop = asyncio.new_event_loop()
        gpu = _GpuLoad()
        psutil.cpu_percent(None)
        tick = 0
        while not self._stop:
            try:
                self._collect(tick, loop, gpu)
                self.updated.emit(dict(self.state))
            except Exception:
                log.exception("Statusabfrage fehlgeschlagen")
            tick += 1
            for _ in range(10):
                if self._stop:
                    break
                time.sleep(0.1)
        gpu.close()
        loop.close()
        gc.collect()  # COM-Zeiger müssen vor CoUninitialize weg, sonst stürzt das Beenden ab
        comtypes.CoUninitialize()

    def _read_network_speed(self) -> None:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        if self._net_last:
            before, down, up = self._net_last
            elapsed = max(0.1, now - before)
            self.state["net_down"] = (counters.bytes_recv - down) / elapsed
            self.state["net_up"] = (counters.bytes_sent - up) / elapsed
        self._net_last = (now, counters.bytes_recv, counters.bytes_sent)

    def _collect(self, tick: int, loop, gpu: _GpuLoad) -> None:
        s = self.state
        s["cpu"] = psutil.cpu_percent(None)
        memory = psutil.virtual_memory()
        s["ram"], s["ram_used"], s["ram_total"] = memory.percent, memory.used, memory.total
        self._read_network_speed()
        if tick % 15 == 0:
            s["disks"] = _disks()
        if self.want_processes and tick % 3 == 0:
            s["processes"] = self._process_load.read()
        reading = gpu.read()
        s["gpu"] = reading if reading is not None else s["gpu"]
        s["mic"] = win_api.microphone_users()
        s["desktops"] = win_api.virtual_desktops()
        volume = read_volume()
        if volume:
            s["volume"], s["muted"] = volume
        while not self._actions.empty():
            loop.run_until_complete(_radio_set(*self._actions.get()))
            tick = 0
        if tick % 3 == 0:
            s["net_kind"], s["net_name"] = active_network()
            s["recycle"] = win_api.recycle_bin_info()
            s["bluetooth"] = loop.run_until_complete(_radio_state("bluetooth"))
            s["wifi"] = loop.run_until_complete(_radio_state("wifi"))
            battery = psutil.sensors_battery()
            s["battery"] = (round(battery.percent), bool(battery.power_plugged)) if battery else None
        if tick % 5 == 0:
            self.performance.sample(snapshot_processes())
            names = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info["name"]}
            s["cloud"] = sorted({label for exe, label in SYNC_CLIENTS.items() if exe in names})
            s["wallpaper_engine"] = wallpapers.engine_state(process_names=names)
        if tick % 30 == 0:
            s["hardware"] = loop.run_until_complete(hardware.detect())
            s["online"] = (s["net_kind"] != "none" and internet_reachable()) if self.check_online() else None
