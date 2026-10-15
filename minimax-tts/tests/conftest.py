from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from minimax_tts.cli import app


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINIMAX_TTS_CONFIG_DIR", str(tmp_path / "cfg"))
    for name in (
        "MINIMAX_TTS_API_KEY",
        "MINIMAX_TTS_API_BASE",
        "MINIMAX_TTS_DEFAULT_MODEL",
        "MINIMAX_TTS_ALLOW_TEST_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("minimax_tts.api._sleep", lambda _seconds: None)
    monkeypatch.setenv("MINIMAX_TTS_ENABLED", "1")
    monkeypatch.setenv("MINIMAX_TTS_ENABLED", "1")


@pytest.fixture
def runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def minimal_mp3(frames: int = 8) -> bytes:
    header = bytes([0xFF, 0xFB, 0x90, 0xC0])
    size = int(144 * 128000 / 44100)
    return (header + b"\x00" * (size - 4)) * frames


def success_payload(audio: bytes, *, status_code: int = 0, status: int = 2, audio_hex: str | None = None, usage: int | None = 12, trace: str = "trace-1", status_msg: str = "success") -> dict:
    return {
        "data": None if audio_hex is None and status_code != 0 else {"audio": audio.hex() if audio_hex is None else audio_hex, "status": status},
        "extra_info": {"audio_length": 200, "usage_characters": usage},
        "trace_id": trace,
        "base_resp": {"status_code": status_code, "status_msg": status_msg},
    }


def install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def factory(_timeout: float) -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False)

    monkeypatch.setattr("minimax_tts.cli.make_client", factory)


def add_narrator(runner: CliRunner, *, language: str = "auto", name: str = "narrator") -> None:
    result = runner.invoke(
        app,
        ["voices", "add", name, "--voice-id", "voice-1", "--model", "speech-2.8-hd", "--language-boost", language],
    )
    assert result.exit_code == 0, result.stdout
