from __future__ import annotations

import json
from hashlib import sha256

from pydantic import BaseModel, Field, field_validator

from minimax_tts.errors import CliError
from minimax_tts.settings import (
    BITRATES,
    EMOTIONS,
    EMOTIONS_28,
    LANGUAGES,
    MODELS_28,
    SAMPLE_RATES,
    TEXT_LIMIT,
    validate_model,
)


class ResolvedVoice(BaseModel):
    voice_id: str
    model: str
    speed: float = 1.0
    volume: float = 1.0
    pitch: int = 0
    emotion: str | None = None
    language_boost: str = "auto"
    sample_rate: int = 32000
    bitrate: int = 128000
    alias: str | None = None


def _number(value: float, *, low: float, high: float, name: str, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CliError("INVALID_ARGUMENT", f"{name} is not a number")
    if integer and not isinstance(value, int):
        raise CliError("INVALID_ARGUMENT", f"{name} must be an integer")
    if value < low or value > high:
        raise CliError("INVALID_ARGUMENT", f"{name} is outside the allowed range")
    if name == "volume" and value <= 0:
        raise CliError("INVALID_ARGUMENT", "volume must be greater than 0 and at most 10")
    return int(value) if integer else round(float(value), 4)


def check_speed(speed: float) -> float:
    return float(_number(speed, low=0.5, high=2.0, name="speed"))


def check_volume(volume: float) -> float:
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        raise CliError("INVALID_ARGUMENT", "volume is not a number")
    if volume <= 0 or volume > 10:
        raise CliError("INVALID_ARGUMENT", "volume must be greater than 0 and at most 10")
    return round(float(volume), 4)


def check_pitch(pitch: int) -> int:
    return int(_number(pitch, low=-12, high=12, name="pitch", integer=True))


def check_sample_rate(sample_rate: int) -> int:
    if isinstance(sample_rate, bool) or sample_rate not in SAMPLE_RATES:
        raise CliError("INVALID_ARGUMENT", f"Unsupported sample rate: {sample_rate}")
    return sample_rate


def check_bitrate(bitrate: int) -> int:
    if isinstance(bitrate, bool) or bitrate not in BITRATES:
        raise CliError("INVALID_ARGUMENT", f"Unsupported bitrate: {bitrate}")
    return bitrate


def check_language(language: str) -> str:
    if language not in LANGUAGES:
        raise CliError("INVALID_ARGUMENT", f"Unsupported language boost: {language}")
    return language


def check_emotion(emotion: str | None, model: str) -> str | None:
    if emotion is None or emotion == "":
        return None
    allowed = EMOTIONS_28 if model in MODELS_28 else EMOTIONS
    if emotion not in allowed:
        raise CliError("INVALID_ARGUMENT", f"emotion is not supported by {model}")
    return emotion


def require_text(text: str) -> str:
    if text.strip() == "":
        raise CliError("INVALID_ARGUMENT", "text is empty")
    if len(text) >= TEXT_LIMIT:
        raise CliError("TEXT_TOO_LONG", "text must be shorter than 10000 characters")
    return text


def fingerprint(voice: ResolvedVoice, text: str) -> str:
    payload = {
        "schema": 1,
        "text": text,
        "voice_id": voice.voice_id,
        "model": voice.model,
        "speed": f"{voice.speed:.4f}",
        "volume": f"{voice.volume:.4f}",
        "pitch": voice.pitch,
        "emotion": voice.emotion or "",
        "language_boost": voice.language_boost,
        "sample_rate": voice.sample_rate,
        "bitrate": voice.bitrate,
        "format": "mp3",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


class VoicePreset(BaseModel):
    voice_id: str = Field(min_length=1)
    model: str = "speech-2.8-hd"
    speed: float = 1.0
    volume: float = 1.0
    pitch: int = 0
    emotion: str | None = None
    language_boost: str = "auto"
    sample_rate: int = 32000
    bitrate: int = 128000

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        return validate_model(value)

    @field_validator("speed")
    @classmethod
    def _speed(cls, value: float) -> float:
        return check_speed(value)

    @field_validator("volume")
    @classmethod
    def _volume(cls, value: float) -> float:
        return check_volume(value)

    @field_validator("pitch")
    @classmethod
    def _pitch(cls, value: int) -> int:
        return check_pitch(value)

    @field_validator("sample_rate")
    @classmethod
    def _rate(cls, value: int) -> int:
        return check_sample_rate(value)

    @field_validator("bitrate")
    @classmethod
    def _bitrate(cls, value: int) -> int:
        return check_bitrate(value)

    @field_validator("language_boost")
    @classmethod
    def _language(cls, value: str) -> str:
        return check_language(value)


class VoiceFile(BaseModel):
    version: int = 1
    voices: dict[str, VoicePreset] = Field(default_factory=dict)


class ManifestDefaults(BaseModel):
    voice: str | None = None
    voice_id: str | None = None
    model: str | None = None
    speed: float | None = None
    volume: float | None = None
    pitch: int | None = None
    emotion: str | None = None
    language_boost: str | None = None
    sample_rate: int | None = None
    bitrate: int | None = None

    model_config = {"extra": "forbid"}


class ManifestTask(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    text: str | None = None
    text_file: str | None = None
    output: str = Field(min_length=1)
    voice: str | None = None
    voice_id: str | None = None
    model: str | None = None
    speed: float | None = None
    volume: float | None = None
    pitch: int | None = None
    emotion: str | None = None
    language_boost: str | None = None
    sample_rate: int | None = None
    bitrate: int | None = None

    model_config = {"extra": "forbid"}


class Manifest(BaseModel):
    version: int
    defaults: ManifestDefaults = Field(default_factory=ManifestDefaults)
    tasks: list[ManifestTask] = Field(min_length=1)

    model_config = {"extra": "forbid"}
