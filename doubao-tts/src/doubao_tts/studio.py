from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from doubao_tts.audio import validate_mp3_bytes
from doubao_tts.errors import CliError, redact
from doubao_tts.settings import Settings

CREATE_PATH = "/api/v3/tts/create"
CLONE_PATH = "/api/v3/tts/voice_clone"
DESIGN_PATH = "/api/v3/tts/voice_design"
GET_VOICE_PATH = "/api/v3/tts/get_voice"
CREATE_MODEL = "seed-audio-1.0"
PROMPT_LIMIT = 3000
DESIGN_TEXT_LIMIT = 300
DESIGN_PROMPT_LIMIT = 200
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CREATE_SAMPLE_RATES = {8000, 16000, 24000, 48000}
AUDIO_FORMATS = {"wav", "mp3", "ogg", "m4a", "aac", "pcm"}
LANGUAGE_CODES = {"cn": 0, "en": 1, "ja": 2, "es": 3, "id": 4, "pt": 5, "de": 6, "fr": 7, "ko": 8}
STATUS_NAMES = {0: "NotFound", 1: "Training", 2: "Success", 3: "Failed", 4: "Active"}
DEMO_HOST_SUFFIXES = ("bytedance.com", "volcengine.com", "bytespeech.com", "byteimg.com")


def language_code(name: str, *, allowed: set[str]) -> int:
    key = name.strip().lower()
    if key not in allowed or key not in LANGUAGE_CODES:
        raise CliError("INVALID_ARGUMENT", f"unsupported language: {name}")
    return LANGUAGE_CODES[key]


def read_media(path: Path, *, kind: str) -> tuple[bytes, str]:
    if not path.is_file():
        raise CliError("INVALID_ARGUMENT", f"{kind} file is unreadable")
    data = path.read_bytes()
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise CliError("INVALID_ARGUMENT", f"{kind} file must be between 1 byte and 10MB")
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "jpeg":
        suffix = "jpg"
    if kind == "audio" and suffix not in AUDIO_FORMATS:
        raise CliError("INVALID_ARGUMENT", "audio format must be wav, mp3, ogg, m4a, aac, or pcm")
    if kind == "image" and suffix not in {"png", "jpg", "webp"}:
        raise CliError("INVALID_ARGUMENT", "image format must be png, jpg, or webp")
    return data, suffix


def create_audio(
    settings: Settings,
    *,
    client: httpx.Client,
    prompt: str,
    speakers: list[str],
    reference_audios: list[Path],
    sample_rate: int,
    speech_rate: int,
    loudness_rate: int,
    pitch_rate: int,
    timeout: float,
) -> dict[str, Any]:
    if not prompt.strip():
        raise CliError("INVALID_ARGUMENT", "prompt is empty")
    if len(prompt) > PROMPT_LIMIT:
        raise CliError("INVALID_ARGUMENT", "prompt must be at most 3000 characters")
    if sample_rate not in CREATE_SAMPLE_RATES:
        raise CliError("INVALID_ARGUMENT", f"unsupported sample rate: {sample_rate}")
    for value, name in ((speech_rate, "speech-rate"), (loudness_rate, "loudness-rate"), (pitch_rate, "pitch-rate")):
        if value < -50 or value > 100:
            raise CliError("INVALID_ARGUMENT", f"{name} must be from -50 to 100")
    references: list[dict[str, str]] = []
    for speaker in speakers:
        if not speaker.strip():
            raise CliError("INVALID_ARGUMENT", "speaker is empty")
        references.append({"speaker": speaker})
    for audio_path in reference_audios:
        data, _fmt = read_media(audio_path, kind="audio")
        references.append({"audio_data": base64.b64encode(data).decode("ascii")})
    if len(references) > 3:
        raise CliError("INVALID_ARGUMENT", "at most 3 audio references are allowed")
    body: dict[str, Any] = {
        "model": CREATE_MODEL,
        "text_prompt": prompt,
        "audio_config": {
            "format": "mp3",
            "sample_rate": sample_rate,
            "pitch_rate": pitch_rate,
            "speech_rate": speech_rate,
            "loudness_rate": loudness_rate,
        },
    }
    if references:
        body["references"] = references
    payload, request_id, log_id = _post_json(settings, client, CREATE_PATH, body, timeout)
    audio = _audio_bytes(payload, request_id=request_id, log_id=log_id, client=client, timeout=timeout, secrets=_secrets(settings))
    original = payload.get("original_duration")
    duration = payload.get("duration")
    return {
        "audio": audio,
        "request_id": request_id,
        "log_id": log_id,
        "model": CREATE_MODEL,
        "sample_rate": sample_rate,
        "original_duration_ms": _seconds_to_ms(original),
        "provider_duration_ms": _seconds_to_ms(duration),
    }


