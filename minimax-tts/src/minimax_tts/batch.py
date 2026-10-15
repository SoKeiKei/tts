from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from minimax_tts import __version__
from minimax_tts.api import synthesize
from minimax_tts.audio import atomic_write, commit_mp3, ensure_mp3_destination, measure_duration_ms, sha256_bytes
from minimax_tts.errors import CliError
from minimax_tts.models import Manifest, ManifestTask, ResolvedVoice, fingerprint, require_text
from minimax_tts.script_rules import require_cantonese_colloquial, require_cantonese_pairing
from minimax_tts.settings import Settings, require_api_key
from minimax_tts.voices import resolve_voice

PROVIDER = "minimax"


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
    if not _inside(manifest_path.parent.resolve(), source):
        raise CliError("INVALID_ARGUMENT", f"task {task.id} text_file must stay next to the manifest")
    try:
        return require_text(source.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise CliError("INVALID_ARGUMENT", f"task {task.id} text_file is unreadable") from exc


def _check_task_id(task_id: str) -> None:
    if not task_id[0].isalnum() or any(not (ch.isalnum() or ch in "._-") for ch in task_id):
        raise CliError("INVALID_ARGUMENT", f"task id is invalid: {task_id}")


def resolve_task(settings: Settings, task: ManifestTask, defaults: Any, manifest_path: Path, output_dir: Path):
    _check_task_id(task.id)
    text = task_text(task, manifest_path)
    voice = resolve_voice(
        settings,
        alias=task.voice if task.voice is not None else defaults.voice,
        voice_id=task.voice_id if task.voice_id is not None else defaults.voice_id,
        model=task.model if task.model is not None else defaults.model,
        speed=task.speed if task.speed is not None else defaults.speed,
        volume=task.volume if task.volume is not None else defaults.volume,
        pitch=task.pitch if task.pitch is not None else defaults.pitch,
        emotion=task.emotion if task.emotion is not None else defaults.emotion,
        language_boost=task.language_boost if task.language_boost is not None else defaults.language_boost,
        sample_rate=task.sample_rate if task.sample_rate is not None else defaults.sample_rate,
        bitrate=task.bitrate if task.bitrate is not None else defaults.bitrate,
    )
    require_cantonese_pairing(voice.voice_id, voice.language_boost)
    if voice.language_boost == "Chinese,Yue":
        require_cantonese_colloquial(text)
    destination = safe_output(output_dir, task.output)
    return text, destination, voice, fingerprint(voice, text)


def _read_meta(path: Path, task_id: str) -> dict[str, Any] | None:
    meta = meta_path_for(path)
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
    atomic_write(meta_path_for(path), (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


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
    planned = []
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
    succeeded = failed = skipped = 0
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
                    "model": voice.model,
                    "language_boost": voice.language_boost,
                }
            )
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
        else:
            succeeded += 1
    payload: dict[str, Any] = {
        "ok": failed == 0,
        "provider": PROVIDER,
        "total": len(planned),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
    if dry_run:
        payload["dry_run"] = True
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
            return (
                {
                    "ok": True,
                    "status": "skipped",
                    "task_id": task_id,
                    "output_path": destination.as_posix(),
                    "duration_ms": measure_duration_ms(destination),
                    "reused": True,
                    "input_sha256": digest,
                    "request_id": meta.get("request_id"),
                    "provider_trace_id": meta.get("provider_trace_id"),
                    "voice_id": voice.voice_id,
                    "model": voice.model,
                },
                "skipped",
            )
        if not (overwrite or (resume and meta is not None)):
            body = CliError("OUTPUT_EXISTS", "output file already exists", task_id=task_id).payload(PROVIDER)
            body["status"] = "failed"
            return body, "failed"
    assert client is not None
    require_api_key(settings)
    try:
        produced = synthesize(settings, text=text, voice=voice, client=client, retries=retries, timeout=timeout)
        duration = commit_mp3(destination, produced.audio)
        write_meta(
            destination,
            {
                "version": 1,
                "provider": PROVIDER,
                "tool_version": __version__,
                "task_id": task_id,
                "input_sha256": digest,
                "audio_sha256": sha256_bytes(destination.read_bytes()),
                "duration_ms": duration,
                "request_id": produced.request_id,
                "provider_trace_id": produced.trace_id,
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "params": produced.voice.model_dump(),
                "usage": {"usage_characters": produced.usage_characters},
            },
        )
    except CliError as exc:
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
            "model": voice.model,
            "voice_id": voice.voice_id,
            "request_id": produced.request_id,
            "provider_trace_id": produced.trace_id,
            "usage": {"usage_characters": produced.usage_characters},
            "input_sha256": digest,
            "reused": False,
        },
        status,
    )
