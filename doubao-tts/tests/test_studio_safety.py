from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx
import pytest

from doubao_tts.cli import app
from tests.conftest import minimal_mp3


def _arguments(kind: str, sample: Path) -> list[str]:
    if kind == "create":
        return ["create", "--prompt", "rain and narration"]
    if kind == "clone":
        return ["clone", "--audio", str(sample), "--speaker-id", "S_demo"]
    if kind == "design":
        return ["design", "--speaker-id", "S_demo", "--text", "preview", "--prompt", "soft voice"]
    return ["voice-status", "--speaker-id", "S_demo"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only output-name rules")
@pytest.mark.parametrize("kind", ["create", "clone", "design", "voice-status"])
def test_studio_checks_filename_before_request(runner, monkeypatch, tmp_path: Path, kind: str) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "review-test-key")
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"RIFFsample")

    def unexpected_client(_timeout):
        pytest.fail("invalid destination must be rejected before creating a client")

    monkeypatch.setattr("doubao_tts.cli._long_client", unexpected_client)
    result = runner.invoke(app, _arguments(kind, sample) + ["--out", str(tmp_path / "bad?.mp3"), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 2, result.stdout
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("kind", ["create", "clone", "design", "voice-status"])
def test_studio_saves_audio_and_can_explicitly_overwrite(runner, monkeypatch, tmp_path: Path, kind: str) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "review-test-key")
    audio = minimal_mp3()
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"RIFFsample")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=audio)
        if request.url.path.endswith("/create"):
            return httpx.Response(200, json={"code": 0, "audio": base64.b64encode(audio).decode("ascii")})
        return httpx.Response(200, json={
            "speaker_id": "S_demo",
            "status": 2,
            "demo_audio": "https://openspeech.bytedance.com/demo.mp3",
        })

    def factory(_timeout):
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr("doubao_tts.cli._long_client", factory)
    target = tmp_path / "line.mp3"
    arguments = _arguments(kind, sample) + ["--out", str(target), "--json"]
    first = runner.invoke(app, arguments)
    assert first.exit_code == 0, first.stdout
    payload = json.loads(first.stdout)
    assert payload["ok"] is True and payload["duration_ms"] > 0
    assert target.read_bytes() == audio
    target.write_bytes(b"old audio")
    overwritten = runner.invoke(app, arguments + ["--overwrite"])
    assert overwritten.exit_code == 0, overwritten.stdout
    assert target.read_bytes() == audio
    assert requests
