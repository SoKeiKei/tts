from __future__ import annotations

import os
from pathlib import Path

import pytest

import minimax_tts.audio as audio
from minimax_tts.errors import CliError
from tests.conftest import minimal_mp3


def test_concurrent_commit_keeps_first_audio_and_cleans_own_partials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "shared.mp3"
    first_audio = minimal_mp3(8)
    second_audio = minimal_mp3(9)
    original_link = os.link
    interleaved = {"done": False, "second_error": None}

    def link_with_interleaved_commit(source, destination, *args, **kwargs):
        if not interleaved["done"]:
            original_link(source, destination, *args, **kwargs)
            interleaved["done"] = True
            try:
                audio.commit_mp3(target, second_audio)
            except CliError as exc:
                interleaved["second_error"] = exc.code
            return None
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(audio.os, "link", link_with_interleaved_commit)

    audio.commit_mp3(target, first_audio)

    assert interleaved["second_error"] == "OUTPUT_EXISTS"
    assert target.read_bytes() == first_audio
    assert list(tmp_path.glob(".tts-*.partial")) == []


def test_concurrent_atomic_write_uses_independent_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.meta.json"
    original_replace = os.replace
    interleaved = {"done": False}

    def replace_with_interleaved_write(source, destination):
        if not interleaved["done"]:
            interleaved["done"] = True
            audio.atomic_write(target, b"second")
        original_replace(source, destination)

    monkeypatch.setattr(audio.os, "replace", replace_with_interleaved_write)

    audio.atomic_write(target, b"first")

    assert target.read_bytes() == b"first"
    assert list(tmp_path.glob(".tts-*.partial")) == []


def test_prepare_output_checks_publish_and_preserves_unrelated_files(tmp_path: Path) -> None:
    target = tmp_path / "ready.mp3"
    unrelated = tmp_path / ".minimax-tts-write-probe"
    unrelated.write_text("keep", encoding="utf-8")

    audio.prepare_output(target)

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_prepare_output_reports_publish_permission_failure_and_cleans_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied_link(*_args, **_kwargs):
        raise PermissionError("mocked publish denial")

    monkeypatch.setattr(audio.os, "link", denied_link)

    with pytest.raises(CliError) as caught:
        audio.prepare_output(tmp_path / "blocked.mp3")

    assert caught.value.code == "OUTPUT_IO"
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_prepare_output_rejects_non_directory_parent_before_publish(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")

    with pytest.raises(CliError) as caught:
        audio.prepare_output(parent / "out.mp3")

    assert caught.value.code == "OUTPUT_IO"
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_prepare_output_rejects_directory_target_before_publish(tmp_path: Path) -> None:
    target = tmp_path / "occupied.mp3"
    target.mkdir()

    with pytest.raises(CliError) as caught:
        audio.prepare_output(target, overwrite=True)

    assert caught.value.code == "OUTPUT_IO"
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_prepare_output_rejects_read_only_target_before_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "read-only.mp3"
    original = b"keep this file"
    target.write_bytes(original)
    target.chmod(0o444)

    try:
        with pytest.raises(CliError) as caught:
            audio.prepare_output(target, overwrite=True)
    finally:
        target.chmod(0o666)

    assert caught.value.code == "OUTPUT_IO"
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_overwrite_preflight_reports_publish_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protected.mp3"
    original = b"keep this file"
    target.write_bytes(original)

    def denied_replace(*_args, **_kwargs):
        raise PermissionError("mocked replace denial")

    monkeypatch.setattr(audio.os, "replace", denied_replace)

    with pytest.raises(CliError) as caught:
        audio.prepare_output(target, overwrite=True)

    assert caught.value.code == "OUTPUT_IO"
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_overwrite_preflight_and_commit_replace_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "replace.mp3"
    original = minimal_mp3(8)
    replacement = minimal_mp3(9)
    target.write_bytes(original)

    audio.prepare_output(target, overwrite=True)
    audio.commit_mp3(target, replacement, overwrite=True)

    assert target.read_bytes() == replacement
    assert list(tmp_path.glob(".tts-*.partial")) == []
    assert list(tmp_path.glob(".tts-probe-*.tmp")) == []


def test_prepare_output_refuses_existing_file_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "keep.mp3"
    original = b"owned by another task"
    target.write_bytes(original)

    with pytest.raises(CliError) as caught:
        audio.prepare_output(target)

    assert caught.value.code == "OUTPUT_EXISTS"
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows-only output-name rules")
@pytest.mark.parametrize(
    "relative_path",
    ["bad?.mp3", "NUL.mp3", "trailing. ", "subdir.\\audio.mp3"],
)
def test_windows_output_names_are_rejected_before_directory_creation(
    tmp_path: Path, relative_path: str
) -> None:
    target = tmp_path / relative_path
    with pytest.raises(CliError) as caught:
        audio.prepare_output(target)

    assert caught.value.code == "INVALID_ARGUMENT"
    assert not (tmp_path / "subdir.").exists()