def clone_voice(
    settings: Settings,
    *,
    client: httpx.Client,
    audio_path: Path,
    speaker_id: str,
    custom_speaker_id: str | None,
    language: str,
    demo_text: str | None,
    timeout: float,
) -> dict[str, Any]:
    data, fmt = read_media(audio_path, kind="audio")
    if demo_text is not None and (len(demo_text) < 4 or len(demo_text) > 300):
        raise CliError("INVALID_ARGUMENT", "demo text must be from 4 to 300 characters")
    body: dict[str, Any] = {
        "speaker_id": "custom_speaker_id" if custom_speaker_id else speaker_id,
        "audio": {"data": base64.b64encode(data).decode("ascii"), "format": fmt},
        "language": language_code(language, allowed=set(LANGUAGE_CODES)),
    }
    if custom_speaker_id:
        _check_custom_speaker(custom_speaker_id)
        body["custom_speaker_id"] = custom_speaker_id
    elif not speaker_id.strip():
        raise CliError("INVALID_ARGUMENT", "speaker id is empty")
    if demo_text:
        body["extra_params"] = {"demo_text": demo_text}
    payload, request_id, log_id = _post_json(settings, client, CLONE_PATH, body, timeout)
    return _voice_result(payload, request_id=request_id, log_id=log_id, client=client, timeout=timeout, secrets=_secrets(settings))


def design_voice(
    settings: Settings,
    *,
    client: httpx.Client,
    speaker_id: str,
    text: str,
    text_prompt: str | None,
    image_path: Path | None,
    image_url: str | None,
    language: str,
    timeout: float,
) -> dict[str, Any]:
    if not speaker_id.strip():
        raise CliError("INVALID_ARGUMENT", "speaker id is empty")
    if not text.strip() or len(text) > DESIGN_TEXT_LIMIT:
        raise CliError("INVALID_ARGUMENT", "preview text must be from 1 to 300 characters")
    if text_prompt is not None and len(text_prompt) > DESIGN_PROMPT_LIMIT:
        raise CliError("INVALID_ARGUMENT", "prompt must be at most 200 characters")
    prompt: dict[str, Any] = {}
    if text_prompt and text_prompt.strip():
        prompt["text_prompt"] = text_prompt
    if image_path is not None:
        image, _fmt = read_media(image_path, kind="image")
        prompt["image_prompt"] = {"image_bytes": base64.b64encode(image).decode("ascii")}
    elif image_url:
        parsed = urlparse(image_url)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise CliError("INVALID_ARGUMENT", "image URL must be HTTPS without credentials")
        prompt["image_prompt"] = {"image_url": image_url}
    if "text_prompt" not in prompt and "image_prompt" not in prompt:
        raise CliError("INVALID_ARGUMENT", "provide a text prompt or an image")
    body = {
        "speaker_id": speaker_id,
        "text": text,
        "prompt": prompt,
        "language": language_code(language, allowed={"cn", "en"}),
    }
    payload, request_id, log_id = _post_json(settings, client, DESIGN_PATH, body, timeout)
    return _voice_result(payload, request_id=request_id, log_id=log_id, client=client, timeout=timeout, secrets=_secrets(settings))


