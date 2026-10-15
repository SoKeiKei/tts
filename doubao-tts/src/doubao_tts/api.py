from __future__ import annotations

import base64
import codecs
import json
import random
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from doubao_tts.audio import validate_mp3_bytes
from doubao_tts.errors import CliError, redact
from doubao_tts.models import ResolvedVoice
from doubao_tts.settings import ENDPOINT_PATH, Settings

_sleep = time.sleep
SUCCESS_END = 20000000


@dataclass
class SynthAudio:
    audio: bytes
    request_id: str
    log_id: str | None
    text_words: int | None
    voice: ResolvedVoice


def iter_json_objects(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    utf8 = codecs.getincrementaldecoder("utf-8")()
    buf = ""
    for chunk in chunks:
        buf += utf8.decode(chunk)
        yield from _drain(buf, decoder)
        buf = _remainder(buf, decoder)
    buf += utf8.decode(b"", final=True)
    yield from _drain(buf, decoder, final=True)


def _skip_prefix(buf: str) -> str:
    while True:
        buf = buf.lstrip()
        if buf.startswith("data:"):
            buf = buf[5:]
            continue
        if buf.startswith("event:") or buf.startswith(":"):
            newline = buf.find("\n")
            if newline < 0:
                return buf
            buf = buf[newline + 1 :]
            continue
        return buf


def _drain(buf: str, decoder: json.JSONDecoder, *, final: bool = False) -> Iterator[dict[str, Any]]:
    working = _skip_prefix(buf)
    while working:
        try:
            obj, index = decoder.raw_decode(working)
        except json.JSONDecodeError:
            if final and working.strip():
                raise CliError("INVALID_AUDIO", "incomplete JSON frame from provider") from None
            return
        if not isinstance(obj, dict):
            raise CliError("INVALID_AUDIO", "provider frame is not a JSON object")
        yield obj
        working = _skip_prefix(working[index:])
        if not working:
            return


def _remainder(buf: str, decoder: json.JSONDecoder) -> str:
    working = _skip_prefix(buf)
    while working:
        try:
            _obj, index = decoder.raw_decode(working)
        except json.JSONDecodeError:
            return working
        working = _skip_prefix(working[index:])
    return ""


def _code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def map_provider_failure(code: int, message: str) -> CliError:
    text = (message or "").lower()
    if code == 40402003 or "textlimit" in text or "exceed max limit" in text:
        return CliError("INVALID_ARGUMENT", "Provider rejected the text length")
    if "concurrency" in text or "rate" in text or "qps" in text:
        return CliError("PROVIDER_RATE_LIMIT", "Provider rate limit", retryable=True)
    if code == 45000000 or "permission" in text or "access denied" in text or "auth" in text:
        return CliError("AUTH_OR_RESOURCE", "Voice or resource is not authorized")
    if "quota" in text or "balance" in text or "余额" in message:
        return CliError("QUOTA_EXHAUSTED", "Provider quota is exhausted")
    if code == 55000000 or code >= 55000000:
        return CliError(
            "NETWORK_TEMPORARY",
            "Provider temporary error",
            retryable=True,
            may_have_been_billed=True,
        )
    return CliError("PROVIDER_ERROR", f"Provider error {code}")


def map_http_status(status: int, retry_after: float | None) -> CliError | None:
    if status == 429:
        return CliError(
            "PROVIDER_RATE_LIMIT",
            "Provider rate limit",
            retryable=True,
            retry_after=retry_after,
        )
    if status in {401, 403}:
        return CliError("AUTH_OR_RESOURCE", "Voice or resource is not authorized")
    if status == 402:
        return CliError("QUOTA_EXHAUSTED", "Provider quota is exhausted")
    if status in {408, 409, 425} or 500 <= status <= 599:
        return CliError(
            "NETWORK_TEMPORARY",
            "Provider temporary error",
            retryable=True,
            may_have_been_billed=status != 408,
            retry_after=retry_after,
        )
    if status >= 400:
        return CliError("PROVIDER_ERROR", f"Provider HTTP {status}")
    return None


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0:
        return 0
    return min(seconds, 30.0)


def assemble_audio(frames: Iterable[dict[str, Any]], *, secrets: list[str]) -> tuple[bytes, int | None]:
    parts: list[bytes] = []
    saw_end = False
    text_words: int | None = None
    for frame in frames:
        code = _code(frame.get("code"))
        message = redact(str(frame.get("message") or ""), secrets)
        if code is None:
            raise CliError("INVALID_AUDIO", "provider frame has no code", may_have_been_billed=True)
        if code not in (0, SUCCESS_END):
            raise map_provider_failure(code, message).with_context()
        usage = frame.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("text_words"), int):
            text_words = usage["text_words"]
        data = frame.get("data")
        if isinstance(data, str) and data:
            try:
                parts.append(base64.b64decode(data, validate=True))
            except (ValueError, base64.binascii.Error) as exc:
                raise CliError(
                    "INVALID_AUDIO",
                    "provider audio chunk is not valid base64",
                    may_have_been_billed=True,
                ) from exc
        if code == SUCCESS_END:
            saw_end = True
            break
    if not saw_end:
        raise CliError(
            "INVALID_AUDIO",
            "missing success end frame",
            may_have_been_billed=bool(parts),
        )
    audio = b"".join(parts)
    if not audio:
        raise CliError("INVALID_AUDIO", "provider returned no audio", may_have_been_billed=True)
    try:
        validate_mp3_bytes(audio)
    except CliError as exc:
        exc.may_have_been_billed = True
        raise
    return audio, text_words


