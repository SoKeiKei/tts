from __future__ import annotations

import json
import os

from pydantic import ValidationError

from minimax_tts.errors import CliError
from minimax_tts.models import (
    ResolvedVoice,
    VoiceFile,
    VoicePreset,
    check_bitrate,
    check_emotion,
    check_language,
    check_pitch,
    check_sample_rate,
    check_speed,
    check_volume,
)
from minimax_tts.settings import Settings, validate_model


def voices_path(settings: Settings):
    return settings.config_dir / "voices.json"


def load_voices(settings: Settings) -> VoiceFile:
    path = voices_path(settings)
    if not path.exists():
        return VoiceFile()
    try:
        return VoiceFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CliError("OUTPUT_IO", "voices.json is unreadable or invalid") from exc


def save_voices(settings: Settings, store: VoiceFile) -> None:
    path = voices_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        partial.write_text(json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CliError("OUTPUT_IO", "failed to write voices.json") from exc


def add_voice(settings: Settings, name: str, preset: VoicePreset) -> VoicePreset:
    if not name or any(ch in name for ch in "/\\"):
        raise CliError("INVALID_ARGUMENT", "voice alias is invalid")
    check_emotion(preset.emotion, preset.model)
    store = load_voices(settings)
    store.voices[name] = preset
    save_voices(settings, store)
    return preset


def remove_voice(settings: Settings, name: str) -> None:
    store = load_voices(settings)
    if name not in store.voices:
        raise CliError("INVALID_ARGUMENT", f"voice alias not found: {name}")
    del store.voices[name]
    save_voices(settings, store)


def show_voice(settings: Settings, name: str) -> VoicePreset:
    preset = load_voices(settings).voices.get(name)
    if preset is None:
        raise CliError("INVALID_ARGUMENT", f"voice alias not found: {name}")
    return preset


def resolve_voice(
    settings: Settings,
    *,
    alias: str | None,
    voice_id: str | None,
    model: str | None,
    speed: float | None,
    volume: float | None,
    pitch: int | None,
    emotion: str | None,
    language_boost: str | None,
    sample_rate: int | None,
    bitrate: int | None,
) -> ResolvedVoice:
    if (alias is None) == (voice_id is None):
        raise CliError("INVALID_ARGUMENT", "choose exactly one of --voice or --voice-id")
    if alias is not None:
        preset = show_voice(settings, alias)
        chosen_model = validate_model(model or preset.model)
        resolved = ResolvedVoice(
            voice_id=preset.voice_id,
            model=chosen_model,
            speed=preset.speed if speed is None else check_speed(speed),
            volume=preset.volume if volume is None else check_volume(volume),
            pitch=preset.pitch if pitch is None else check_pitch(pitch),
            emotion=check_emotion(emotion if emotion is not None else preset.emotion, chosen_model),
            language_boost=check_language(language_boost or preset.language_boost),
            sample_rate=preset.sample_rate if sample_rate is None else check_sample_rate(sample_rate),
            bitrate=preset.bitrate if bitrate is None else check_bitrate(bitrate),
            alias=alias,
        )
        return resolved
    assert voice_id is not None
    if not voice_id.strip():
        raise CliError("INVALID_ARGUMENT", "voice id is empty")
    chosen_model = validate_model(model or settings.model)
    return ResolvedVoice(
        voice_id=voice_id,
        model=chosen_model,
        speed=1.0 if speed is None else check_speed(speed),
        volume=1.0 if volume is None else check_volume(volume),
        pitch=0 if pitch is None else check_pitch(pitch),
        emotion=check_emotion(emotion, chosen_model),
        language_boost=check_language(language_boost or "auto"),
        sample_rate=32000 if sample_rate is None else check_sample_rate(sample_rate),
        bitrate=128000 if bitrate is None else check_bitrate(bitrate),
        alias=None,
    )
