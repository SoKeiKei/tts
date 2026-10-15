from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from doubao_tts.cli import app


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOUBAO_TTS_CONFIG_DIR", str(tmp_path / "cfg"))
    for name in (
        "DOUBAO_TTS_API_KEY",
        "DOUBAO_TTS_BASE_URL",
        "DOUBAO_TTS_RESOURCE_ID",
        "DOUBAO_TTS_ALLOW_TEST_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("doubao_tts.api._sleep", lambda _seconds: None)


@pytest.fixture
def runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def minimal_mp3(frames: int = 8) -> bytes:
    header = bytes([0xFF, 0xFB, 0x90, 0xC0])
    size = int(144 * 128000 / 44100)
    frame = header + b"\x00" * (size - 4)
    return frame * frames


def ndjson_success(audio: bytes, *, secret: str | None = None) -> bytes:
    import base64

    message = "" if secret is None else f"note {secret}"
    first = {"code": 0, "message": message, "data": base64.b64encode(audio).decode("ascii")}
    end = {"code": 20000000, "message": "ok", "data": None, "usage": {"text_words": 4}}
    return (json.dumps(first) + "\n" + json.dumps(end) + "\n").encode("utf-8")


def install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> dict[str, int]:
    calls = {"n": 0}

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def factory(_timeout: float) -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False)

    monkeypatch.setattr("doubao_tts.cli.make_client", factory)
    return calls


def add_narrator(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        ["voices", "add", "narrator", "--voice-id", "speaker-1", "--resource", "seed-tts-2.0"],
    )
    assert result.exit_code == 0, result.stdout
