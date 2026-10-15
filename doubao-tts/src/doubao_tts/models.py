from __future__ import annotations

import json
from hashlib import sha256

from pydantic import BaseModel, Field, field_validator

from doubao_tts.errors import CliError
from doubao_tts.settings import ALLOWED_SAMPLE_RATES, validate_resource


class ResolvedVoice(BaseModel):
    voice_id: str
    resource_id: str
    speed: int = 0
    volume: int = 0
    sample_rate: int = 24000
    emotion: str | None = None
    alias: str | None = None


def check_speed(speed: int) -> int:
    if isinstance(speed, bool) or not isinstance(speed, int) or speed < -50 or speed > 100:
        raise CliError("INVALID_ARGUMENT", "speed must be an integer from -50 to 100")
    return speed


def check_volume(volume: int) -> int:
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < -50 or volume > 100:
        raise CliError("INVALID_ARGUMENT", "volume must be an integer from -50 to 100")
    return volume


def check_sample_rate(sample_rate: int) -> int:
    if isinstance(sample_rate, bool) or sample_rate not in ALLOWED_SAMPLE_RATES:
        raise CliError("INVALID_ARGUMENT", f"Unsupported sample rate: {sample_rate}")
    return sample_rate


def check_emotion(emotion: str | None) -> str | None:
    if emotion is None or emotion == "":
        return None
    if not all(ch.isalnum() or ch in "_-" for ch in emotion) or not emotion.isascii() or len(emotion) > 32:
        raise CliError("INVALID_ARGUMENT", "emotion contains unsupported characters")
    return emotion


def require_text(text: str) -> str:
    if text.strip() == "":
        raise CliError("INVALID_ARGUMENT", "text is empty")
    return text


def fingerprint(
    *,
    text: str,
    voice_id: str,
    resource_id: str,
    speed: int,
    volume: int,
    sample_rate: int,
    emotion: str | None,
) -> str:
    payload = {
        "schema": 1,
        "text": text,
        "voice_id": voice_id,
        "resource_id": resource_id,
        "speed": speed,
        "volume": volume,
        "sample_rate": sample_rate,
        "emotion": emotion or "",
        "format": "mp3",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


class VoicePreset(BaseModel):
    voice_id: str = Field(min_length=1)
    resource_id: str
    speed: int = 0
    volume: int = 0
    sample_rate: int = 24000

    @field_validator("resource_id")
    @classmethod
    def _resource(cls, value: str) -> str:
        return validate_resource(value)

    @field_validator("speed")
    @classmethod
    def _speed(cls, value: int) -> int:
        return check_speed(value)

    @field_validator("volume")
    @classmethod
    def _volume(cls, value: int) -> int:
        return check_volume(value)

    @field_validator("sample_rate")
    @classmethod
    def _rate(cls, value: int) -> int:
        return check_sample_rate(value)


class VoiceFile(BaseModel):
    version: int = 1
    voices: dict[str, VoicePreset] = Field(default_factory=dict)


class ManifestTask(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    text: str | None = None
    text_file: str | None = None
    output: str = Field(min_length=1)
    voice: str | None = None
    voice_id: str | None = None
    resource: str | None = None
    speed: int | None = None
    volume: int | None = None
    sample_rate: int | None = None
    emotion: str | None = None

    model_config = {"extra": "forbid"}


class ManifestDefaults(BaseModel):
    voice: str | None = None
    voice_id: str | None = None
    resource: str | None = None
    speed: int | None = None
    volume: int | None = None
    sample_rate: int | None = None
    emotion: str | None = None

    model_config = {"extra": "forbid"}


class Manifest(BaseModel):
    version: int
    defaults: ManifestDefaults = Field(default_factory=ManifestDefaults)
    tasks: list[ManifestTask] = Field(min_length=1)

    model_config = {"extra": "forbid"}
