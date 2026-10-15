from __future__ import annotations

import json
from pathlib import Path

import httpx

from doubao_tts.cli import app
from tests.conftest import add_narrator, install_transport, minimal_mp3, ndjson_success


def test_version_and_help(runner) -> None:
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == "0.1.1"
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "synth" in help_result.stdout
    assert "minimax" not in help_result.stdout.lower()


def test_doctor_missing_key_is_json(runner) -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["provider"] == "doubao"
    assert payload["error"]["code"] == "CONFIG_MISSING"
    assert "\n" not in result.stdout.strip()


def test_doctor_masks_key(runner, monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "doubao-live-key-xyz")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    assert "doubao-live-key-xyz" not in result.stdout
    assert "doubao-live-key-xyz" not in (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["checks"]["api_key_present"] is True
    assert payload["checks"]["api_key_masked"] != "doubao-live-key-xyz"


def test_synth_json_contract(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "doubao-live-key-xyz")
    add_narrator(runner)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["X-Api-Key"]
        seen["resource"] = request.headers["X-Api-Resource-Id"]
        assert "authorization" not in {name.lower() for name in request.headers}
        body = json.loads(request.content.decode("utf-8"))
        assert body["req_params"]["speaker"] == "speaker-1"
        assert body["req_params"]["audio_params"]["format"] == "mp3"
        assert body["req_params"]["audio_params"]["speech_rate"] == 0
        assert body["req_params"]["text"] == "今天我们来学 Apple。"
        return httpx.Response(
            200,
            content=ndjson_success(minimal_mp3(), secret="doubao-live-key-xyz"),
            headers={"X-Tt-Logid": "log-abc"},
        )

    install_transport(monkeypatch, handler)
    out = tmp_path / "中文 目录" / "台词 一.mp3"
    result = runner.invoke(
        app,
        ["synth", "--text", "今天我们来学 Apple。", "--voice", "narrator", "--out", str(out), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    assert "doubao-live-key-xyz" not in result.stdout
    assert "doubao-live-key-xyz" not in (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["provider"] == "doubao"
    assert payload["output_path"].endswith("台词 一.mp3")
    assert isinstance(payload["duration_ms"], int) and payload["duration_ms"] > 0
    assert payload["request_id"]
    assert payload["log_id"] == "log-abc"
    assert payload["usage"]["text_words"] == 4
    assert out.is_file()
    assert out.with_suffix(".meta.json").is_file()
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert "doubao-live-key-xyz" not in json.dumps(meta)
    assert "今天我们来学" not in json.dumps(meta)
    assert seen["resource"] == "seed-tts-2.0"


def test_dry_run_does_not_create_audio(runner, tmp_path: Path) -> None:
    add_narrator(runner)
    out = tmp_path / "check.mp3"
    result = runner.invoke(
        app,
        ["synth", "--text", "测试", "--voice", "narrator", "--out", str(out), "--dry-run", "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["ok"] is True and payload["dry_run"] is True
    assert "duration_ms" not in payload
    assert "output_path" not in payload
    assert out.exists() is False


def test_stdin_and_output_exists(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    out = tmp_path / "line.mp3"
    out.write_bytes(b"already")
    result = runner.invoke(
        app,
        ["synth", "--stdin", "--voice", "narrator", "--out", str(out), "--json"],
        input="你好\n",
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 5
    assert payload["ok"] is False
    assert payload["error"]["code"] == "OUTPUT_EXISTS"
    assert out.read_bytes() == b"already"


def test_resource_mismatch(runner) -> None:
    add_narrator(runner)
    result = runner.invoke(
        app,
        [
            "synth",
            "--text",
            "测试",
            "--voice",
            "narrator",
            "--resource",
            "seed-icl-2.0",
            "--out",
            "a.mp3",
            "--json",
        ],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_rate_limit_then_success(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    state = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, content=b"busy")
        return httpx.Response(200, content=ndjson_success(minimal_mp3()), headers={"X-Tt-Logid": "L"})

    install_transport(monkeypatch, handler)
    out = tmp_path / "retry.mp3"
    result = runner.invoke(
        app,
        ["synth", "--text", "重试", "--voice-id", "speaker-9", "--out", str(out), "--json", "--retries", "2"],
    )
    assert result.exit_code == 0, result.stdout
    assert state["n"] == 2
    assert out.is_file()


def test_retries_disabled(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, content=b"busy")

    install_transport(monkeypatch, handler)
    result = runner.invoke(
        app,
        ["synth", "--text", "重试", "--voice-id", "speaker-9", "--out", str(tmp_path / "a.mp3"), "--json", "--retries", "0"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 4
    assert payload["error"]["code"] == "PROVIDER_RATE_LIMIT"
    assert payload["error"]["retryable"] is True
    assert calls["n"] == 1
    assert not (tmp_path / "a.mp3").exists()


def test_bad_audio_leaves_no_success_file(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")

    def handler(_request: httpx.Request) -> httpx.Response:
        body = json.dumps({"code": 0, "data": "QQ=="}) + "\n"
        return httpx.Response(200, content=body.encode("utf-8"))

    install_transport(monkeypatch, handler)
    out = tmp_path / "bad.mp3"
    result = runner.invoke(
        app,
        ["synth", "--text", "坏音频", "--voice-id", "speaker-9", "--out", str(out), "--json", "--retries", "0"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 5
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_AUDIO"
    assert out.exists() is False
    assert list(tmp_path.glob("*.partial")) == []


def test_voices_roundtrip(runner) -> None:
    add_narrator(runner)
    listed = json.loads(runner.invoke(app, ["voices", "list", "--json"]).stdout)
    assert listed["voices"][0]["name"] == "narrator"
    shown = json.loads(runner.invoke(app, ["voices", "show", "narrator", "--json"]).stdout)
    assert shown["voice"]["voice_id"] == "speaker-1"
    removed = runner.invoke(app, ["voices", "remove", "narrator", "--json"])
    assert removed.exit_code == 0
    missing = runner.invoke(app, ["voices", "show", "narrator", "--json"])
    assert missing.exit_code == 2


def test_pairings_exclude_cantonese(runner) -> None:
    listed = json.loads(runner.invoke(app, ["voices", "pairings", "--json"]).stdout)
    assert listed["cantonese_rule"]["available"] is False
    assert listed["cantonese_rule"]["command"] == "minimax-tts"
    assert "繁体字粤语口语" in listed["cantonese_rule"]["text_rule"]
    assert all(item["language"] != "yue" for item in listed["pairings"])
    yue = json.loads(runner.invoke(app, ["voices", "pairings", "--language", "yue", "--json"]).stdout)
    assert yue["pairings"] == []
    mandarin = runner.invoke(app, ["voices", "pairings", "--language", "zh"])
    assert "zh_female_vv_uranus_bigtts" in mandarin.stdout
    assert "seed-tts-2.0" in mandarin.stdout


def test_cantonese_text_is_rejected(runner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["synth", "--text", "今日我哋一齊學英語！", "--voice-id", "zh_female_vv_uranus_bigtts", "--out", str(tmp_path / "a.mp3"), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert "minimax-tts" in payload["error"]["message"]
    assert not (tmp_path / "a.mp3").exists()
