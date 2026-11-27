# --- [Antigravity Start] Hocheffiziente Audio- & Dual-TTS-Engine für TexttoSpeech for Windows ---
"""Audio-Geräteverwaltung, DSP, Dual-TTS (Windows OneCore + SAPI5) und persistenter Mixer."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import contextlib
from dataclasses import dataclass
import gc
import io
import logging
import queue
import re
import threading
import time
import wave
from xml.sax.saxutils import escape as _xml_escape

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - PortAudio nicht verfügbar
    sd = None

log = logging.getLogger(__name__)

STYLES: tuple[str, ...] = ("cyber", "droid", "classic", "clean")

_VIRTUAL_MIC_SPEAKER_TAGS = ("steam streaming microphone", "cable input", "voicemeeter")
_VIRTUAL_OUTPUT_TAGS = ("steam streaming microphone", "cable input", "cable output", "voicemeeter")
_VIRTUAL_AUDIO_TAGS = (
    "steam streaming microphone",
    "steam streaming speakers",
    "cable input",
    "cable output",
    "vb-audio",
    "voicemeeter",
    "virtual audio",
)
_LOOPBACK_INPUT_TAGS = ("stereomix", "stereo mix", "what u hear", "wasapi loopback")
_INPUT_AVOID_TAGS = ("hands-free", "hfp", "ag audio", "headset", "kopfhörermikrofon", "bt")
_OUTPUT_AVOID_TAGS = ("hands-free", "hfp", "ag audio")
_PREFERRED_OUTPUT_HINTS = ("kopfhörer", "headphone", "headphones", "lautsprecher", "speaker", "speakers")
_OFF_TOKENS = ("off", "none", "aus", "disabled")
_AUTO_TOKENS = ("auto", "default", "")

_MAX_TTS_CACHE_ENTRIES = 32
_MAX_TTS_CACHE_TEXT_LEN = 200
_PASSTHROUGH_MAX_BUFFER_SEC = 0.22
_NOISE_GATE_THRESHOLD = np.float32(0.015)
_RPC_E_CHANGED_MODE = -2147417850
_XML_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_VOICES_CACHE_LOCK = threading.Lock()
_VOICES_CACHE: list[tuple[str, str]] | None = None


@dataclass(frozen=True)
class _ResolvedDevice:
    """Gecachte SoundDevice-Konfiguration für einen Audio-Endpunkt."""

    index: int
    name: str
    samplerate: int
    channels: int
    effective_sr: int
    is_steam: bool
    is_wdmks: bool


@contextlib.contextmanager
def _com_scope():
    """Initialisiert STA-COM sicher auf dem aktuellen Thread und gibt es sauber wieder frei."""
    import comtypes

    initialized = False
    try:
        comtypes.CoInitialize()
        initialized = True
    except OSError as exc:
        if getattr(exc, "winerror", None) != _RPC_E_CHANGED_MODE:
            raise
    try:
        yield
    finally:
        if initialized:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _is_virtual_name(name: str) -> bool:
    lower = name.lower()
    return any(tag in lower for tag in _VIRTUAL_AUDIO_TAGS)


def _is_clean_input_name(name: str) -> bool:
    lower = name.strip().lower()
    if not lower or _is_virtual_name(lower):
        return False
    if any(tag in lower for tag in _LOOPBACK_INPUT_TAGS):
        return False
    if any(tag in lower for tag in _INPUT_AVOID_TAGS):
        return False
    return True


def _is_clean_output_name(name: str) -> bool:
    lower = name.strip().lower()
    if not lower or _is_virtual_name(lower):
        return False
    if any(tag in lower for tag in _OUTPUT_AVOID_TAGS):
        return False
    return True


def _hostapi_indices() -> tuple[int | None, int | None, int | None, int | None]:
    """Ermittelt die Indizes von WDM-KS, WASAPI, DirectSound und MME."""
    if sd is None:
        return None, None, None, None
    hostapis = sd.query_hostapis()
    wdmks = wasapi = ds = mme = None
    for idx, api in enumerate(hostapis):
        name = str(api.get("name", ""))
        if "WDM-KS" in name and wdmks is None:
            wdmks = idx
        elif "WASAPI" in name and wasapi is None:
            wasapi = idx
        elif "DirectSound" in name and ds is None:
            ds = idx
        elif "MME" in name and mme is None:
            mme = idx
    return wdmks, wasapi, ds, mme


def _hostapi_score(host: int | None, wasapi: int | None, ds: int | None, mme: int | None, wdmks: int | None) -> int:
    if host == wasapi:
        return 30
    if host == ds:
        return 20
    if host == mme:
        return 10
    if host == wdmks:
        return 2
    return 0


def _normalize_rate_pct(speech_rate: int | float | None) -> int:
    """Normalisiert beliebige Rate-Angaben (SAPI -10..10, Faktor 0.25..4.0 oder Prozent) auf Prozent."""
    if speech_rate is None:
        return 100
    if isinstance(speech_rate, float) and 0.2 <= speech_rate <= 4.0 and not speech_rate.is_integer():
        return max(40, min(260, int(round(speech_rate * 100.0))))
    if isinstance(speech_rate, (int, float)):
        val = float(speech_rate)
        if isinstance(speech_rate, float) and 0.25 <= val <= 4.0:
            return max(40, min(260, int(round(val * 100.0))))
        if -10.0 <= val <= 10.0:
            # SAPI-Skala (-10..10 -> ca. 33%..300%)
            return max(40, min(260, int(round((3.0 ** (val / 10.0)) * 100.0))))
        return max(40, min(260, int(round(val))))
    return 100


def _rate_pct_to_sapi(rate_pct: int, scream: bool = False) -> int:
    """Wandelt Prozent-Sprechgeschwindigkeit in einen SAPI-Rate-Wert (-10..10) um."""
    import math

    factor = max(0.35, min(3.0, rate_pct / 100.0))
    sapi_rate = int(round(10.0 * math.log(factor, 3.0)))
    if scream:
        sapi_rate += 1
    return max(-10, min(10, sapi_rate))


def unlock_virtual_microphone() -> tuple[bool, str]:
    """Entsperrt und entstummt aktive virtuelle Mikrofon-Endpunkte über pycaw."""
    ep = None
    devices = []
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        with _com_scope():
            unlocked_names: list[str] = []
            for data_flow in (EDataFlow.eRender.value, EDataFlow.eCapture.value):
                devices = AudioUtilities.GetAllDevices(data_flow, DEVICE_STATE.ACTIVE.value)
                for dev in devices:
                    name = str(getattr(dev, "FriendlyName", "") or "")
                    lower = name.lower()
                    if any(tag in lower for tag in _VIRTUAL_OUTPUT_TAGS):
                        try:
                            ep = dev._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
                                IAudioEndpointVolume
                            )
                            ep.SetMute(0, None)
                            target_vol = 1.0 if data_flow == EDataFlow.eRender.value else 0.95
                            ep.SetMasterVolumeLevelScalar(target_vol, None)
                            unlocked_names.append(name)
                        except Exception:
                            pass
                        finally:
                            ep = None
                devices = []
            if unlocked_names:
                return True, f"Aktiviert & entstummt: {', '.join(unlocked_names)}"
            return False, "Kein virtueller Mikrofon-Treiber (Steam Mic / VB-Cable) gefunden."
    except Exception as exc:
        return False, f"Fehler beim Entsperren: {exc}"
    finally:
        ep = None
        devices = []


def capture_default_audio_names() -> tuple[str, str]:
    """Ermittelt aktuelle Standard-Eingabe und -Ausgabe, sofern sie sauber (kein Virtual/HFP) sind."""
    mic_dev = None
    spk_dev = None
    in_devices = []
    out_devices = []
    try:
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities

        with _com_scope():
            in_name = ""
            out_name = ""
            try:
                mic_dev = AudioUtilities.GetMicrophone()
                mic_id = mic_dev.GetId() if mic_dev is not None else ""
                if mic_id:
                    in_devices = AudioUtilities.GetAllDevices(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
                    for dev in in_devices:
                        if getattr(dev, "id", "") == mic_id:
                            cand = str(getattr(dev, "FriendlyName", "") or "")
                            if _is_clean_input_name(cand):
                                in_name = cand
                            break
            except Exception:
                pass

            try:
                spk_dev = AudioUtilities.GetSpeakers()
                spk_id = getattr(spk_dev, "id", "") or (spk_dev.GetId() if hasattr(spk_dev, "GetId") else "")
                cand = str(getattr(spk_dev, "FriendlyName", "") or "")
                if not cand and spk_id:
                    out_devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
                    for dev in out_devices:
                        if getattr(dev, "id", "") == spk_id:
                            cand = str(getattr(dev, "FriendlyName", "") or "")
                            break
                if cand and _is_clean_output_name(cand):
                    out_name = cand
            except Exception:
                pass

            return in_name, out_name
    except Exception:
        return "", ""
    finally:
        mic_dev = None
        spk_dev = None
        in_devices = []
        out_devices = []


def _pick_preferred_or_clean(
    devices: list,
    preferred: str,
    is_clean_fn,
    allow_hint_bonus: bool = False,
) -> object | None:
    pref = preferred.strip().lower()
    if pref:
        exact = [d for d in devices if str(getattr(d, "FriendlyName", "") or "").lower() == pref and is_clean_fn(d.FriendlyName or "")]
        if exact:
            return exact[0]
        partial = [d for d in devices if pref in str(getattr(d, "FriendlyName", "") or "").lower() and is_clean_fn(d.FriendlyName or "")]
        if partial:
            return partial[0]

    clean = [d for d in devices if is_clean_fn(str(getattr(d, "FriendlyName", "") or ""))]
    if not clean:
        return None
    if allow_hint_bonus:
        clean.sort(
            key=lambda d: (
                0 if any(h in str(getattr(d, "FriendlyName", "") or "").lower() for h in _PREFERRED_OUTPUT_HINTS) else 1,
                str(getattr(d, "FriendlyName", "") or "").lower(),
            )
        )
    return clean[0]


def restore_normal_audio(
    mute_virtual_speaker: bool = True,
    preferred_input: str = "",
    preferred_output: str = "",
) -> tuple[bool, str]:
    """Beendet Bluetooth-Hands-Free (HFP/AG-Audio), stellt saubere Ein-/Ausgabe her und mutet Virtual-Mic-Speaker."""
    in_devices = []
    out_devices = []
    ep = None
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        roles = [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
        with _com_scope():
            in_devices = AudioUtilities.GetAllDevices(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
            out_devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)

            def _pref_input_allowed(name: str) -> bool:
                lower = name.strip().lower()
                if not lower or _is_virtual_name(lower):
                    return False
                if any(tag in lower for tag in _LOOPBACK_INPUT_TAGS):
                    return False
                return not any(tag in lower for tag in _OUTPUT_AVOID_TAGS)

            target_in = None
            if preferred_input.strip():
                target_in = _pick_preferred_or_clean(in_devices, preferred_input, _is_clean_input_name)
                if target_in is None:
                    target_in = _pick_preferred_or_clean(in_devices, preferred_input, _pref_input_allowed)
            if target_in is None:
                target_in = _pick_preferred_or_clean(in_devices, "", _is_clean_input_name)

            changed_parts: list[str] = []
            if target_in is not None:
                AudioUtilities.SetDefaultDevice(target_in.id, roles)
                changed_parts.append(f"Mic: {target_in.FriendlyName}")

            target_out = _pick_preferred_or_clean(
                out_devices,
                preferred_output,
                _is_clean_output_name,
                allow_hint_bonus=True,
            )
            if target_out is not None:
                AudioUtilities.SetDefaultDevice(target_out.id, roles)
                changed_parts.append(f"Audio: {target_out.FriendlyName}")

            if mute_virtual_speaker:
                for dev in out_devices:
                    name = str(getattr(dev, "FriendlyName", "") or "")
                    lower = name.lower()
                    if any(tag in lower for tag in _VIRTUAL_MIC_SPEAKER_TAGS):
                        try:
                            ep = dev._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(
                                IAudioEndpointVolume
                            )
                            ep.SetMute(1, None)
                        except Exception:
                            pass
                        finally:
                            ep = None

            summary = f" ({', '.join(changed_parts)})" if changed_parts else ""
            return True, f"Normaler Hi-Fi Stereo Audio-Modus wiederhergestellt{summary}."
    except Exception as exc:
        return False, f"Audio-Reset Fehler: {exc}"
    finally:
        ep = None
        in_devices = []
        out_devices = []


def find_virtual_output_device(preferred: str = "auto") -> tuple[int | None, str]:
    """Sucht den optimalen virtuellen Audio-Ausgang (Steam WDM-KS 44.1kHz Mono bevorzugt)."""
    pref = str(preferred or "auto").strip()
    if pref.lower() in _OFF_TOKENS:
        return None, "Aus"
    if sd is None:
        return None, "Nicht gefunden"
    try:
        devices = sd.query_devices()
        wdmks_idx, wasapi_idx, ds_idx, mme_idx = _hostapi_indices()
        pref_low = pref.lower()
        has_pref = pref_low not in _AUTO_TOKENS

        candidates: list[tuple[int, int, str]] = []
        for idx, dev in enumerate(devices):
            if int(dev.get("max_output_channels", 0)) <= 0:
                continue
            name = str(dev.get("name", ""))
            lower = name.lower()
            host = dev.get("hostapi")

            if has_pref and pref_low in lower:
                if "steam streaming microphone" in lower:
                    score = 130 + (20 if host == wdmks_idx else (10 if host == wasapi_idx else 0))
                else:
                    score = 120 + _hostapi_score(host, wasapi_idx, ds_idx, mme_idx, wdmks_idx)
            elif "cable input" in lower:
                score = 92 + (5 if host == wasapi_idx else 0)
            elif "steam streaming microphone" in lower:
                if host == wdmks_idx:
                    score = 98
                elif host == wasapi_idx:
                    score = 85
                elif host == ds_idx:
                    score = 80
                else:
                    score = 75
            elif "voicemeeter" in lower:
                score = 75 + (5 if host == wasapi_idx else 0)
            else:
                continue
            candidates.append((score, idx, name))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, best_idx, best_name = candidates[0]
            return best_idx, best_name
    except Exception:
        pass
    return None, "Nicht gefunden"


def _find_non_wdmks_steam_output() -> tuple[int | None, str]:
    """Fallback für Steam Streaming Microphone, falls WDM-KS exklusiv blockiert ist."""
    if sd is None:
        return None, "Nicht gefunden"
    try:
        devices = sd.query_devices()
        wdmks_idx, wasapi_idx, ds_idx, mme_idx = _hostapi_indices()
        candidates: list[tuple[int, int, str]] = []
        for idx, dev in enumerate(devices):
            if int(dev.get("max_output_channels", 0)) <= 0:
                continue
            name = str(dev.get("name", ""))
            host = dev.get("hostapi")
            if "steam streaming microphone" in name.lower() and host != wdmks_idx:
                candidates.append((_hostapi_score(host, wasapi_idx, ds_idx, mme_idx, wdmks_idx), idx, name))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, idx, name = candidates[0]
            return idx, name
    except Exception:
        pass
    return None, "Nicht gefunden"


def find_output_device(preferred: str = "default") -> tuple[int | None, str]:
    """Sucht ein Ausgabegerät für Kopfhörer/Mithören (bevorzugt WASAPI)."""
    pref = str(preferred or "default").strip()
    if pref.lower() in _OFF_TOKENS:
        return None, "Aus"
    if sd is None:
        return None, "Nicht gefunden"
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        wdmks_idx, wasapi_idx, ds_idx, mme_idx = _hostapi_indices()
        pref_low = pref.lower()

        if pref_low not in _AUTO_TOKENS:
            matches: list[tuple[int, int, str]] = []
            for idx, dev in enumerate(devices):
                if int(dev.get("max_output_channels", 0)) <= 0:
                    continue
                name = str(dev.get("name", ""))
                lower = name.lower()
                if pref_low not in lower:
                    continue
                exact_bonus = 20 if lower == pref_low else 0
                non_virt = 15 if not _is_virtual_name(lower) else 0
                score = exact_bonus + non_virt + _hostapi_score(dev.get("hostapi"), wasapi_idx, ds_idx, mme_idx, wdmks_idx)
                matches.append((score, idx, name))
            if matches:
                matches.sort(key=lambda item: item[0], reverse=True)
                _, idx, name = matches[0]
                return idx, name

        # System-Standard über WASAPI bevorzugen (volle Namen, geringe Latenz)
        if wasapi_idx is not None:
            wasapi_def = hostapis[wasapi_idx].get("default_output_device", -1)
            if isinstance(wasapi_def, int) and 0 <= wasapi_def < len(devices):
                d = devices[wasapi_def]
                name = str(d.get("name", ""))
                if int(d.get("max_output_channels", 0)) > 0 and _is_clean_output_name(name):
                    return wasapi_def, name

        sys_def = sd.default.device[1] if sd.default.device else -1
        if isinstance(sys_def, int) and 0 <= sys_def < len(devices):
            d = devices[sys_def]
            name = str(d.get("name", ""))
            if int(d.get("max_output_channels", 0)) > 0 and not _is_virtual_name(name):
                # Prüfe auf gleichnamiges WASAPI-Gerät ohne 31-Zeichen-MME-Abschnitt
                if wasapi_idx is not None:
                    prefix = name[:24].lower()
                    for idx, wd in enumerate(devices):
                        if (
                            wd.get("hostapi") == wasapi_idx
                            and int(wd.get("max_output_channels", 0)) > 0
                            and str(wd.get("name", "")).lower().startswith(prefix)
                        ):
                            return idx, str(wd.get("name", ""))
                return sys_def, name

        # Fallback: bestes sauberes Ausgabegerät
        candidates: list[tuple[int, int, str]] = []
        for idx, dev in enumerate(devices):
            if int(dev.get("max_output_channels", 0)) <= 0:
                continue
            name = str(dev.get("name", ""))
            if not _is_clean_output_name(name):
                continue
            hint = 10 if any(h in name.lower() for h in _PREFERRED_OUTPUT_HINTS) else 0
            score = hint + _hostapi_score(dev.get("hostapi"), wasapi_idx, ds_idx, mme_idx, wdmks_idx)
            candidates.append((score, idx, name))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, idx, name = candidates[0]
            return idx, name

        if isinstance(sys_def, int) and 0 <= sys_def < len(devices):
            return sys_def, str(devices[sys_def].get("name", ""))
    except Exception:
        pass
    return None, "Nicht gefunden"


def find_input_device(preferred: str = "auto") -> tuple[int | None, str]:
    """Sucht ein echtes physisches Mikrofon (wählt bei 'auto' niemals virtuelle Kabel oder Loopbacks)."""
    pref = str(preferred or "auto").strip()
    if pref.lower() in _OFF_TOKENS:
        return None, "Aus"
    if sd is None:
        return None, "Kein echtes Mikrofon gefunden"
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        wdmks_idx, wasapi_idx, ds_idx, mme_idx = _hostapi_indices()
        pref_low = pref.lower()

        def _eligible_input(dev_dict: dict) -> bool:
            if int(dev_dict.get("max_input_channels", 0)) <= 0:
                return False
            name = str(dev_dict.get("name", ""))
            lower = name.lower()
            if not lower or _is_virtual_name(lower):
                return False
            if any(tag in lower for tag in _LOOPBACK_INPUT_TAGS):
                return False
            if "soundmapper" in lower or "primärer soundaufnahmetreiber" in lower:
                return False
            return True

        if pref_low not in _AUTO_TOKENS:
            matches: list[tuple[int, int, str]] = []
            for idx, dev in enumerate(devices):
                if not _eligible_input(dev):
                    continue
                name = str(dev.get("name", ""))
                lower = name.lower()
                if pref_low not in lower:
                    continue
                exact_bonus = 25 if lower == pref_low else 0
                clean_bonus = 30 if _is_clean_input_name(name) else 0
                score = exact_bonus + clean_bonus + _hostapi_score(dev.get("hostapi"), wasapi_idx, ds_idx, mme_idx, wdmks_idx)
                matches.append((score, idx, name))
            if matches:
                matches.sort(key=lambda item: item[0], reverse=True)
                _, idx, name = matches[0]
                return idx, name

        default_names: set[str] = set()
        if wasapi_idx is not None:
            w_def = hostapis[wasapi_idx].get("default_input_device", -1)
            if isinstance(w_def, int) and 0 <= w_def < len(devices):
                d = devices[w_def]
                name = str(d.get("name", ""))
                if _eligible_input(d) and _is_clean_input_name(name):
                    return w_def, name
                if _eligible_input(d):
                    default_names.add(name[:24].lower())

        sys_in = sd.default.device[0] if sd.default.device else -1
        if isinstance(sys_in, int) and 0 <= sys_in < len(devices):
            d = devices[sys_in]
            if _eligible_input(d):
                default_names.add(str(d.get("name", ""))[:24].lower())

        candidates: list[tuple[int, int, str]] = []
        for idx, dev in enumerate(devices):
            if not _eligible_input(dev):
                continue
            name = str(dev.get("name", ""))
            lower = name.lower()
            clean_score = 100 if _is_clean_input_name(name) else -50
            def_bonus = 15 if any(lower.startswith(dn) for dn in default_names if dn) else 0
            score = clean_score + def_bonus + _hostapi_score(dev.get("hostapi"), wasapi_idx, ds_idx, mme_idx, wdmks_idx)
            candidates.append((score, idx, name))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, idx, name = candidates[0]
            return idx, name
    except Exception:
        pass
    return None, "Kein echtes Mikrofon gefunden"


def list_available_voices() -> list[tuple[str, str]]:
    """Listet OneCore- (winsdk) und SAPI5-Stimmen gecacht auf; fängt Fehler ab."""
    global _VOICES_CACHE
    with _VOICES_CACHE_LOCK:
        if _VOICES_CACHE is not None:
            return list(_VOICES_CACHE)

    voices: list[tuple[str, str]] = [("auto", "Automatisch (Windows Standard)")]
    seen_ids: set[str] = {"auto"}

    try:
        from winsdk.windows.media.speechsynthesis import SpeechSynthesizer

        for voice in SpeechSynthesizer.all_voices:
            vid = f"winsdk:{voice.id}"
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            lang = str(getattr(voice, "language", "") or "").strip()
            disp = str(getattr(voice, "display_name", "") or voice.id).strip()
            label = f"{disp} ({lang})" if lang else disp
            voices.append((vid, label))
    except Exception:
        pass

    sapi_voice = None
    sapi_voices = None
    try:
        import comtypes.client

        with _com_scope():
            sapi_voice = comtypes.client.CreateObject("SAPI.SpVoice")
            sapi_voices = sapi_voice.GetVoices()
            count = int(getattr(sapi_voices, "Count", 0))
            for idx in range(count):
                tok = sapi_voices.Item(idx)
                tok_id = str(tok.Id)
                vid = f"sapi:{tok_id}"
                if vid in seen_ids:
                    continue
                seen_ids.add(vid)
                desc = str(tok.GetDescription() or tok_id).strip()
                voices.append((vid, f"{desc} [SAPI5]"))
    except Exception:
        pass
    finally:
        sapi_voices = None
        sapi_voice = None

    with _VOICES_CACHE_LOCK:
        _VOICES_CACHE = list(voices)
    return voices


def is_scream_text(text: str, force_scream: bool = False) -> bool:
    """Erkennt Schrei-Modus anhand von force_scream, Großbuchstaben-Anteil (>= 65%) oder '!' am Ende."""
    if force_scream:
        return True
    txt = str(text or "")
    letters = [c for c in txt if c.isalpha()]
    if len(letters) >= 3 and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.65:
        return True
    if txt.rstrip().endswith("!"):
        return True
    return False


def resample_1d(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resampelt ein 1D-Audiosignal (float32) inkl. Moving-Average-Anti-Aliasing bei starkem Downsampling."""
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim > 1:
        arr = np.mean(arr, axis=1, dtype=np.float32)
    if src_sr <= 0 or dst_sr <= 0 or arr.size == 0 or src_sr == dst_sr:
        return arr

    if dst_sr < src_sr * 0.9 and arr.size >= 2:
        win = min(arr.size, max(2, int(round(src_sr / float(dst_sr)))))
        kernel = np.full(win, 1.0 / float(win), dtype=np.float32)
        left = win // 2
        right = win - 1 - left
        padded = np.pad(arr, (left, right), mode="edge")
        arr = np.convolve(padded, kernel, mode="valid").astype(np.float32, copy=False)

    dst_len = max(1, int(round(arr.size * (float(dst_sr) / float(src_sr)))))
    if dst_len == arr.size:
        return arr

    src_x = np.linspace(0.0, 1.0, arr.size, endpoint=False, dtype=np.float32)
    dst_x = np.linspace(0.0, 1.0, dst_len, endpoint=False, dtype=np.float32)
    return np.interp(dst_x, src_x, arr).astype(np.float32)