def get_voice(
    settings: Settings,
    *,
    client: httpx.Client,
    speaker_id: str,
    custom_speaker_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    body: dict[str, Any]
    if custom_speaker_id:
        _check_custom_speaker(custom_speaker_id)
        body = {"speaker_id": "custom_speaker_id", "custom_speaker_id": custom_speaker_id}
    elif speaker_id.strip():
        body = {"speaker_id": speaker_id}
    else:
        raise CliError("INVALID_ARGUMENT", "speaker id is empty")
    payload, request_id, log_id = _post_json(settings, client, GET_VOICE_PATH, body, timeout)
    return _voice_result(payload, request_id=request_id, log_id=log_id, client=client, timeout=timeout, secrets=_secrets(settings))


def _check_custom_speaker(value: str) -> None:
    if len(value) < 8 or len(value) > 256 or not value[0].isascii() or not value[0].isalpha():
        raise CliError("INVALID_ARGUMENT", "custom speaker id must start with a letter and be 8 to 256 characters")
    if value[0] in "-_" or value[-1] in "-_" or any(not (ch.isalnum() or ch in "-_") for ch in value):
        raise CliError("INVALID_ARGUMENT", "custom speaker id contains unsupported characters")


def _secrets(settings: Settings) -> list[str]:
    return [settings.api_key] if settings.api_key else []


def _post_json(
    settings: Settings,
    client: httpx.Client,
    path: str,
    body: dict[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], str, str | None]:
    if not settings.api_key:
        raise CliError("CONFIG_MISSING", "API key is missing")
    request_id = str(uuid.uuid4())
    try:
        response = client.post(
            settings.base_url.rstrip("/") + path,
            headers={"X-Api-Key": settings.api_key, "X-Api-Request-Id": request_id},
            json=body,
            timeout=httpx.Timeout(connect=min(10.0, timeout), read=timeout, write=timeout, pool=min(10.0, timeout)),
        )
    except httpx.TimeoutException as exc:
        raise CliError("NETWORK_TEMPORARY", "request timed out", retryable=True, may_have_been_billed=True, request_id=request_id) from exc
    except httpx.ConnectError as exc:
        raise CliError("NETWORK_TEMPORARY", "connection failed", retryable=True, request_id=request_id) from exc
    except httpx.TransportError as exc:
        raise CliError("NETWORK_TEMPORARY", "connection failed", retryable=True, may_have_been_billed=True, request_id=request_id) from exc
    log_id = response.headers.get("x-tt-logid")
    if response.status_code == 429:
        raise CliError("PROVIDER_RATE_LIMIT", "Provider rate limit", retryable=True, request_id=request_id, log_id=log_id)
    if response.status_code in {401, 403}:
        raise CliError("AUTH_OR_RESOURCE", "Voice or resource is not authorized", request_id=request_id, log_id=log_id)
    try:
        payload = response.json()
    except ValueError as exc:
        raise CliError("PROVIDER_ERROR", f"Provider HTTP {response.status_code}", request_id=request_id, log_id=log_id) from exc
    if not isinstance(payload, dict):
        raise CliError("PROVIDER_ERROR", "provider response is not an object", request_id=request_id, log_id=log_id)
    _raise_provider_code(payload, http_status=response.status_code, request_id=request_id, log_id=log_id, secrets=_secrets(settings))
    return payload, request_id, log_id


def _raise_provider_code(
    payload: dict[str, Any],
    *,
    http_status: int,
    request_id: str,
    log_id: str | None,
    secrets: list[str],
) -> None:
    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool) or code in {0, 20000000}:
        if http_status >= 400:
            raise CliError("PROVIDER_ERROR", f"Provider HTTP {http_status}", request_id=request_id, log_id=log_id)
        return
    message = redact(str(payload.get("message") or ""), secrets)
    if code == 45001001:
        raise CliError("INVALID_ARGUMENT", message or "Provider rejected the parameters", request_id=request_id, log_id=log_id)
    if code in {45000000, 45001123}:
        raise CliError("AUTH_OR_RESOURCE", message or "Voice or resource is not authorized", request_id=request_id, log_id=log_id)
    if code >= 55000000:
        raise CliError("NETWORK_TEMPORARY", message or "Provider temporary error", retryable=True, may_have_been_billed=True, request_id=request_id, log_id=log_id)
    raise CliError("PROVIDER_ERROR", message or f"Provider error {code}", request_id=request_id, log_id=log_id)


