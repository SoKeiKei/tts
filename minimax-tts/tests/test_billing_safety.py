from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from minimax_tts.api import synthesize
from minimax_tts.errors import CliError
from minimax_tts.models import ResolvedVoice
from minimax_tts.settings import Settings


def _audio_response(audio: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "data": {"status": 2, "audio": audio.hex()},
            "trace_id": "mock-trace",
        },
    )


def _synthesize(handler, *, retries: int = 3):
    settings = Settings(api_key="test-only", config_dir=Path("."))
    voice = ResolvedVoice(voice_id="mock-voice", model="speech-2.8-hd")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return synthesize(
            settings,
            text="mock text",
            voice=voice,
            client=client,
            retries=retries,
            timeout=1,
            request_id="mock-request",
        )


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout])
def test_ambiguous_response_failures_are_not_retried(error_type, monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise error_type("mock response failure")

    with pytest.raises(CliError) as caught:
        _synthesize(handler)

    assert calls["n"] == 1
    assert caught.value.may_have_been_billed is True
    assert caught.value.request_id == "mock-request"


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
def test_pre_send_failures_can_retry(error_type, monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise error_type("mock connection failure")
        return _audio_response(_valid_mp3())

    result = _synthesize(handler, retries=2)

    assert calls["n"] == 2
    assert result.request_id == "mock-request"


def test_rate_limit_can_retry(monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return _audio_response(_valid_mp3())

    result = _synthesize(handler, retries=2)

    assert calls["n"] == 2
    assert result.request_id == "mock-request"


def test_potentially_billed_http_failure_is_not_retried(monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    with pytest.raises(CliError) as caught:
        _synthesize(handler)

    assert calls["n"] == 1
    assert caught.value.may_have_been_billed is True
    assert caught.value.request_id == "mock-request"


def test_invalid_audio_keeps_possible_charge_flag(monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)

    with pytest.raises(CliError) as caught:
        _synthesize(lambda _request: _audio_response(b"not an mp3"))

    assert caught.value.code == "INVALID_AUDIO"
    assert caught.value.may_have_been_billed is True
    assert caught.value.request_id == "mock-request"


def test_invalid_utf8_keeps_request_context_and_charge_flag(monkeypatch) -> None:
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)

    with pytest.raises(CliError) as caught:
        _synthesize(lambda _request: httpx.Response(200, content=b"\xff"))

    assert caught.value.code == "INVALID_AUDIO"
    assert caught.value.may_have_been_billed is True
    assert caught.value.request_id == "mock-request"


def _valid_mp3() -> bytes:
    frame = bytes([0xFF, 0xFB, 0x90, 0xC0]) + bytes(413)
    return frame * 8