def _backoff(attempt: int) -> float:
    base = 0.5 * (2**attempt)
    return min(30.0, base + random.uniform(0, 0.25))


def synthesize(
    settings: Settings,
    *,
    text: str,
    voice: ResolvedVoice,
    client: httpx.Client,
    retries: int,
    timeout: float,
    request_id: str | None = None,
    secrets: list[str] | None = None,
) -> SynthAudio:
    secret_values = [settings.api_key or "", *(secrets or [])]
    secret_values = [item for item in secret_values if item]
    attempts = max(retries, 0) + 1
    last: CliError | None = None
    billed = False
    for attempt in range(attempts):
        current_id = request_id or str(uuid.uuid4())
        try:
            return _once(
                settings,
                text=text,
                voice=voice,
                client=client,
                timeout=timeout,
                request_id=current_id,
                secrets=secret_values,
            )
        except CliError as exc:
            exc.request_id = exc.request_id or current_id
            billed = billed or exc.may_have_been_billed
            exc.may_have_been_billed = billed
            last = exc
            if not exc.retryable or exc.may_have_been_billed or attempt + 1 >= attempts:
                raise
            delay = exc.retry_after if exc.retry_after is not None else _backoff(attempt)
            _sleep(delay)
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
    audio_params: dict[str, Any] = {
        "format": "mp3",
        "sample_rate": voice.sample_rate,
        "speech_rate": voice.speed,
        "loudness_rate": voice.volume,
    }
    if voice.emotion:
        audio_params["emotion"] = voice.emotion
    body = {
        "user": {"uid": "doubao-tts"},
        "req_params": {
            "text": text,
            "speaker": voice.voice_id,
            "audio_params": audio_params,
        },
    }
    headers = {
        "X-Api-Key": settings.api_key,
        "X-Api-Resource-Id": voice.resource_id,
        "X-Api-Request-Id": request_id,
        "X-Control-Require-Usage-Tokens-Return": "text_words",
    }
    deadline = time.monotonic() + timeout
    try:
        with client.stream("POST", url, headers=headers, json=body, timeout=httpx.Timeout(connect=min(10.0, timeout), read=min(30.0, timeout), write=min(30.0, timeout), pool=min(10.0, timeout))) as response:
            log_id = response.headers.get("x-tt-logid")
            status_error = map_http_status(response.status_code, _retry_after(response.headers))
            if status_error is not None:
                status_error.with_context(request_id=request_id, log_id=log_id)
                raise status_error
            chunks: list[bytes] = []
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise CliError(
                        "NETWORK_TEMPORARY",
                        "request timed out",
                        retryable=True,
                        may_have_been_billed=True,
                        request_id=request_id,
                        log_id=log_id,
                    )
                chunks.append(chunk)
    except CliError:
        raise
    except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
        raise CliError(
            "NETWORK_TEMPORARY",
            "connection failed",
            retryable=True,
            may_have_been_billed=False,
            request_id=request_id,
        ) from exc
    except httpx.TimeoutException as exc:
        raise CliError(
            "NETWORK_TEMPORARY",
            "request timed out",
            retryable=True,
            may_have_been_billed=True,
            request_id=request_id,
        ) from exc
    except httpx.TransportError as exc:
        raise CliError(
            "NETWORK_TEMPORARY",
            "connection failed",
            retryable=True,
            may_have_been_billed=True,
            request_id=request_id,
        ) from exc
    try:
        audio, text_words = assemble_audio(iter_json_objects(chunks), secrets=secrets)
    except UnicodeDecodeError as exc:
        raise CliError(
            "INVALID_AUDIO",
            "provider response is not valid UTF-8",
            request_id=request_id,
            log_id=log_id,
            may_have_been_billed=True,
        ) from exc
    except CliError as exc:
        exc.with_context(request_id=request_id, log_id=log_id)
        if exc.code == "INVALID_AUDIO":
            exc.may_have_been_billed = True
        raise
    return SynthAudio(
        audio=audio,
        request_id=request_id,
        log_id=log_id,
        text_words=text_words,
        voice=voice,
    )