def _audio_bytes(
    payload: dict[str, Any],
    *,
    request_id: str,
    log_id: str | None,
    client: httpx.Client,
    timeout: float,
    secrets: list[str],
) -> bytes:
    encoded = payload.get("audio")
    if isinstance(encoded, str) and encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise CliError("INVALID_AUDIO", "provider audio is not valid base64", request_id=request_id, log_id=log_id) from exc
    else:
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise CliError("INVALID_AUDIO", "provider returned no audio", request_id=request_id, log_id=log_id)
        data = _download(client, url, timeout=timeout, request_id=request_id, log_id=log_id)
    if secrets:
        pass
    validate_mp3_bytes(data)
    return data


def _voice_result(
    payload: dict[str, Any],
    *,
    request_id: str,
    log_id: str | None,
    client: httpx.Client,
    timeout: float,
    secrets: list[str],
) -> dict[str, Any]:
    status = payload.get("status")
    if not isinstance(status, int) or isinstance(status, bool):
        status = None
    demo = _first_demo(payload)
    audio: bytes | None = None
    if demo and status in {2, 4}:
        try:
            downloaded = _download(client, demo, timeout=timeout, request_id=request_id, log_id=log_id)
            validate_mp3_bytes(downloaded)
            audio = downloaded
        except CliError:
            audio = None
    result = {
        "request_id": request_id,
        "log_id": log_id,
        "speaker_id": payload.get("speaker_id"),
        "status": status,
        "status_name": STATUS_NAMES.get(status, "Unknown") if status is not None else None,
        "available_training_times": payload.get("available_training_times"),
        "language": payload.get("language"),
        "audio": audio,
        "ready_for_tts": status in {2, 4},
    }
    if secrets and isinstance(result["speaker_id"], str):
        result["speaker_id"] = redact(result["speaker_id"], secrets)
    if status == 3:
        raise CliError("PROVIDER_ERROR", "voice training failed", request_id=request_id, log_id=log_id)
    return result


def _first_demo(payload: dict[str, Any]) -> str | None:
    direct = payload.get("demo_audio")
    if isinstance(direct, str) and direct:
        return direct
    rows = payload.get("speaker_status")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("demo_audio"), str) and row["demo_audio"]:
                return row["demo_audio"]
    return None


def _download(client: httpx.Client, url: str, *, timeout: float, request_id: str, log_id: str | None) -> bytes:
    _check_public_url(url)
    try:
        response = client.get(url, timeout=httpx.Timeout(connect=min(10.0, timeout), read=timeout, write=30.0, pool=10.0), follow_redirects=False)
    except httpx.HTTPError as exc:
        raise CliError("INVALID_AUDIO", "provider demo audio could not be downloaded", request_id=request_id, log_id=log_id) from exc
    if response.status_code != 200 or not response.content:
        raise CliError("INVALID_AUDIO", "provider demo audio could not be downloaded", request_id=request_id, log_id=log_id)
    return response.content


def _check_public_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or not host:
        raise CliError("INVALID_ARGUMENT", "URL must be HTTPS without credentials")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in DEMO_HOST_SUFFIXES):
        raise CliError("INVALID_ARGUMENT", "URL host is not an allowed Doubao host")


def _seconds_to_ms(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return int(round(float(value) * 1000))
