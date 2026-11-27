# --- [Antigravity Start] PopWin TTS Audio-Engine Paket ---
"""Hocheffiziente Audio- & Dual-TTS-Engine für PopWin (TexttoSpeech for Windows)."""
from __future__ import annotations

from tts.audio import (
    STYLES,
    RobotVoiceEngine,
    apply_robot_dsp,
    capture_default_audio_names,
    find_input_device,
    find_output_device,
    find_virtual_output_device,
    is_scream_text,
    list_available_voices,
    resample_1d,
    restore_normal_audio,
    unlock_virtual_microphone,
)

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
