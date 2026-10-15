from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from doubao_tts.cli import app
from doubao_tts.settings import Settings
from doubao_tts.studio import create_audio
from tests.conftest import minimal_mp3


def _settings() -> Settings:
    return Settings(
        api_key="doubao-live-key-xyz",
        resource_id="seed-tts-2.0",
        base_url="https://openspeech.bytedance.com",
        config_dir=Path("."),
    )


def test_create_sends_seed_audio_and_saves_mp3(tmp_path: Path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode("utf-8"))
        assert request.headers["X-Api-Key"] == "doubao-live-key-xyz"
        return httpx.Response(
            200,
            json={"code": 0, "audio": base64.b64encode(minimal_mp3()).decode("ascii"), "original_duration": 1.2},
            headers={"X-Tt-Logid": "log-create"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    produced = create_audio(
        _settings(),
        client=client,
        prompt="室内有雨声。旁白说：今天学习红色。然后一声轻轻的翻书声。",
        speakers=["zh_female_vv_uranus_bigtts"],
        reference_audios=[],
        sample_rate=48000,
        speech_rate=0,
        loudness_rate=0,
        pitch_rate=0,
        timeout=30,
    )
    assert seen["path"] == "/api/v3/tts/create"
    assert seen["body"]["model"] == "seed-audio-1.0"
    assert seen["body"]["references"] == [{"speaker": "zh_female_vv_uranus_bigtts"}]
    assert seen["body"]["audio_config"]["format"] == "mp3"
    assert produced["audio"].startswith(b"\xff")
    assert "doubao-live-key-xyz" not in json.dumps(seen["body"])


def test_clone_and_design_commands_do_not_print_key(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "doubao-live-key-xyz")
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"RIFFdemo")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert "doubao-live-key-xyz" not in json.dumps({k: v for k, v in body.items() if k != "audio"})
        if request.url.path.endswith("voice_clone"):
            assert body["audio"]["format"] == "wav"
            return httpx.Response(200, json={"speaker_id": "S_demo", "status": 2, "available_training_times": 9})
        return httpx.Response(
            200,
            json={"speaker_id": "S_demo", "status": 2, "demo_audio": "https://evil.example/demo.mp3"},
        )

    transport = httpx.MockTransport(handler)

    def factory(_timeout: float) -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False)

    monkeypatch.setattr("doubao_tts.cli._long_client", factory)
    cloned = runner.invoke(
        app,
        ["clone", "--audio", str(sample), "--speaker-id", "S_demo", "--language", "cn", "--json"],
    )
    payload = json.loads(cloned.stdout)
    assert cloned.exit_code == 0, cloned.stdout
    assert payload["kind"] == "voice-clone"
    assert payload["ready_for_tts"] is True
    assert payload["resource_id_for_synth"] == "seed-icl-2.0"
    assert "doubao-live-key-xyz" not in cloned.stdout
    designed = runner.invoke(
        app,
        ["design", "--speaker-id", "S_demo", "--text", "夜色渐浓。", "--prompt", "女性，语速中等", "--json"],
    )
    assert designed.exit_code == 0, designed.stdout
    assert "evil.example" not in designed.stdout
