from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from minimax_tts.cli import app
from tests.conftest import add_narrator, install_transport, minimal_mp3, success_payload


def _manifest(path: Path, tasks: list[dict], defaults: dict | None = None) -> None:
    path.write_text(
        json.dumps(
            {"version": 1, "defaults": defaults or {"voice": "narrator", "model": "speech-2.8-hd"}, "tasks": tasks},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_manifest_validation(runner, tmp_path: Path) -> None:
    add_narrator(runner)
    add_narrator(runner, language="Chinese,Yue", name="yue_host")
    manifest = tmp_path / "batch.json"
    _manifest(manifest, [{"id": "dup", "text": "a", "output": "a.mp3"}, {"id": "dup", "text": "b", "output": "b.mp3"}])
    duplicate = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"])
    assert duplicate.exit_code == 2
    _manifest(manifest, [{"id": "bad", "text": "a", "output": "../x.mp3"}])
    travel = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"])
    assert travel.exit_code == 2
    assert not (tmp_path / "x.mp3").exists()


def test_resume_and_emotion_change(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        assert body["text"]
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "batch.json"
    audio = tmp_path / "音频 输出"
    tasks = [{"id": "line-1", "text": "Hello! Today we learn colors.", "output": "line-1.mp3"}]
    _manifest(manifest, tasks)
    first = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"])
    assert first.exit_code == 0, first.stdout
    second = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--resume", "--json"])
    assert second.exit_code == 0, second.stdout
    assert json.loads(second.stdout)["skipped"] == 1
    assert calls["n"] == 1
    tasks[0]["emotion"] = "happy"
    _manifest(manifest, tasks)
    third = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--resume", "--json"])
    payload = json.loads(third.stdout)
    assert third.exit_code == 0, third.stdout
    assert calls["n"] == 2
    assert payload["results"][0]["status"] == "regenerated"


def test_existing_file_is_not_overwritten(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    audio = tmp_path / "out"
    audio.mkdir()
    (audio / "keep.mp3").write_bytes(b"foreign")
    manifest = tmp_path / "batch.json"
    _manifest(
        manifest,
        [
            {"id": "new-1", "text": "new", "output": "new.mp3"},
            {"id": "keep-1", "text": "old", "output": "keep.mp3"},
        ],
    )
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 6
    assert payload["succeeded"] == 1
    assert payload["results"][1]["error"]["code"] == "OUTPUT_EXISTS"
    assert (audio / "keep.mp3").read_bytes() == b"foreign"
    assert calls["n"] == 1


def test_pairings_list_cantonese_rule(runner) -> None:
    payload = json.loads(runner.invoke(app, ["voices", "pairings", "--language", "yue", "--json"]).stdout)
    assert set(payload["cantonese_rule"]) == {"available", "provider", "model", "language_boost", "text_rule", "example"}
    assert payload["cantonese_rule"]["language_boost"] == "Chinese,Yue"
    assert "可参考" in payload["cantonese_rule"]["text_rule"]
    assert "必须" not in payload["cantonese_rule"]["text_rule"]
    ids = {item["voice_id"] for item in payload["pairings"]}
    assert "Cantonese_GentleLady" in ids
    assert "Cantonese_ProfessionalHost (F)" in ids
    assert all(item["language_boost"] == "Chinese,Yue" for item in payload["pairings"])


@pytest.mark.parametrize(
    ("text", "voice_id", "language_boost"),
    [
        ("今日我哋一齐学英语！", "Cantonese_GentleLady", "auto"),
        ("今天我们学习英语。", "Chinese (Mandarin)_Lyrical_Voice", "Chinese,Yue"),
    ],
)
def test_text_voice_and_language_are_not_forced_to_pair(runner, monkeypatch, tmp_path: Path, text: str, voice_id: str, language_boost: str) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    result = runner.invoke(
        app,
        [
            "synth",
            "--text",
            text,
            "--voice-id",
            voice_id,
            "--language-boost",
            language_boost,
            "--out",
            str(tmp_path / "a.mp3"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert len(seen) == 1
    assert seen[0]["text"] == text
    assert seen[0]["voice_setting"]["voice_id"] == voice_id
    assert seen[0]["language_boost"] == language_boost
    assert (tmp_path / "a.mp3").is_file()


def test_synth_and_batch_work_without_enabled_flag(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MINIMAX_TTS_ENABLED", raising=False)
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    synth = runner.invoke(
        app,
        ["synth", "--text", "测试。", "--voice-id", "v", "--out", str(tmp_path / "a.mp3"), "--json"],
    )
    assert synth.exit_code == 0, synth.stdout

    manifest = tmp_path / "batch.json"
    _manifest(
        manifest,
        [{"id": "yue-line", "text": "今日我哋一齐学英语！", "output": "yue-line.mp3", "voice_id": "Cantonese_GentleLady", "language_boost": "Chinese,Yue"}],
        defaults={"model": "speech-2.8-hd"},
    )
    batch = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "batch-out"), "--json"])
    assert batch.exit_code == 0, batch.stdout
    assert json.loads(batch.stdout)["succeeded"] == 1
    assert calls[0]["text"] == "测试。"
    assert calls[1]["text"] == "今日我哋一齐学英语！"
    assert calls[1]["voice_setting"]["voice_id"] == "Cantonese_GentleLady"
    assert calls[1]["language_boost"] == "Chinese,Yue"


def test_dry_run_synth_and_batch_work_without_enabled_flag(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MINIMAX_TTS_ENABLED", raising=False)
    dry_synth = runner.invoke(
        app,
        ["synth", "--text", "测试。", "--voice-id", "v", "--out", str(tmp_path / "dry.mp3"), "--dry-run", "--json"],
    )
    assert dry_synth.exit_code == 0, dry_synth.stdout
    assert json.loads(dry_synth.stdout)["dry_run"] is True
    assert not (tmp_path / "dry.mp3").exists()

    manifest = tmp_path / "dry-batch.json"
    _manifest(manifest, [{"id": "line-1", "text": "测试。", "output": "line-1.mp3", "voice_id": "v"}], defaults={"model": "speech-2.8-hd"})
    dry_batch = runner.invoke(
        app,
        ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "dry-out"), "--dry-run", "--json"],
    )
    assert dry_batch.exit_code == 0, dry_batch.stdout
    assert json.loads(dry_batch.stdout)["dry_run"] is True


def test_missing_api_key_prevents_requests(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MINIMAX_TTS_API_KEY", raising=False)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=success_payload(minimal_mp3()))

    install_transport(monkeypatch, handler)
    synth = runner.invoke(
        app,
        ["synth", "--text", "测试。", "--voice-id", "v", "--out", str(tmp_path / "a.mp3"), "--json"],
    )
    assert synth.exit_code == 3
    assert json.loads(synth.stdout)["error"]["code"] == "CONFIG_MISSING"

    manifest = tmp_path / "missing-key.json"
    _manifest(manifest, [{"id": "line-1", "text": "测试。", "output": "line-1.mp3", "voice_id": "v"}], defaults={"model": "speech-2.8-hd"})
    batch = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "missing-key-out"), "--json"])
    assert batch.exit_code == 6
    assert json.loads(batch.stdout)["results"][0]["error"]["code"] == "CONFIG_MISSING"
    assert calls["n"] == 0


def test_bad_audio_reports_possible_charge(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"audio": "68656c6c6f", "status": 2},
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "trace_id": "trace-bad",
            },
        )

    install_transport(monkeypatch, handler)
    result = runner.invoke(
        app,
        ["synth", "--text", "坏音频", "--voice-id", "voice-1", "--out", str(tmp_path / "a.mp3"), "--json", "--retries", "0"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 5
    assert payload["error"]["code"] == "INVALID_AUDIO"
    assert payload["may_have_been_billed"] is True
    assert not (tmp_path / "a.mp3").exists()


def test_quota_stops_remaining_batch_tasks(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=success_payload(b"", status_code=1008, status_msg="insufficient balance"))

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "batch.json"
    _manifest(
        manifest,
        [
            {"id": "line-1", "text": "第一句", "output": "line-1.mp3"},
            {"id": "line-2", "text": "第二句", "output": "line-2.mp3"},
        ],
    )
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"])
    payload = json.loads(result.stdout)
    assert calls["n"] == 1
    assert payload["results"][0]["error"]["code"] == "QUOTA_EXHAUSTED"
    assert payload["results"][1]["status"] == "stopped"


def test_doctor_does_not_remove_existing_probe_name(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIMAX_TTS_API_KEY", "minimax-live-key-xyz")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    keep = cfg / ".minimax-tts-write-probe"
    keep.write_text("keep-me", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    assert keep.read_text(encoding="utf-8") == "keep-me"
    import sys

    import minimax_tts

    assert minimax_tts.__version__ == "0.1.2"
    assert not any(name.startswith("doubao") for name in sys.modules)
