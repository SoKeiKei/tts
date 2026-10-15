from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from doubao_tts.cli import app
from doubao_tts import cli
from tests.conftest import install_transport, minimal_mp3, ndjson_success


def _manifest(path: Path, outputs: list[str]) -> None:
    path.write_text(
        json.dumps({
            "version": 1,
            "defaults": {"voice_id": "review-voice"},
            "tasks": [
                {"id": f"line-{index}", "text": "review", "output": output}
                for index, output in enumerate(outputs, start=1)
            ],
        }),
        encoding="utf-8",
    )


@pytest.fixture
def successful_requests(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "review-test-key")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=ndjson_success(minimal_mp3()))

    install_transport(monkeypatch, handler)
    return calls


@pytest.mark.parametrize("status", [401, 403, 402])
def test_batch_stops_after_authorization_or_quota_failure(runner, monkeypatch, tmp_path: Path, status: int) -> None:
    monkeypatch.setenv("DOUBAO_TTS_API_KEY", "review-test-key")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    install_transport(monkeypatch, handler)
    manifest = tmp_path / "batch.json"
    _manifest(manifest, ["one.mp3", "two.mp3", "three.mp3"])
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 6, result.stdout
    assert len(calls) == 1
    code = "QUOTA_EXHAUSTED" if status == 402 else "AUTH_OR_RESOURCE"
    assert payload["results"][0]["error"]["code"] == code
    assert [row["status"] for row in payload["results"]] == ["failed", "stopped", "stopped"]
    assert all(row["error"]["code"] == code for row in payload["results"])
    assert payload["failed"] == 3


def test_batch_missing_key_preserves_task_results_and_stops(runner, successful_requests, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DOUBAO_TTS_API_KEY")
    manifest = tmp_path / "batch.json"
    _manifest(manifest, ["one.mp3", "two.mp3"])
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 6, result.stdout
    assert successful_requests == []
    assert [row["status"] for row in payload["results"]] == ["failed", "stopped"]
    assert all(row["error"]["code"] == "CONFIG_MISSING" for row in payload["results"])


def test_batch_bad_parent_keeps_completed_and_later_results(runner, successful_requests, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "blocked").write_text("keep", encoding="utf-8")
    manifest = tmp_path / "batch.json"
    _manifest(manifest, ["one.mp3", "blocked/two.mp3", "three.mp3"])
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(out), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 6, result.stdout
    assert len(successful_requests) == 2
    assert payload["succeeded"] == 2 and payload["failed"] == 1
    assert [row["status"] for row in payload["results"]] == ["succeeded", "failed", "succeeded"]
    assert payload["results"][1]["error"]["code"] == "OUTPUT_IO"
    assert not payload["results"][1].get("may_have_been_billed", False)
    assert (out / "one.mp3").is_file() and (out / "three.mp3").is_file()
    assert (out / "blocked").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only output-name rules")
@pytest.mark.parametrize("relative", ["bad?.mp3", "NUL.mp3", "folder./line.mp3"])
def test_invalid_output_is_rejected_before_synthesis(runner, successful_requests, tmp_path: Path, relative: str) -> None:
    result = runner.invoke(app, ["synth", "--text", "review", "--voice-id", "review-voice", "--out", str(tmp_path / relative), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 2, result.stdout
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert successful_requests == []


def test_metadata_destination_checked_before_synthesis(runner, successful_requests, tmp_path: Path) -> None:
    target = tmp_path / "line.mp3"
    target.with_suffix(".meta.json").mkdir()
    result = runner.invoke(app, ["synth", "--text", "review", "--voice-id", "review-voice", "--out", str(target), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 5, result.stdout
    assert payload["error"]["code"] == "OUTPUT_IO"
    assert successful_requests == []
    assert not target.exists()


def test_corrupt_metadata_keeps_batch_summary_and_foreign_audio(runner, successful_requests, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "two.mp3"
    existing.write_bytes(minimal_mp3())
    existing.with_suffix(".meta.json").write_bytes(b"\xff")
    manifest = tmp_path / "batch.json"
    _manifest(manifest, ["one.mp3", "two.mp3", "three.mp3"])
    result = runner.invoke(app, ["batch", "--manifest", str(manifest), "--output-dir", str(out), "--resume", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 6, result.stdout
    assert len(successful_requests) == 2
    assert payload["succeeded"] == 2 and payload["failed"] == 1
    assert [row["status"] for row in payload["results"]] == ["succeeded", "failed", "succeeded"]
    assert payload["results"][1]["error"]["code"] == "OUTPUT_EXISTS"
    assert existing.read_bytes() == minimal_mp3()
    assert existing.with_suffix(".meta.json").read_bytes() == b"\xff"


def test_save_failure_after_synthesis_reports_possible_charge(runner, successful_requests, monkeypatch, tmp_path: Path) -> None:
    def fail_save(*args, **kwargs):
        raise PermissionError("write denied after synthesis")

    monkeypatch.setattr("doubao_tts.cli.commit_mp3", fail_save)
    target = tmp_path / "line.mp3"
    result = runner.invoke(app, ["synth", "--text", "review", "--voice-id", "review-voice", "--out", str(target), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 5, result.stdout
    assert payload["error"]["code"] == "OUTPUT_IO"
    assert payload["may_have_been_billed"] is True
    assert payload["request_id"]
    assert len(successful_requests) == 1
    assert not target.exists()


def test_explicit_overwrite_still_saves_audio(runner, successful_requests, tmp_path: Path) -> None:
    target = tmp_path / "line.mp3"
    target.write_bytes(b"old audio")
    result = runner.invoke(app, ["synth", "--text", "review", "--voice-id", "review-voice", "--out", str(target), "--overwrite", "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.stdout
    assert payload["ok"] is True
    assert target.read_bytes() == minimal_mp3()
    assert target.with_suffix(".meta.json").is_file()
    assert len(successful_requests) == 1


def test_publish_permission_failure_does_not_submit(runner, successful_requests, monkeypatch, tmp_path: Path) -> None:
    def denied_link(*args, **kwargs):
        raise PermissionError("publishing denied")

    monkeypatch.setattr("doubao_tts.audio.os.link", denied_link)
    result = runner.invoke(app, ["synth", "--text", "review", "--voice-id", "review-voice", "--out", str(tmp_path / "line.mp3"), "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 5, result.stdout
    assert payload["error"]["code"] == "OUTPUT_IO"
    assert successful_requests == []


def test_doctor_probe_collision_preserves_foreign_file(monkeypatch, tmp_path: Path) -> None:
    foreign = tmp_path / ".doubao-tts-write-probe-collision"
    foreign.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    writable, _error = cli._writable_dir(tmp_path)
    assert writable is False
    assert foreign.read_text(encoding="utf-8") == "keep"
