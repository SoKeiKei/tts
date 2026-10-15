from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from doubao_tts import __version__
from doubao_tts.api import synthesize
from doubao_tts.audio import atomic_write, commit_mp3, ensure_mp3_destination, measure_duration_ms, prepare_output, sha256_bytes
from doubao_tts.errors import CliError
from doubao_tts.models import Manifest, ManifestTask, ResolvedVoice, fingerprint, require_text
from doubao_tts.script_rules import reject_cantonese_text
from doubao_tts.settings import Settings, require_api_key
from doubao_tts.voices import resolve_voice

PROVIDER = "doubao"


def meta_path_for(audio_path: Path) -> Path:
    return audio_path.with_suffix(".meta.json")


def safe_output(output_dir: Path, relative: str) -> Path:
    if not relative or relative.strip() != relative:
        raise CliError("INVALID_ARGUMENT", "output path is unsafe")
    if any(ch in relative for ch in '<>:"|?*\n\r\t'):
        raise CliError("INVALID_ARGUMENT", "output path is unsafe")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or raw.anchor:
        raise CliError("INVALID_ARGUMENT", "output path must stay inside the output directory")
    base = output_dir.resolve()
    current = base
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            target = current.resolve()
            if not _inside(base, target):
                raise CliError("INVALID_ARGUMENT", "output path must stay inside the output directory")
    ensure_mp3_destination(base / raw)
    destination = (base / raw).resolve()
    if not _inside(base, destination):
        raise CliError("INVALID_ARGUMENT", "output path must stay inside the output directory")
    return ensure_mp3_destination(destination)


def _inside(base: Path, target: Path) -> bool:
    try:
        return os.path.commonpath([str(base), str(target)]) == str(base)
    except ValueError:
        return False


