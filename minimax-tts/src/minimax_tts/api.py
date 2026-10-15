from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from minimax_tts.audio import validate_mp3_bytes
from minimax_tts.errors import CliError, redact
from minimax_tts.models import ResolvedVoice
from minimax_tts.settings import ENDPOINT_PATH, Settings

_sleep = time.sleep


@dataclass
class SynthAudio:
    audio: bytes
    request_id: str
    trace_id: str | None
    usage_characters: int | None
    voice: ResolvedVoice


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return min(max(seconds, 0.0), 30.0)


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2**attempt) + random.uniform(0, 0.25))


def map_status_code(code: int, message: str) -> CliError:
    text = message.lower()
    if code in {1002, 1039} or "rate limit" in text:
        return CliError("PROVIDER_RATE_LIMIT", "Provider rate limit", retryable=True)
    if code in {1000, 1001, 1024}:
        return CliError("NETWORK_TEMPORARY", "Provider temporary error", retryable=True, may_have_been_billed=True)
    if code == 1004 or "auth" in text:
        return CliError("AUTH_OR_RESOURCE", "Voice or model is not authorized")
    if code == 1008 or "balance" in text or "quota" in text:
        return CliError("QUOTA_EXHAUSTED", "Provider quota is exhausted")
    if code in {2013, 1042}:
        return CliError("INVALID_ARGUMENT", "Provider rejected the parameters")
    return CliError("PROVIDER_ERROR", f"Provider error {code}")


def map_http_status(status: int, retry_after: float | None) -> CliError | None:
    if status == 429:
        return CliError("PROVIDER_RATE_LIMIT", "Provider rate limit", retryable=True, retry_after=retry_after)
    if status in {401, 403}:
        return CliError("AUTH_OR_RESOURCE", "Voice or model is not authorized")
    if status == 402:
        return CliError("QUOTA_EXHAUSTED", "Provider quota is exhausted")
    if status in {408, 409, 425} or 500 <= status <= 599:
        return CliError(
            "NETWORK_TEMPORARY",
            "Provider temporary error",
            retryable=True,
            may_have_been_billed=True,
            retry_after=retry_after,
        )
    if status >= 400:
        return CliError("PROVIDER_ERROR", f"Provider HTTP {status}")
    return None


def decode_audio(payload: dict[str, Any], *, secrets: list[str]) -> tuple[bytes, str | None, int | None]:
    base = payload.get("base_resp") if isinstance(payload.get("base_resp"), dict) else {}
    code = base.get("status_code")
    message = redact(str(base.get("status_msg") or ""), secrets)
    trace = payload.get("trace_id")
    trace_id = trace if isinstance(trace, str) and trace else None
    if code != 0:
        if not isinstance(code, int):
            raise CliError("PROVIDER_ERROR", "provider response has no status code", provider_trace_id=trace_id)
        error = map_status_code(code, message)
        error.provider_trace_id = trace_id
        raise error
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("status") != 2:
        raise CliError("INVALID_AUDIO", "provider audio is incomplete", provider_trace_id=trace_id, may_have_been_billed=True)
    audio_hex = data.get("audio")
    if not isinstance(audio_hex, str) or not audio_hex or len(audio_hex) % 2 != 0:
        raise CliError("INVALID_AUDIO", "provider audio hex is invalid", provider_trace_id=trace_id, may_have_been_billed=True)
    try:
        audio = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise CliError(
            "INVALID_AUDIO",
            "provider audio hex is invalid",
            provider_trace_id=trace_id,
            may_have_been_billed=True,
        ) from exc
    if not audio:
        raise CliError("INVALID_AUDIO", "provider returned no audio", provider_trace_id=trace_id, may_have_been_billed=True)
    try:
        validate_mp3_bytes(audio)
    except CliError as exc:
        exc.may_have_been_billed = True
        exc.provider_trace_id = trace_id
        raise
    extra = payload.get("extra_info") if isinstance(payload.get("extra_info"), dict) else {}
    usage = extra.get("usage_characters")
    usage_characters = usage if isinstance(usage, int) and not isinstance(usage, bool) else None
    return audio, trace_id, usage_characters


def build_body(text: str, voice: ResolvedVoice) -> dict[str, Any]:
    voice_setting: dict[str, Any] = {
        "voice_id": voice.voice_id,
        "speed": voice.speed,
        "vol": voice.volume,
        "pitch": voice.pitch,
    }
    if voice.emotion:
        voice_setting["emotion"] = voice.emotion
    return {
        "model": voice.model,
        "text": text,
        "stream": False,
        "output_format": "hex",
        "language_boost": voice.language_boost,
        "voice_setting": voice_setting,
        "audio_setting": {
            "sample_rate": voice.sample_rate,
            "bitrate": voice.bitrate,
            "format": "mp3",
            "channel": 1,
        },
    }


def synthesize(
    settings: Settings,
    *,
    text: str,
    voice: ResolvedVoice,
    client: httpx.Client,
    retries: int,
    timeout: float,
    request_id: str | None = None,
) -> SynthAudio:
    secrets = [settings.api_key] if settings.api_key else []
    attempts = max(retries, 0) + 1
    last: CliError | None = None
    billed = False
    for attempt in range(attempts):
        current_id = request_id or str(uuid.uuid4())
        try:
            return _once(settings, text=text, voice=voice, client=client, timeout=timeout, request_id=current_id, secrets=secrets)
        except CliError as exc:
            exc.request_id = exc.request_id or current_id
            billed = billed or exc.may_have_been_billed
            exc.may_have_been_billed = billed
            last = exc
            if not exc.retryable or exc.may_have_been_billed or attempt + 1 >= attempts:
                raise
            _sleep(exc.retry_after if exc.retry_after is not None else _backoff(attempt))
    assert last is not None
    raise last


def _once(
    settings: Settings,
    *,
    text: str,
    voice: ResolvedVoice,
    client: httpx.Client,
    timeout: float,
    request_id: str,
    secrets: list[str],
) -> SynthAudio:
    if not settings.api_key:
        raise CliError("CONFIG_MISSING", "API key is missing", request_id=request_id)
    url = settings.base_url.rstrip("/") + ENDPOINT_PATH
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    try:
        response = client.post(
            url,
            headers=headers,
            json=build_body(text, voice),
            timeout=httpx.Timeout(connect=min(10.0, timeout), read=timeout, write=min(30.0, timeout), pool=min(10.0, timeout)),
        )
    except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
        raise CliError("NETWORK_TEMPORARY", "connection failed", retryable=True, may_have_been_billed=False, request_id=request_id) from exc
    except httpx.TimeoutException as exc:
        raise CliError("NETWORK_TEMPORARY", "request timed out", retryable=True, may_have_been_billed=True, request_id=request_id) from exc
    except httpx.TransportError as exc:
        raise CliError("NETWORK_TEMPORARY", "connection failed", retryable=True, may_have_been_billed=True, request_id=request_id) from exc
    status_error = map_http_status(response.status_code, _retry_after(response.headers))
    if status_error is not None:
        status_error.request_id = request_id
        raise status_error
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("INVALID_AUDIO", "provider response is not JSON", request_id=request_id, may_have_been_billed=True) from exc
    if not isinstance(payload, dict):
        raise CliError("INVALID_AUDIO", "provider response is not JSON", request_id=request_id, may_have_been_billed=True)
    audio, trace_id, usage = decode_audio(payload, secrets=secrets)
    return SynthAudio(audio=audio, request_id=request_id, trace_id=trace_id, usage_characters=usage, voice=voice)
