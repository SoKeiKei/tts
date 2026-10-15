from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from doubao_tts.audio import commit_mp3
from doubao_tts.cli import app
from doubao_tts.errors import CliError
from tests.conftest import add_narrator, install_transport, minimal_mp3, ndjson_success


def _manifest(path: Path, tasks: list[dict]) -> None:
    path.write_text(
        json.dumps({"version": 1, "defaults": {"voice": "narrator"}, "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_batch_resume_skips_second_call(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=ndjson_success(minimal_mp3()), headers={"X-Tt-Logid": "L"})

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "jobs" / "batch.json"
    manifest.parent.mkdir()
    audio = tmp_path / "音频 输出"
    _manifest(
        manifest,
        [
            {"id": "ep01-sc01-line01", "text": "早上好，今天学习颜色。", "output": "ep01-sc01-line01.mp3"},
            {"id": "ep01-sc01-line02", "text": "Apple!", "output": "ep01-sc01-line02.mp3"},
        ],
    )
    first = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"])
    assert first.exit_code == 0, first.stdout
    assert calls["n"] == 2
    second = runner.invoke(
        app,
        ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--resume", "--json"],
    )
    payload = json.loads(second.stdout)
    assert second.exit_code == 0, second.stdout
    assert calls["n"] == 2
    assert payload["skipped"] == 2
    assert all(item["reused"] is True for item in payload["results"])


def test_resume_regenerates_when_text_changes(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=ndjson_success(minimal_mp3()))

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "batch.json"
    audio = tmp_path / "out"
    tasks = [{"id": "line-1", "text": "第一句", "output": "line-1.mp3"}]
    _manifest(manifest, tasks)
    assert runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"]).exit_code == 0
    tasks[0]["text"] = "第二句"
    _manifest(manifest, tasks)
    result = runner.invoke(
        app,
        ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--resume", "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.stdout
    assert calls["n"] == 2
    assert payload["results"][0]["status"] == "regenerated"
    assert payload["results"][0]["reused"] is False


def test_path_traversal_and_existing_file(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=ndjson_success(minimal_mp3()))

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "batch.json"
    audio = tmp_path / "out"
    _manifest(manifest, [{"id": "bad-1", "text": "不行", "output": "../escape.mp3"}])
    escaped = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"])
    assert escaped.exit_code == 2
    assert json.loads(escaped.stdout)["error"]["code"] == "INVALID_ARGUMENT"
    assert calls["n"] == 0

    _manifest(
        manifest,
        [
            {"id": "keep-1", "text": "保留", "output": "keep-1.mp3"},
            {"id": "clash-1", "text": "冲突", "output": "clash-1.mp3"},
        ],
    )
    audio.mkdir()
    (audio / "clash-1.mp3").write_bytes(b"foreign")
    mixed = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(audio), "--json"])
    payload = json.loads(mixed.stdout)
    assert mixed.exit_code == 6
    assert payload["succeeded"] == 1
    assert payload["failed"] == 1
    assert payload["results"][1]["error"]["code"] == "OUTPUT_EXISTS"
    assert (audio / "clash-1.mp3").read_bytes() == b"foreign"
    assert (audio / "keep-1.mp3").is_file()
    assert calls["n"] == 1


def test_absolute_output_rejected(runner, tmp_path: Path) -> None:
    add_narrator(runner)
    manifest = tmp_path / "batch.json"
    target = (tmp_path / "outside.mp3").as_posix()
    _manifest(manifest, [{"id": "abs-1", "text": "绝对", "output": target}])
    result = runner.invoke(
        app,
        ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"],
    )
    assert result.exit_code == 2
    assert not (tmp_path / "outside.mp3").exists()


def test_write_failure_cleans_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("doubao_tts.audio.os.link", boom)
    with pytest.raises(CliError) as caught:
        commit_mp3(tmp_path / "a.mp3", minimal_mp3())
    assert caught.value.code == "OUTPUT_IO"
    assert not (tmp_path / "a.mp3").exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_commit_refuses_file_created_by_someone_else(tmp_path: Path) -> None:
    target = tmp_path / "a.mp3"
    target.write_bytes(b"owned-by-other-task")
    with pytest.raises(CliError) as caught:
        commit_mp3(target, minimal_mp3())
    assert caught.value.code == "OUTPUT_EXISTS"
    assert caught.value.may_have_been_billed is True
    assert target.read_bytes() == b"owned-by-other-task"


def test_timeout_reports_possible_charge(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out waiting for response")

    install_transport(monkeypatch, handler)
    result = runner.invoke(
        app,
        ["synth", "--text", "超时", "--voice-id", "speaker-9", "--out", str(tmp_path / "a.mp3"), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 4
    assert payload["may_have_been_billed"] is True
    assert calls["n"] == 1
    assert not (tmp_path / "a.mp3").exists()


def test_unwritable_parent_does_not_call_api(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=ndjson_success(minimal_mp3()))

    install_transport(monkeypatch, handler)
    parent = tmp_path / "not-a-directory"
    parent.write_text("blocked", encoding="utf-8")
    result = runner.invoke(
        app,
        ["synth", "--text", "不要请求", "--voice-id", "speaker-9", "--out", str(parent / "a.mp3"), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 5
    assert payload["error"]["code"] == "OUTPUT_IO"
    assert calls["n"] == 0


def test_quota_stops_the_rest_of_the_batch(runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    add_narrator(runner)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, content=b"{}")

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
    assert payload["results"][1]["error"]["code"] == "QUOTA_EXHAUSTED"


def test_doctor_probe_does_not_delete_existing_file(runner, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "k-12345678")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    keep = cfg / ".doubao-tts-write-probe"
    keep.write_text("keep-me", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    assert keep.read_text(encoding="utf-8") == "keep-me"


def test_no_other_provider_import() -> None:
    import sys

    import doubao_tts

    assert doubao_tts.__version__ == "0.1.1"
    assert not any(name.startswith("minimax") for name in sys.modules)
