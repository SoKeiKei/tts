from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from doubao_tts.errors import CliError
from doubao_tts.models import (
    ResolvedVoice,
    VoiceFile,
    VoicePreset,
    check_emotion,
    check_sample_rate,
    check_speed,
    check_volume,
)
from doubao_tts.settings import Settings, validate_resource


def voices_path(settings: Settings) -> Path:
    return settings.config_dir / "voices.json"


def load_voices(settings: Settings) -> VoiceFile:
    path = voices_path(settings)
    if not path.exists():
        return VoiceFile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return VoiceFile.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise CliError("OUTPUT_IO", "voices.json is unreadable or invalid") from exc


def save_voices(settings: Settings, store: VoiceFile) -> None:
    path = voices_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = store.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    partial = path.with_name(path.name + ".partial")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CliError("OUTPUT_IO", "failed to write voices.json") from exc


def add_voice(
    settings: Settings,
    name: str,
    *,
    voice_id: str,
    resource_id: str,
    speed: int,
    volume: int,
    sample_rate: int,
) -> VoicePreset:
    if not name or any(ch in name for ch in "/\\"):
        raise CliError("INVALID_ARGUMENT", "voice alias is invalid")
    preset = VoicePreset(
        voice_id=voice_id,
        resource_id=validate_resource(resource_id),
        speed=check_speed(speed),
        volume=check_volume(volume),
        sample_rate=check_sample_rate(sample_rate),
    )
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
    store = load_voices(settings)
    preset = store.voices.get(name)
    if preset is None:
        raise CliError("INVALID_ARGUMENT", f"voice alias not found: {name}")
    return preset


def resolve_voice(
    settings: Settings,
    *,
    alias: str | None,
    voice_id: str | None,
    resource_id: str | None,
    speed: int | None,
    volume: int | None,
    sample_rate: int | None,
    emotion: str | None,
) -> ResolvedVoice:
    if (alias is None) == (voice_id is None):
        raise CliError("INVALID_ARGUMENT", "choose exactly one of --voice or --voice-id")
    if alias is not None:
        preset = show_voice(settings, alias)
        chosen_resource = preset.resource_id
        if resource_id is not None and resource_id != preset.resource_id:
            raise CliError(
                "INVALID_ARGUMENT",
                "explicit resource does not match the voice preset; refusing to change resource",
            )
        resolved = ResolvedVoice(
            voice_id=preset.voice_id,
            resource_id=chosen_resource,
            speed=preset.speed if speed is None else check_speed(speed),
            volume=preset.volume if volume is None else check_volume(volume),
            sample_rate=preset.sample_rate if sample_rate is None else check_sample_rate(sample_rate),
            emotion=check_emotion(emotion),
            alias=alias,
        )
        return resolved
    assert voice_id is not None
    if not voice_id.strip():
        raise CliError("INVALID_ARGUMENT", "voice id is empty")
    chosen_resource = validate_resource(resource_id or settings.resource_id)
    return ResolvedVoice(
        voice_id=voice_id,
        resource_id=chosen_resource,
        speed=0 if speed is None else check_speed(speed),
        volume=0 if volume is None else check_volume(volume),
        sample_rate=24000 if sample_rate is None else check_sample_rate(sample_rate),
        emotion=check_emotion(emotion),
        alias=None,
    )
