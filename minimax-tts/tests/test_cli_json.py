from __future__ import annotations

import json
from pathlib import Path

import httpx

from minimax_tts.api import build_body
from minimax_tts.cli import app
from minimax_tts.models import ResolvedVoice
from tests.conftest import add_narrator, install_transport, minimal_mp3, success_payload


def test_version_help_and_doctor(runner, monkeypatch) -> None:
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "0.1.2"
    help_result = runner.invoke(app, ["--help"])
    assert "synth" in help_result.stdout
    assert "doubao" not in help_result.stdout.lower()
    missing = runner.invoke(app, ["doctor", "--json"])
    assert missing.exit_code == 3
    assert json.loads(missing.stdout)["error"]["code"] == "CONFIG_MISSING"
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    ready = runner.invoke(app, ["doctor", "--json"])
    assert ready.exit_code == 0, ready.stdout
    assert "minimax-live-key-xyz" not in ready.stdout
    assert json.loads(ready.stdout)["checks"]["base_url"] == "https://api.minimaxi.com"


def test_rejects_untrusted_host(runner, monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    monkeypatch.setenv("MINIMAX_TTS_API_BASE", "https://evil.example")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "CONFIG_INVALID"


def test_request_schema_and_cantonese(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content.decode("utf-8"))
        assert request.url.host == "api.minimaxi.com"
        return httpx.Response(200, json=success_payload(minimal_mp3(), trace="tid", usage=26))

    install_transport(monkeypatch, handler)
    out = tmp_path / "粤语 输出" / "一句.mp3"
    result = runner.invoke(
        app,
        [
            "synth",
            "--text",
            "今日我哋一齊學英語！",
            "--voice-id",
            "Cantonese_GentleLady",
            "--language-boost",
            "Chinese,Yue",
            "--emotion",
            "calm",
            "--model",
            "speech-2.8-hd",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["provider"] == "minimax"
    assert payload["provider_trace_id"] == "tid"
    assert payload["usage"]["usage_characters"] == 26
    assert isinstance(payload["duration_ms"], int)
    assert "minimax-live-key-xyz" not in result.stdout
    body = seen["body"]
    assert seen["auth"] == "Bearer minimax-live-key-xyz"
    assert body["stream"] is False
    assert body["output_format"] == "hex"
    assert body["model"] == "speech-2.8-hd"
    assert body["language_boost"] == "Chinese,Yue"
    assert body["voice_setting"]["emotion"] == "calm"
    assert body["voice_setting"]["speed"] == 1.0
    assert body["voice_setting"]["vol"] == 1.0
    assert body["voice_setting"]["pitch"] == 0
    assert body["audio_setting"] == {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1}
    assert out.is_file()
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert "minimax-live-key-xyz" not in json.dumps(meta)
    assert "今日我哋" not in json.dumps(meta)


def test_text_too_long_and_dry_run(runner, tmp_path: Path) -> None:
    add_narrator(runner)
    long_text = "字" * 10000
    result = runner.invoke(
        app,
        ["synth", "--text", long_text, "--voice", "narrator", "--out", str(tmp_path / "a.mp3"), "--json"],
    )
    assert json.loads(result.stdout)["error"]["code"] == "TEXT_TOO_LONG"
    dry = runner.invoke(
        app,
        ["synth", "--text", "测试", "--voice", "narrator", "--out", str(tmp_path / "b.mp3"), "--dry-run", "--json"],
    )
    payload = json.loads(dry.stdout)
    assert payload["dry_run"] is True
    assert "duration_ms" not in payload
    assert "output_path" not in payload
    assert not (tmp_path / "b.mp3").exists()


def test_whisper_rejected_on_28(runner) -> None:
    result = runner.invoke(
        app,
        ["synth", "--text", "hi", "--voice-id", "v", "--emotion", "whisper", "--model", "speech-2.8-hd", "--out", "a.mp3", "--json"],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_ARGUMENT"


def test_body_does_not_reuse_doubao_fields() -> None:
    voice = ResolvedVoice(voice_id="v", model="speech-2.8-turbo", language_boost="auto")
    body = build_body("Hello", voice)
    assert "speaker" not in json.dumps(body)
    assert "speech_rate" not in json.dumps(body)
    assert "resource" not in json.dumps(body)


def test_rate_limit_retry_and_quota(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "slow"})
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    ok = runner.invoke(
        app,
        ["synth", "--text", "hi", "--voice-id", "v", "--out", str(tmp_path / "a.mp3"), "--json", "--retries", "1"],
    )
    assert ok.exit_code == 0, ok.stdout
    assert calls["n"] == 2

    def quota(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload(b"", status_code=1008, status_msg="insufficient balance secret minimax-live-key-xyz"))

    install_transport(monkeypatch, quota)
    denied = runner.invoke(
        app,
        ["synth", "--text", "hi", "--voice-id", "v", "--out", str(tmp_path / "b.mp3"), "--json", "--retries", "2"],
    )
    assert denied.exit_code == 3
    assert "minimax-live-key-xyz" not in denied.stdout
    assert json.loads(denied.stdout)["error"]["code"] == "QUOTA_EXHAUSTED"
    assert not (tmp_path / "b.mp3").exists()