def load_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError("INVALID_ARGUMENT", "manifest is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise CliError("INVALID_ARGUMENT", "manifest version must be 1")
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise CliError("INVALID_ARGUMENT", "manifest fields are invalid") from exc


def task_text(task: ManifestTask, manifest_path: Path) -> str:
    if (task.text is None) == (task.text_file is None):
        raise CliError("INVALID_ARGUMENT", f"task {task.id} needs exactly one of text or text_file")
    if task.text is not None:
        return require_text(task.text)
    assert task.text_file is not None
    file_path = Path(task.text_file)
    if file_path.is_absolute() or ".." in file_path.parts:
        raise CliError("INVALID_ARGUMENT", f"task {task.id} text_file must stay next to the manifest")
    source = (manifest_path.parent / file_path).resolve()
    root = manifest_path.parent.resolve()
    if not _inside(root, source):
        raise CliError("INVALID_ARGUMENT", f"task {task.id} text_file must stay next to the manifest")
    try:
        return require_text(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise CliError("INVALID_ARGUMENT", f"task {task.id} text_file is unreadable") from exc


def _check_task_id(task_id: str) -> None:
    if not task_id[0].isalnum() or any(not (ch.isalnum() or ch in "._-") for ch in task_id):
        raise CliError("INVALID_ARGUMENT", f"task id is invalid: {task_id}")


def resolve_task(
    settings: Settings,
    task: ManifestTask,
    defaults: Any,
    manifest_path: Path,
    output_dir: Path,
) -> tuple[str, Path, ResolvedVoice, str]:
    _check_task_id(task.id)
    text = task_text(task, manifest_path)
    reject_cantonese_text(text)
    alias = task.voice if task.voice is not None else defaults.voice
    voice_id = task.voice_id if task.voice_id is not None else defaults.voice_id
    resource = task.resource if task.resource is not None else defaults.resource
    speed = task.speed if task.speed is not None else defaults.speed
    volume = task.volume if task.volume is not None else defaults.volume
    sample_rate = task.sample_rate if task.sample_rate is not None else defaults.sample_rate
    emotion = task.emotion if task.emotion is not None else defaults.emotion
    voice = resolve_voice(
        settings,
        alias=alias,
        voice_id=voice_id,
        resource_id=resource,
        speed=speed,
        volume=volume,
        sample_rate=sample_rate,
        emotion=emotion,
    )
    destination = safe_output(output_dir, task.output)
    digest = fingerprint(
        text=text,
        voice_id=voice.voice_id,
        resource_id=voice.resource_id,
        speed=voice.speed,
        volume=voice.volume,
        sample_rate=voice.sample_rate,
        emotion=voice.emotion,
    )
    return text, destination, voice, digest


def _read_meta(path: Path, task_id: str) -> dict[str, Any] | None:
    meta = meta_path_for(path)
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != 1 or data.get("provider") != PROVIDER or data.get("task_id") != task_id:
        return None
    if not isinstance(data.get("input_sha256"), str) or not isinstance(data.get("audio_sha256"), str):
        return None
    return data


def _matches(path: Path, meta: dict[str, Any], digest: str) -> bool:
    if meta.get("input_sha256") != digest:
        return False
    try:
        audio = path.read_bytes()
        measure_duration_ms(path)
    except (OSError, CliError):
        return False
    return sha256_bytes(audio) == meta.get("audio_sha256")


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(meta_path_for(path), raw)


def run_batch(
    settings: Settings,
    *,
    manifest_path: Path,
    output_dir: Path,
    resume: bool,
    overwrite: bool,
    dry_run: bool,
    retries: int,
    timeout: float,
    client: httpx.Client | None,
) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(manifest_path)
    seen_ids: set[str] = set()
    seen_outputs: set[str] = set()
    planned: list[tuple[ManifestTask, str, Path, ResolvedVoice, str]] = []
    for task in manifest.tasks:
        if task.id in seen_ids:
            raise CliError("INVALID_ARGUMENT", f"duplicate task id: {task.id}")
        seen_ids.add(task.id)
        text, destination, voice, digest = resolve_task(settings, task, manifest.defaults, manifest_path, output_dir)
        key = str(destination).lower()
        if key in seen_outputs:
            raise CliError("INVALID_ARGUMENT", f"duplicate output path for task {task.id}")
        seen_outputs.add(key)
        planned.append((task, text, destination, voice, digest))

    if not dry_run:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CliError("OUTPUT_IO", "output directory is not writable") from exc

    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    skipped = 0
    stop_code: str | None = None
    for task, text, destination, voice, digest in planned:
        if dry_run:
            results.append(
                {
                    "ok": True,
                    "status": "dry_run",
                    "task_id": task.id,
                    "planned_output": destination.as_posix(),
                    "output_exists": destination.exists(),
                    "input_sha256": digest,
                    "voice_id": voice.voice_id,
                    "resource_id": voice.resource_id,
                }
            )
            continue
        if stop_code is not None:
            error = CliError(
                stop_code,
                "batch stopped after a configuration, authorization, or quota failure; this task was not submitted",
                task_id=task.id,
            )
            body = error.payload(PROVIDER)
            body["status"] = "stopped"
            results.append(body)
            failed += 1
            continue
        item, kind = _run_one(
            settings,
            task_id=task.id,
            text=text,
            destination=destination,
            voice=voice,
            digest=digest,
            resume=resume,
            overwrite=overwrite,
            retries=retries,
            timeout=timeout,
            client=client,
        )
        results.append(item)
        if kind == "skipped":
            skipped += 1
        elif kind == "failed":
            failed += 1
            error_code = item.get("error", {}).get("code")
            if error_code in {"CONFIG_MISSING", "AUTH_OR_RESOURCE", "QUOTA_EXHAUSTED"}:
                stop_code = error_code
        else:
            succeeded += 1
    payload = {
        "ok": failed == 0,
        "provider": PROVIDER,
        "dry_run": dry_run,
        "total": len(planned),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
    if not dry_run:
        payload.pop("dry_run")
    return payload, (0 if failed == 0 else 6)


def _run_one(
    settings: Settings,
    *,
    task_id: str,
    text: str,
    destination: Path,
    voice: ResolvedVoice,
    digest: str,
    resume: bool,
    overwrite: bool,
    retries: int,
    timeout: float,
    client: httpx.Client | None,
) -> tuple[dict[str, Any], str]:
    existed = destination.exists()
    if existed:
        meta = _read_meta(destination, task_id)
        if resume and meta is not None and _matches(destination, meta, digest):
            duration = measure_duration_ms(destination)
            return (
                {
                    "ok": True,
                    "status": "skipped",
                    "task_id": task_id,
                    "output_path": destination.as_posix(),
                    "duration_ms": duration,
                    "reused": True,
                    "input_sha256": digest,
                    "request_id": meta.get("request_id"),
                    "voice_id": voice.voice_id,
                    "resource_id": voice.resource_id,
                },
                "skipped",
            )
        if not (overwrite or (resume and meta is not None)):
            error = CliError("OUTPUT_EXISTS", "output file already exists", task_id=task_id)
            body = error.payload(PROVIDER)
            body["status"] = "failed"
            return body, "failed"
    assert client is not None
    produced = None
    try:
        require_api_key(settings)
        prepare_output(destination, overwrite=overwrite or existed)
        prepare_output(meta_path_for(destination), overwrite=True)
        produced = synthesize(
            settings,
            text=text,
            voice=voice,
            client=client,
            retries=retries,
            timeout=timeout,
        )
        duration = commit_mp3(destination, produced.audio, overwrite=overwrite or existed)
        meta = {
            "version": 1,
            "provider": PROVIDER,
            "tool_version": __version__,
            "task_id": task_id,
            "input_sha256": digest,
            "audio_sha256": sha256_bytes(destination.read_bytes()),
            "duration_ms": duration,
            "request_id": produced.request_id,
            "log_id": produced.log_id,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "params": {
                "voice_id": voice.voice_id,
                "resource_id": voice.resource_id,
                "speed": voice.speed,
                "volume": voice.volume,
                "sample_rate": voice.sample_rate,
                "emotion": voice.emotion,
                "format": "mp3",
            },
            "usage": {"text_words": produced.text_words},
        }
        write_meta(destination, meta)
    except (CliError, OSError) as error:
        exc = error if isinstance(error, CliError) else CliError("OUTPUT_IO", "failed to save output")
        if produced is not None:
            exc.may_have_been_billed = True
            exc.with_context(request_id=produced.request_id, log_id=produced.log_id)
        exc.task_id = task_id
        body = exc.payload(PROVIDER)
        body["status"] = "failed"
        return body, "failed"
    status = "regenerated" if existed else "succeeded"
    return (
        {
            "ok": True,
            "status": status,
            "task_id": task_id,
            "output_path": destination.as_posix(),
            "duration_ms": duration,
            "audio_format": "mp3",
            "sample_rate": voice.sample_rate,
            "voice_id": voice.voice_id,
            "resource_id": voice.resource_id,
            "request_id": produced.request_id,
            "log_id": produced.log_id,
            "usage": {"text_words": produced.text_words},
            "input_sha256": digest,
            "reused": False,
        },
        status,
    )
