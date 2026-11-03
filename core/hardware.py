"""Was dieser PC kann. PopWin zeigt nur Anzeigen, die hier etwas bewirken; ein USB-Dongle wird beim nächsten Blick erkannt."""
from __future__ import annotations

import logging

import psutil

log = logging.getLogger(__name__)

FEATURES = {"bluetooth": "Bluetooth", "wifi": "WLAN", "mobile": "Mobilfunk", "battery": "Akku", "microphone": "Mikrofon",
            "speakers": "Lautsprecher", "camera": "Kamera"}


def has_battery() -> bool:
    try:
        return psutil.sensors_battery() is not None
    except (OSError, RuntimeError):
        return False


async def _radios() -> dict[str, bool]:
    from winsdk.windows.devices.radios import Radio, RadioKind
    kinds = {radio.kind for radio in await Radio.get_radios_async()}
    return {"bluetooth": RadioKind.BLUETOOTH in kinds, "wifi": RadioKind.WI_FI in kinds,
            "mobile": RadioKind.MOBILE_BROADBAND in kinds}


async def _devices() -> dict[str, bool]:
    from winsdk.windows.devices.enumeration import DeviceClass, DeviceInformation
    found = {}
    for name, kind in (("microphone", DeviceClass.AUDIO_CAPTURE), ("speakers", DeviceClass.AUDIO_RENDER),
                       ("camera", DeviceClass.VIDEO_CAPTURE)):
        found[name] = (await DeviceInformation.find_all_async(kind)).size > 0
    return found


async def detect() -> dict[str, bool]:
    """Was sich nicht klären ließ, fehlt im Ergebnis und gilt als vorhanden: Lieber eine Anzeige zu viel als eine verschwundene."""
    found = {"battery": has_battery()}
    for probe in (_radios, _devices):
        try:
            found.update(await probe())
        except Exception as exc:
            log.debug("Hardware nicht lesbar: %s", exc)
    return found


def available(known: dict, name: str) -> bool:
    return known.get(name, name != "battery")  # einen Akku gibt es erst, wenn er wirklich erkannt ist


BATTERY_LEVELS, BATTERY_CHARGING = 0xEBA0, 0xEBAB  # dieselben großen Zeichen wie in der Windows-Taskleiste
VOLUME_LEVELS = (0xE992, 0xE993, 0xE994, 0xE995)  # null bis drei Wellen
VOLUME_MUTE = 0xE74F


def battery_glyph(percent: int, charging: bool) -> str:
    """Segoe-Zeichen: je Zehnerstufe eines, beim Laden mit Blitz. Der Füllstand im Symbol folgt dem Akku."""
    level = max(0, min(10, round(percent / 10)))
    return chr((BATTERY_CHARGING if charging else BATTERY_LEVELS) + level)


def volume_glyph(volume: int | None, muted: bool) -> str:
    """Leise zeigt weniger Wellen als laut, wie bei Windows."""
    if muted:
        return chr(VOLUME_MUTE)
    volume = volume or 0
    return chr(VOLUME_LEVELS[0 if volume == 0 else 1 if volume < 34 else 2 if volume < 67 else 3])