def apply_robot_dsp(
    samples: np.ndarray,
    sr: int,
    style: str = "cyber",
    scream: bool = False,
    boost_pct: int = 280,
    normal_pct: int = 100,
) -> tuple[np.ndarray, np.ndarray]:
    """Verwandelt ein Sprachsignal per NumPy in Roboter-Audio (Kopfhörer & VC-Mikrofon) mit Soft-Limiter."""
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim > 1:
        x = np.mean(x, axis=1, dtype=np.float32)
    x = x.copy()
    if x.size == 0 or sr <= 0:
        return x, x.copy()

    # Stille an Anfang/Ende sanft trimmen
    non_silent = np.where(np.abs(x) > 0.008)[0]
    if non_silent.size > 0:
        start = max(0, int(non_silent[0]) - int(sr * 0.02))
        end = min(x.size, int(non_silent[-1]) + int(sr * 0.05))
        x = x[start:end]
    if x.size == 0:
        return x, x.copy()

    # Pre-Emphasis für helle, verständliche Sprachpräsenz
    bright = x.copy()
    if bright.size > 1:
        bright[1:] = x[1:] - np.float32(0.28) * x[:-1]

    norm_style = style if style in STYLES else "cyber"

    if norm_style == "clean":
        # Reines Pre-Emphasis + Normalisierung ohne Ringmodulation oder Bitcrush
        y = np.float32(0.78) * x + np.float32(0.32) * bright
        peak = float(np.max(np.abs(y)))
        if peak > 1e-5:
            y = y * np.float32(0.85 / peak)
    elif norm_style == "droid":
        x_crisp = np.float32(0.72) * x + np.float32(0.45) * bright
        t = np.arange(x_crisp.size, dtype=np.float32) / np.float32(sr)
        mod1 = np.sin(np.float32(2.0 * np.pi * 92.0) * t)
        mod2 = np.sin(np.float32(2.0 * np.pi * 184.0) * t)
        ring = x_crisp * (np.float32(0.62) + np.float32(0.28) * mod1 + np.float32(0.14) * mod2)
        delay = max(1, int(sr * 0.0085))
        comb = ring.copy()
        for idx in range(2):
            d = delay * (idx + 1)
            if d < comb.size:
                comb[d:] += ring[:-d] * np.float32(0.34 / (idx + 1))
        # Erst normalisieren, DANN Bitcrush für gleichmäßige Quantisierung
        peak = float(np.max(np.abs(comb)))
        if peak > 1e-5:
            comb = comb * np.float32(0.85 / peak)
        y = np.round(comb * np.float32(24.0)) / np.float32(24.0)
    elif norm_style == "classic":
        x_crisp = np.float32(0.72) * x + np.float32(0.45) * bright
        delay = max(1, int(sr * 0.0065))
        y = x_crisp.copy()
        if delay < y.size:
            y[delay:] += x_crisp[:-delay] * np.float32(0.20)
        peak = float(np.max(np.abs(y)))
        if peak > 1e-5:
            y = y * np.float32(0.85 / peak)
    else:
        # "cyber"
        x_crisp = np.float32(0.72) * x + np.float32(0.45) * bright
        t = np.arange(x_crisp.size, dtype=np.float32) / np.float32(sr)
        carrier = np.sin(np.float32(2.0 * np.pi * 82.0) * t) + np.float32(0.30) * np.sin(
            np.float32(2.0 * np.pi * 164.0) * t
        )
        ring = x_crisp * (np.float32(0.74) + np.float32(0.26) * carrier)
        delay1 = max(1, int(sr * 0.0075))
        delay2 = max(1, int(sr * 0.0115))
        y = ring.copy()
        if delay1 < y.size:
            y[delay1:] += ring[:-delay1] * np.float32(0.32)
        if delay2 < y.size:
            y[delay2:] += ring[:-delay2] * np.float32(0.16)
        peak = float(np.max(np.abs(y)))
        if peak > 1e-5:
            y = y * np.float32(0.85 / peak)

    # Sanftes Noise-Gate vor Scream-Overdrive & VC-Boost (verhindert Hochziehen von Grundrauschen)
    abs_y = np.abs(y)
    gate_mask = abs_y < _NOISE_GATE_THRESHOLD
    if np.any(gate_mask):
        y_gated = y.copy()
        y_gated[gate_mask] *= (abs_y[gate_mask] / _NOISE_GATE_THRESHOLD) ** 2
    else:
        y_gated = y

    normal_gain = max(0.1, min(3.0, float(normal_pct) / 100.0))
    scream_gain = max(1.0, min(6.0, float(boost_pct) / 100.0))

    if scream:
        hp_sig = np.tanh(y_gated * np.float32(1.55)) * np.float32(0.86)
        vm_sig = np.tanh(y_gated * np.float32(scream_gain * 1.55)) * np.float32(0.96)
    else:
        hp_sig = np.tanh(y * np.float32(1.05)) * np.float32(0.84)
        vm_sig = np.tanh(y_gated * np.float32(normal_gain * 1.05)) * np.float32(0.95)

    # 6ms Fade-In & 6ms Fade-Out gegen Start-/End-Knackser
    fade_n = min(hp_sig.size // 2, max(1, int(round(sr * 0.006))))
    if fade_n > 0:
        fade_in = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        fade_out = fade_in[::-1]
        hp_sig[:fade_n] *= fade_in
        hp_sig[-fade_n:] *= fade_out
        vm_sig[:fade_n] *= fade_in
        vm_sig[-fade_n:] *= fade_out

    return hp_sig.astype(np.float32, copy=False), vm_sig.astype(np.float32, copy=False)


class _AudioChunkQueue:
    """Thread-sicherer O(1)-Ringbuffer auf Basis von collections.deque ohne np.vstack."""

    def __init__(self, max_samples: int = 0) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._offset = 0
        self._total_samples = 0
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._drained = threading.Event()
        self._drained.set()

    def push(self, chunk: np.ndarray) -> None:
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return
        with self._lock:
            self._chunks.append(arr)
            self._total_samples += arr.size
            self._drained.clear()
            if self._max_samples > 0:
                while self._total_samples > self._max_samples and len(self._chunks) > 1:
                    dropped = self._chunks.popleft()
                    self._total_samples -= dropped.size - self._offset
                    self._offset = 0
                if self._total_samples > self._max_samples and self._chunks:
                    excess = self._total_samples - self._max_samples
                    self._offset += excess
                    self._total_samples -= excess

    def pull(self, out_mono: np.ndarray) -> int:
        frames = out_mono.size
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                head = self._chunks[0]
                avail = head.size - self._offset
                take = min(frames - filled, avail)
                out_mono[filled : filled + take] = head[self._offset : self._offset + take]
                filled += take
                self._offset += take
                self._total_samples -= take
                if self._offset >= head.size:
                    self._chunks.popleft()
                    self._offset = 0
            if self._total_samples <= 0:
                self._total_samples = 0
                self._drained.set()
        if filled < frames:
            out_mono[filled:].fill(0.0)
        return filled

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._offset = 0
            self._total_samples = 0
            self._drained.set()

    def wait_drained(self, timeout: float | None = None) -> bool:
        return self._drained.wait(timeout=timeout)


def _query_resolved_device(role: str, preferred: str) -> _ResolvedDevice | None:
    """Löst eine Geräte-Präferenz in konkrete SoundDevice-Stream-Parameter auf."""
    if sd is None:
        return None
    if role == "virtual":
        idx, name = find_virtual_output_device(preferred)
    elif role == "headphone":
        idx, name = find_output_device(preferred)
    elif role == "input":
        idx, name = find_input_device(preferred)
    else:
        return None

    if idx is None:
        return None

    dev_info = sd.query_devices(idx)
    host_name = str(sd.query_hostapis(dev_info["hostapi"]).get("name", "")) if "hostapi" in dev_info else ""
    lower = name.lower()
    is_steam = "steam streaming microphone" in lower
    is_wdmks = "WDM-KS" in host_name

    if role == "input":
        sr = int(dev_info.get("default_samplerate", 44100))
        ch = 1
        eff_sr = sr
    elif is_steam and is_wdmks:
        sr = 44100
        ch = 1
        eff_sr = 44100
    elif is_steam:
        sr = int(dev_info.get("default_samplerate", 48000))
        ch = min(2, max(1, int(dev_info.get("max_output_channels", 2))))
        eff_sr = int(round(44100.0 / ch))
    else:
        sr = int(dev_info.get("default_samplerate", 44100))
        ch = min(2, max(1, int(dev_info.get("max_output_channels", 2))))
        eff_sr = sr

    return _ResolvedDevice(
        index=idx,
        name=name,
        samplerate=sr,
        channels=ch,
        effective_sr=eff_sr,
        is_steam=is_steam,
        is_wdmks=is_wdmks,
    )


def _format_channel_audio(mono: np.ndarray, channels: int) -> np.ndarray:
    arr = np.asarray(mono, dtype=np.float32).reshape(-1)
    if channels == 2:
        return np.column_stack((arr, arr)).astype(np.float32, copy=False)
    return arr.reshape(-1, 1).astype(np.float32, copy=False)


class RobotVoiceEngine:
    """Thread-basierte Dual-TTS- & Audio-Mixer-Engine mit LRU-Cache und persistentem Passthrough."""

    def __init__(self, config_provider, on_level=None, on_speaking=None, on_error=None) -> None:
        self._config_provider = config_provider
        self._on_level = on_level
        self._on_speaking = on_speaking
        self._on_error = on_error

        self._cfg_lock = threading.Lock()
        self._cfg_overlay: dict = {}

        self._dev_cache_lock = threading.Lock()
        self._dev_cache: dict[tuple[str, str], _ResolvedDevice | None] = {}

        self._tts_cache_lock = threading.Lock()
        self._tts_cache: OrderedDict[tuple[str, str, int, bool], tuple[np.ndarray, int]] = OrderedDict()

        self._queue: queue.Queue[tuple[str, bool] | None] = queue.Queue()
        self._running = True

        self._vm_stream_lock = threading.Lock()
        self._passthru_lock = threading.Lock()
        self._passthru_stop = threading.Event()
        self._passthru_ready = threading.Event()
        self._passthru_active = threading.Event()
        self._passthru_thread: threading.Thread | None = None
        self._passthru_key: tuple[str, str] | None = None
        self._passthru_dev: _ResolvedDevice | None = None
        self._passthru_tts_q = _AudioChunkQueue()

        self._winsdk_synth = None
        self._winsdk_loop: asyncio.AbstractEventLoop | None = None
        self._sapi_voice = None

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="tts-audio-worker")
        self._worker_thread.start()
        self._sync_passthrough(self._get_config())

    def _emit_level(self, level: float, scream: bool) -> None:
        cb = self._on_level
        if cb is not None:
            try:
                cb(float(level), bool(scream))
            except Exception:
                pass

    def _emit_speaking(self, speaking: bool) -> None:
        cb = self._on_speaking
        if cb is not None:
            try:
                cb(bool(speaking))
            except Exception:
                pass

    def _emit_error(self, message: str) -> None:
        log.warning("RobotVoiceEngine: %s", message)
        cb = self._on_error
        if cb is not None:
            try:
                cb(str(message))
            except Exception:
                pass

    def _get_config(self) -> dict:
        base: dict = {}
        try:
            raw = self._config_provider() if callable(self._config_provider) else self._config_provider
            if isinstance(raw, dict):
                base = dict(raw)
        except Exception:
            base = {}
        with self._cfg_lock:
            if self._cfg_overlay:
                base.update(self._cfg_overlay)
        return base

    def _invalidate_device_cache(self) -> None:
        with self._dev_cache_lock:
            self._dev_cache.clear()

    def _resolve_device(self, role: str, preferred: str) -> _ResolvedDevice | None:
        key = (role, str(preferred))
        with self._dev_cache_lock:
            if key in self._dev_cache:
                return self._dev_cache[key]
        resolved = _query_resolved_device(role, preferred)
        with self._dev_cache_lock:
            self._dev_cache[key] = resolved
        return resolved

    def speak(self, text: str, force_scream: bool = False) -> None:
        """Reiht einen Text in die asynchrone TTS-Warteschlange ein."""
        if not self._running:
            return
        clean = str(text or "").strip()
        if clean:
            self._queue.put((clean, bool(force_scream)))

    def update_config(self, cfg: dict | None = None) -> None:
        """Aktualisiert die Konfiguration, invalidiert den Geräte-Cache und schaltet Passthrough sofort um."""
        with self._cfg_lock:
            if cfg is None:
                self._cfg_overlay.clear()
            else:
                self._cfg_overlay.update(cfg)
        self._invalidate_device_cache()
        if self._running:
            self._sync_passthrough(self._get_config())

    def clear_cache(self) -> None:
        """Leert den TTS-Synthese-LRU-Cache sowie den Stimmen-Cache."""
        global _VOICES_CACHE
        with self._tts_cache_lock:
            self._tts_cache.clear()
        with _VOICES_CACHE_LOCK:
            _VOICES_CACHE = None

    def shutdown(self) -> None:
        """Beendet Passthrough, laufende Wiedergabe und Worker-Thread sauber."""
        if not self._running:
            return
        self._running = False
        self._stop_passthrough()
        self._queue.put(None)
        if self._worker_thread.is_alive() and threading.current_thread() is not self._worker_thread:
            self._worker_thread.join(timeout=2.5)

    def _stop_passthrough(self) -> None:
        with self._passthru_lock:
            thread = self._passthru_thread
            self._passthru_stop.set()
            self._passthru_tts_q.clear()
            self._passthru_thread = None
            self._passthru_key = None
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _sync_passthrough(self, cfg: dict) -> None:
        enabled = bool(cfg.get("mic_passthrough", False))
        vm_pref = str(cfg.get("virtual_device", "auto") or "auto")
        in_pref = str(cfg.get("input_device", cfg.get("mic_device", "auto")) or "auto")

        if (
            not self._running
            or not enabled
            or vm_pref.strip().lower() in _OFF_TOKENS
            or in_pref.strip().lower() in _OFF_TOKENS
        ):
            self._stop_passthrough()
            return

        new_key = (vm_pref, in_pref)
        with self._passthru_lock:
            if (
                self._passthru_thread is not None
                and self._passthru_thread.is_alive()
                and self._passthru_key == new_key
            ):
                return

        self._stop_passthrough()
        with self._passthru_lock:
            if not self._running:
                return
            self._passthru_stop.clear()
            self._passthru_ready.clear()
            self._passthru_active.clear()
            self._passthru_key = new_key
            thread = threading.Thread(
                target=self._passthrough_loop,
                args=(vm_pref, in_pref),
                daemon=True,
                name="tts-mic-passthrough",
            )
            self._passthru_thread = thread
            thread.start()

    def _open_virtual_output_stream(self, vm_dev: _ResolvedDevice, callback):
        """Öffnet den virtuellen Ausgangs-Stream; weicht bei blockiertem WDM-KS ggf. auf WASAPI aus."""
        try:
            stream = sd.OutputStream(
                device=vm_dev.index,
                channels=vm_dev.channels,
                samplerate=vm_dev.samplerate,
                dtype="float32",
                callback=callback,
            )
            return stream, vm_dev
        except Exception:
            if vm_dev.is_steam and vm_dev.is_wdmks:
                fb_idx, fb_name = _find_non_wdmks_steam_output()
                if fb_idx is not None:
                    dev_info = sd.query_devices(fb_idx)
                    sr = int(dev_info.get("default_samplerate", 48000))
                    ch = min(2, max(1, int(dev_info.get("max_output_channels", 2))))
                    fb_dev = _ResolvedDevice(
                        index=fb_idx,
                        name=fb_name,
                        samplerate=sr,
                        channels=ch,
                        effective_sr=int(round(44100.0 / ch)),
                        is_steam=True,
                        is_wdmks=False,
                    )
                    stream = sd.OutputStream(
                        device=fb_dev.index,
                        channels=fb_dev.channels,
                        samplerate=fb_dev.samplerate,
                        dtype="float32",
                        callback=callback,
                    )
                    return stream, fb_dev
            raise

    def _passthrough_loop(self, vm_pref: str, in_pref: str) -> None:
        """Persistenter Einzel-OutputStream auf virtuellem Mic + Live-Mic-Input + samplegenaues TTS-Mixing."""
        if sd is None:
            self._passthru_ready.set()
            return

        vm_dev = self._resolve_device("virtual", vm_pref)
        in_dev = self._resolve_device("input", in_pref)
        if vm_dev is None or in_dev is None:
            self._emit_error("Mikrofon-Passthrough konnte kein Eingabe- oder virtuelles Ausgabegerät finden.")
            self._passthru_ready.set()
            return

        acquired_lock = False
        try:
            while self._running and not self._passthru_stop.is_set():
                if self._vm_stream_lock.acquire(timeout=0.05):
                    acquired_lock = True
                    break
            if not acquired_lock or self._passthru_stop.is_set() or not self._running:
                return

            sr_in = in_dev.samplerate
            block_in = max(256, int(round(sr_in * 0.02)))
            mic_q = _AudioChunkQueue(max_samples=max(2048, int(vm_dev.effective_sr * _PASSTHROUGH_MAX_BUFFER_SEC)))
            tts_q = self._passthru_tts_q
            tts_q.clear()

            active_dev = vm_dev
            duck_gain = 1.0
            mic_buf = np.zeros(0, dtype=np.float32)
            tts_buf = np.zeros(0, dtype=np.float32)
            mix_buf = np.zeros(0, dtype=np.float32)

            def in_cb(indata, _frames, _ti, _st) -> None:
                if self._passthru_stop.is_set() or not self._running:
                    return
                mono = indata[:, 0].copy()
                if sr_in != active_dev.effective_sr:
                    mono = resample_1d(mono, sr_in, active_dev.effective_sr)
                mic_q.push(mono)

            def out_cb(outdata, frames, _ti, _st) -> None:
                nonlocal duck_gain, mic_buf, tts_buf, mix_buf
                if self._passthru_stop.is_set() or not self._running:
                    outdata.fill(0.0)
                    return

                if mic_buf.size != frames:
                    mic_buf = np.empty(frames, dtype=np.float32)
                    tts_buf = np.empty(frames, dtype=np.float32)
                    mix_buf = np.empty(frames, dtype=np.float32)

                mic_q.pull(mic_buf)
                n_tts = tts_q.pull(tts_buf)

                target_duck = 0.16 if n_tts > 0 else 1.0
                if duck_gain != target_duck:
                    tau = 0.025 if target_duck < duck_gain else 0.18
                    step = min(1.0, float(frames) / max(1.0, active_dev.samplerate * tau))
                    next_duck = duck_gain + float(np.clip(target_duck - duck_gain, -step, step))
                    ramp = np.linspace(duck_gain, next_duck, frames, dtype=np.float32)
                    np.multiply(mic_buf, ramp, out=mix_buf)
                    duck_gain = next_duck
                else:
                    np.multiply(mic_buf, np.float32(duck_gain), out=mix_buf)

                mix_buf += tts_buf
                mix_buf /= np.float32(0.96)
                np.tanh(mix_buf, out=mix_buf)
                mix_buf *= np.float32(0.96)

                if active_dev.channels == 2:
                    outdata[:, 0] = mix_buf
                    outdata[:, 1] = mix_buf
                else:
                    outdata[:, 0] = mix_buf

            out_stream, active_dev = self._open_virtual_output_stream(vm_dev, out_cb)
            self._passthru_dev = active_dev
            with out_stream:
                with sd.InputStream(
                    device=in_dev.index,
                    channels=1,
                    samplerate=sr_in,
                    blocksize=block_in,
                    dtype="float32",
                    callback=in_cb,
                ):
                    self._passthru_active.set()
                    self._passthru_ready.set()
                    while self._running and not self._passthru_stop.is_set():
                        self._passthru_stop.wait(0.05)
        except Exception as exc:
            self._emit_error(f"Passthrough-Stream Fehler: {exc}")
        finally:
            self._passthru_active.clear()
            self._passthru_dev = None
            self._passthru_tts_q.clear()
            self._passthru_ready.set()
            if acquired_lock:
                try:
                    self._vm_stream_lock.release()
                except Exception:
                    pass

    def _play_on_device(
        self,
        samples: np.ndarray,
        src_sr: int,
        dev: _ResolvedDevice,
        volume_scale: float = 1.0,
        is_virtual: bool = False,
    ) -> None:
        if sd is None or samples.size == 0:
            return
        try:
            scaled = (samples * np.float32(volume_scale)).astype(np.float32, copy=False)

            def _build_stream_and_audio(target_dev: _ResolvedDevice):
                resampled = resample_1d(scaled, src_sr, target_dev.effective_sr)
                audio_arr = _format_channel_audio(resampled, target_dev.channels)
                pos = 0
                done = threading.Event()

                def cb(outdata, frames, _ti, _st) -> None:
                    nonlocal pos
                    if not self._running:
                        outdata.fill(0.0)
                        done.set()
                        raise sd.CallbackStop()
                    rem = len(audio_arr) - pos
                    if rem <= 0:
                        outdata.fill(0.0)
                        done.set()
                        raise sd.CallbackStop()
                    n = min(frames, rem)
                    outdata[:n] = audio_arr[pos : pos + n]
                    pos += n
                    if n < frames:
                        outdata[n:].fill(0.0)
                        done.set()
                        raise sd.CallbackStop()

                if is_virtual:
                    stream, actual_dev = self._open_virtual_output_stream(target_dev, cb)
                    if actual_dev.effective_sr != target_dev.effective_sr or actual_dev.channels != target_dev.channels:
                        stream.close()
                        resampled_fb = resample_1d(scaled, src_sr, actual_dev.effective_sr)
                        audio_arr = _format_channel_audio(resampled_fb, actual_dev.channels)
                        stream = sd.OutputStream(
                            device=actual_dev.index,
                            channels=actual_dev.channels,
                            samplerate=actual_dev.samplerate,
                            dtype="float32",
                            callback=cb,
                        )
                    return stream, actual_dev, audio_arr, done

                stream = sd.OutputStream(
                    device=target_dev.index,
                    channels=target_dev.channels,
                    samplerate=target_dev.samplerate,
                    dtype="float32",
                    callback=cb,
                )
                return stream, target_dev, audio_arr, done

            stream, actual_dev, audio_arr, done = _build_stream_and_audio(dev)
            dur = len(audio_arr) / float(max(1, actual_dev.samplerate))
            with stream:
                done.wait(timeout=dur + 2.5)
        except Exception as exc:
            self._invalidate_device_cache()
            self._emit_error(f"Audio-Wiedergabe auf '{dev.name}' fehlgeschlagen: {exc}")

    def _synthesize_winsdk(self, text: str, raw_voice_id: str, rate_pct: int, scream: bool) -> tuple[np.ndarray, int]:
        from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
        from winsdk.windows.storage.streams import DataReader

        if self._winsdk_synth is None:
            self._winsdk_synth = SpeechSynthesizer()
        if self._winsdk_loop is None or self._winsdk_loop.is_closed():
            self._winsdk_loop = asyncio.new_event_loop()

        synth = self._winsdk_synth
        target_voice = None
        if raw_voice_id:
            q = raw_voice_id.lower()
            all_voices = list(SpeechSynthesizer.all_voices)
            target_voice = next((v for v in all_voices if v.id == raw_voice_id), None)
            if target_voice is None:
                target_voice = next(
                    (v for v in all_voices if q in v.id.lower() or q in str(v.display_name).lower()),
                    None,
                )
            if target_voice is None:
                raise LookupError(f"OneCore-Stimme nicht gefunden: {raw_voice_id}")
        else:
            target_voice = SpeechSynthesizer.default_voice

        if target_voice is not None and getattr(synth.voice, "id", None) != target_voice.id:
            synth.voice = target_voice

        lang = str(getattr(synth.voice, "language", "") or "de-DE")
        lang_escaped = _xml_escape(lang, {'"': "&quot;", "'": "&apos;"})
        clean_xml = _xml_escape(_XML_CTRL_RE.sub("", text))

        eff_rate = min(260, max(40, int(round(rate_pct * (1.08 if scream else 1.0)))))
        pitch = "+4%" if scream else "+0%"
        ssml = (
            f"<speak version='1.0' xml:lang='{lang_escaped}'>"
            f"<prosody rate='{eff_rate}%' pitch='{pitch}'>{clean_xml}</prosody>"
            f"</speak>"
        )

        async def _synth() -> bytes:
            stream = await synth.synthesize_ssml_to_stream_async(ssml)
            try:
                size = int(stream.size)
                reader = DataReader(stream.get_input_stream_at(0))
                try:
                    await reader.load_async(size)
                    buf = bytearray(size)
                    reader.read_bytes(buf)
                    return bytes(buf)
                finally:
                    reader.close()
            finally:
                stream.close()

        wav_bytes = self._winsdk_loop.run_until_complete(_synth())
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())

        if sw == 2:
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / np.float32(32768.0)
        elif sw == 1:
            pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - np.float32(128.0)) / np.float32(128.0)
        elif sw == 4:
            pcm = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / np.float32(2147483648.0)
        else:
            raise ValueError(f"Nicht unterstützte WAV-Sample-Breite: {sw}")

        if nch > 1:
            pcm = pcm.reshape(-1, nch).mean(axis=1, dtype=np.float32)
        return pcm, int(sr)

    def _synthesize_sapi(self, text: str, raw_voice_id: str, rate_pct: int, scream: bool) -> tuple[np.ndarray, int]:
        import comtypes.client

        if self._sapi_voice is None:
            self._sapi_voice = comtypes.client.CreateObject("SAPI.SpVoice")

        voice = self._sapi_voice
        if raw_voice_id:
            q = raw_voice_id.lower()
            voices = voice.GetVoices()
            matched = None
            for idx in range(int(voices.Count)):
                tok = voices.Item(idx)
                tok_id = str(tok.Id)
                desc = str(tok.GetDescription() or "")
                if tok_id == raw_voice_id:
                    matched = tok
                    break
                if matched is None and (q in tok_id.lower() or q in desc.lower()):
                    matched = tok
            if matched is None:
                raise LookupError(f"SAPI5-Stimme nicht gefunden: {raw_voice_id}")
            voice.Voice = matched

        stream = None
        fmt = None
        try:
            stream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
            fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
            fmt.Type = 22  # SAFT22kHz16BitMono
            stream.Format = fmt
            voice.AudioOutputStream = stream
            voice.Rate = _rate_pct_to_sapi(rate_pct, scream=scream)
            voice.Volume = 100
            voice.Speak(text)
            raw_bytes = bytes(stream.GetData())
            pcm = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / np.float32(32768.0)
            return pcm, 22050
        finally:
            try:
                voice.AudioOutputStream = None
            except Exception:
                pass
            stream = None
            fmt = None

    def _synthesize_cached(self, text: str, voice_id: str, rate_pct: int, scream: bool) -> tuple[np.ndarray, int]:
        cache_key = (text, voice_id, rate_pct, scream)
        if len(text) <= _MAX_TTS_CACHE_TEXT_LEN:
            with self._tts_cache_lock:
                cached = self._tts_cache.get(cache_key)
                if cached is not None:
                    self._tts_cache.move_to_end(cache_key)
                    return cached

        vid = voice_id.strip()
        pcm: np.ndarray | None = None
        sr = 16000
        errors: list[str] = []

        if vid.startswith("sapi:"):
            try:
                pcm, sr = self._synthesize_sapi(text, vid[5:], rate_pct, scream)
            except Exception as exc:
                errors.append(f"SAPI5: {exc}")
        elif vid.startswith("winsdk:"):
            try:
                pcm, sr = self._synthesize_winsdk(text, vid[7:], rate_pct, scream)
            except Exception as exc:
                errors.append(f"OneCore: {exc}")
                try:
                    pcm, sr = self._synthesize_sapi(text, "", rate_pct, scream)
                except Exception as exc2:
                    errors.append(f"SAPI5-Fallback: {exc2}")
        elif vid.lower() in _AUTO_TOKENS:
            try:
                pcm, sr = self._synthesize_winsdk(text, "", rate_pct, scream)
            except Exception as exc:
                errors.append(f"OneCore: {exc}")
                try:
                    pcm, sr = self._synthesize_sapi(text, "", rate_pct, scream)
                except Exception as exc2:
                    errors.append(f"SAPI5-Fallback: {exc2}")
        else:
            # Unpräfigierter Stimm-Name: erst OneCore, dann SAPI5 probieren
            try:
                pcm, sr = self._synthesize_winsdk(text, vid, rate_pct, scream)
            except Exception as exc:
                errors.append(f"OneCore: {exc}")
                try:
                    pcm, sr = self._synthesize_sapi(text, vid, rate_pct, scream)
                except Exception as exc2:
                    errors.append(f"SAPI5: {exc2}")

        if pcm is None:
            raise RuntimeError(" | ".join(errors) if errors else "TTS-Synthese fehlgeschlagen")

        if len(text) <= _MAX_TTS_CACHE_TEXT_LEN and pcm.size > 0:
            with self._tts_cache_lock:
                self._tts_cache[cache_key] = (pcm.copy(), sr)
                self._tts_cache.move_to_end(cache_key)
                while len(self._tts_cache) > _MAX_TTS_CACHE_ENTRIES:
                    self._tts_cache.popitem(last=False)

        return pcm, sr

    def _run_level_envelope(self, signal: np.ndarray, sr: int, scream: bool, t_start: float) -> None:
        """Sendet exakt zur verstrichenen Wiedergabezeit synchronisierte Equalizer-Pegel."""
        if signal.size == 0 or sr <= 0:
            return
        dur = signal.size / float(sr)
        win_samples = max(1, int(sr * 0.04))
        while self._running:
            elapsed = time.perf_counter() - t_start
            if elapsed >= dur:
                break
            idx = int(elapsed * sr)
            seg = signal[idx : min(signal.size, idx + win_samples)]
            lvl = float(np.sqrt(np.mean(seg * seg))) * 2.4 if seg.size else 0.0
            self._emit_level(min(1.0, lvl), scream)
            remaining = dur - (time.perf_counter() - t_start)
            if remaining <= 0.0:
                break
            time.sleep(min(0.032, max(0.004, remaining)))

    def _worker_loop(self) -> None:
        import comtypes

        comtypes.CoInitialize()
        try:
            init_cfg = self._get_config()
            if str(init_cfg.get("virtual_device", "auto")).strip().lower() not in _OFF_TOKENS:
                unlock_virtual_microphone()
            if not bool(init_cfg.get("mic_passthrough", False)):
                cur_in, cur_out = capture_default_audio_names()
                if not cur_in or not cur_out:
                    restore_normal_audio(mute_virtual_speaker=False)

            while self._running:
                item = self._queue.get()
                if item is None:
                    break
                text, force_scream = item
                cfg = self._get_config()
                self._sync_passthrough(cfg)

                scream_auto = bool(cfg.get("scream_auto", True))
                scream = is_scream_text(text, force_scream) if scream_auto else bool(force_scream)

                voice_id = str(cfg.get("voice_id", cfg.get("voice", "auto")) or "auto")
                rate_pct = _normalize_rate_pct(cfg.get("speech_rate", cfg.get("rate", 100)))

                try:
                    raw_pcm, src_sr = self._synthesize_cached(text, voice_id, rate_pct, scream)
                except Exception as exc:
                    self._emit_error(f"TTS-Synthese fehlgeschlagen: {exc}")
                    continue

                if raw_pcm.size == 0:
                    continue

                style = str(cfg.get("robot_style", "cyber") or "cyber")
                boost_pct = int(cfg.get("scream_boost", 280))
                raw_normal = next(
                    (
                        cfg[k]
                        for k in ("normal_volume", "virtual_volume", "normal_mic_volume", "normal_pct", "mic_volume")
                        if cfg.get(k) is not None
                    ),
                    100,
                )
                normal_pct = int(raw_normal)

                hp_sig, vm_sig = apply_robot_dsp(
                    raw_pcm,
                    src_sr,
                    style=style,
                    scream=scream,
                    boost_pct=boost_pct,
                    normal_pct=normal_pct,
                )

                hp_vol = max(0.0, min(1.0, float(cfg.get("headphone_volume", 85)) / 100.0))
                hp_dev = self._resolve_device("headphone", str(cfg.get("headphone_device", "default") or "default"))
                vm_pref = str(cfg.get("virtual_device", "auto") or "auto")
                vm_dev = self._resolve_device("virtual", vm_pref)

                self._emit_speaking(True)
                try:
                    if bool(cfg.get("mic_passthrough", False)) and not self._passthru_ready.is_set():
                        self._passthru_ready.wait(timeout=0.6)

                    t_hp: threading.Thread | None = None
                    t_vm: threading.Thread | None = None
                    vm_lock_held = False
                    use_passthru = False

                    t_start = time.perf_counter()

                    if hp_dev is not None and hp_vol > 0.001:
                        t_hp = threading.Thread(
                            target=self._play_on_device,
                            args=(hp_sig, src_sr, hp_dev, hp_vol, False),
                            daemon=True,
                        )
                        t_hp.start()

                    if vm_dev is not None:
                        if self._passthru_active.is_set() and self._passthru_dev is not None:
                            use_passthru = True
                        else:
                            # Synchronisiert mit Passthrough-Start, um WDM-KS -9996 zu vermeiden
                            self._vm_stream_lock.acquire()
                            vm_lock_held = True
                            if self._passthru_active.is_set() and self._passthru_dev is not None:
                                self._vm_stream_lock.release()
                                vm_lock_held = False
                                use_passthru = True

                        if use_passthru and self._passthru_dev is not None:
                            pt_dev = self._passthru_dev
                            vm_res = resample_1d(vm_sig, src_sr, pt_dev.effective_sr)
                            self._passthru_tts_q.push(vm_res)
                        else:
                            t_vm = threading.Thread(
                                target=self._play_on_device,
                                args=(vm_sig, src_sr, vm_dev, 1.0, True),
                                daemon=True,
                            )
                            t_vm.start()

                    ref_sig = hp_sig if hp_dev is not None else vm_sig
                    audio_dur = ref_sig.size / float(max(1, src_sr))
                    self._run_level_envelope(ref_sig, src_sr, scream, t_start)

                    deadline = t_start + audio_dur + 3.0
                    if t_hp is not None:
                        t_hp.join(timeout=max(0.5, deadline - time.perf_counter()))
                    if t_vm is not None:
                        t_vm.join(timeout=max(0.5, deadline - time.perf_counter()))
                    if use_passthru:
                        self._passthru_tts_q.wait_drained(timeout=max(0.5, deadline - time.perf_counter()))
                finally:
                    if vm_lock_held:
                        try:
                            self._vm_stream_lock.release()
                        except Exception:
                            pass
                    self._emit_level(0.0, False)
                    self._emit_speaking(False)
        finally:
            if self._winsdk_synth is not None:
                try:
                    self._winsdk_synth.close()
                except Exception:
                    pass
                self._winsdk_synth = None
            if self._winsdk_loop is not None:
                try:
                    if not self._winsdk_loop.is_closed():
                        self._winsdk_loop.close()
                except Exception:
                    pass
                self._winsdk_loop = None
            self._sapi_voice = None
            gc.collect()
            comtypes.CoUninitialize()


__all__ = [
    "STYLES",
    "RobotVoiceEngine",
    "apply_robot_dsp",
    "capture_default_audio_names",
    "find_input_device",
    "find_output_device",
    "find_virtual_output_device",
    "is_scream_text",
    "list_available_voices",
    "resample_1d",
    "restore_normal_audio",
    "unlock_virtual_microphone",
]
# --- [Antigravity Ende] ---
