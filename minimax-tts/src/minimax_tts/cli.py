from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import typer

from minimax_tts import __version__
from minimax_tts.api import synthesize
from minimax_tts.audio import commit_mp3, ensure_mp3_destination, prepare_output, sha256_bytes
from minimax_tts.batch import run_batch, write_meta
from minimax_tts.errors import EXIT_INTERRUPT, CliError, dumps, redact
from minimax_tts.pairings import list_pairings
from minimax_tts.models import (
    VoicePreset,
    check_bitrate,
    check_emotion,
    check_language,
    check_pitch,
    check_sample_rate,
    check_speed,
    check_volume,
    fingerprint,
    require_text,
)
from minimax_tts.settings import Settings, load_settings, mask_secret, require_api_key, validate_model
from minimax_tts.voices import add_voice, load_voices, remove_voice, resolve_voice, show_voice, voices_path

PROVIDER = "minimax"


app = typer.Typer(add_completion=False, no_args_is_help=True, name="minimax-tts")
voices_app = typer.Typer(add_completion=False, no_args_is_help=True, help="管理本地音色别名")
app.add_typer(voices_app, name="voices")


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def _secrets(settings: Settings) -> list[str]:
    return [settings.api_key] if settings.api_key else []


def _emit(payload: dict[str, Any], json_output: bool) -> None:
    if json_output:
        sys.stdout.write(dumps(payload) + "\n")
        return
    if payload.get("ok") is True and payload.get("output_path"):
        typer.echo(f"wrote {payload['output_path']} ({payload.get('duration_ms')} ms)")
        return
    if payload.get("dry_run") is True and payload.get("ok") is True:
        typer.echo("dry-run ok")
        return
    if "pairings" in payload:
        rule = payload.get("cantonese_rule") or {}
        typer.echo(rule.get("text_rule", ""))
        if rule.get("example"):
            typer.echo(f"示例：{rule['example']}")
        for item in payload["pairings"]:
            typer.echo(
                f"{item['language']}\t{item['scene']}\t{item['name']}\t{item['voice_id']}\t{item['language_boost']}"
            )
        return
    typer.echo(dumps(payload))


def _fail(exc: CliError, json_output: bool, settings: Settings) -> None:
    exc.message = redact(exc.message, _secrets(settings))
    if json_output:
        sys.stdout.write(dumps(exc.payload(PROVIDER)) + "\n")
    else:
        typer.echo(f"error {exc.code}: {exc.message}", err=True)
    raise typer.Exit(exc.exit_code)


def _load(json_output: bool) -> Settings:
    try:
        return load_settings()
    except CliError as exc:
        fallback = Settings(
            api_key=None,
            base_url="https://api.minimaxi.com",
            model="speech-2.8-hd",
            config_dir=Path("."),
        )
        _fail(exc, json_output, fallback)
        raise AssertionError


def _run(json_output: bool, settings: Settings, action: Any) -> None:
    try:
        payload, code = action()
    except CliError as exc:
        _fail(exc, json_output, settings)
    except KeyboardInterrupt:
        typer.echo("interrupted", err=True)
        raise typer.Exit(EXIT_INTERRUPT) from None
    except Exception as exc:
        _fail(CliError("INTERNAL", redact(str(exc), _secrets(settings)) or "internal error"), json_output, settings)
    _emit(payload, json_output)
    if code:
        raise typer.Exit(code)


@contextmanager
def _saving_result(request_id: str, trace_id: str | None, *, task_id: str | None = None) -> Iterator[None]:
    try:
        yield
    except (CliError, OSError) as error:
        exc = error if isinstance(error, CliError) else CliError("OUTPUT_IO", "failed to save output")
        exc.may_have_been_billed = True
        exc.request_id = request_id
        exc.provider_trace_id = trace_id
        exc.task_id = task_id
        if exc is error:
            raise
        raise exc from error


def make_client(timeout: float) -> httpx.Client:
    bounded = max(timeout, 1.0)
    return httpx.Client(
        timeout=httpx.Timeout(connect=min(10.0, bounded), read=bounded, write=min(30.0, bounded), pool=min(10.0, bounded)),
        follow_redirects=False,
    )


def _read_text(text: str | None, text_file: Path | None, use_stdin: bool) -> str:
    if sum([text is not None, text_file is not None, use_stdin]) != 1:
        raise CliError("INVALID_ARGUMENT", "choose exactly one of --text, --text-file, or --stdin")
    if text is not None:
        return require_text(text)
    if text_file is not None:
        try:
            return require_text(text_file.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise CliError("INVALID_ARGUMENT", "text file is unreadable") from exc
    return require_text(sys.stdin.read())


def _bounds(retries: int, timeout: float) -> None:
    if retries < 0 or retries > 8:
        raise CliError("INVALID_ARGUMENT", "retries must be from 0 to 8")
    if timeout <= 0 or timeout > 600:
        raise CliError("INVALID_ARGUMENT", "timeout must be between 0 and 600 seconds")


def _writable(path: Path) -> bool:
    probe: Path | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        candidate = path / f".minimax-tts-write-probe-{uuid.uuid4().hex}"
        descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        probe = candidate
        try:
            os.write(descriptor, b"ok")
        finally:
            os.close(descriptor)
        probe.unlink()
        return True
    except OSError:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main_callback(
    _version: bool = typer.Option(False, "--version", help="显示版本", is_eager=True, callback=_print_version),
) -> None:
    return


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        checks: dict[str, Any] = {
            "api_key_present": bool(settings.api_key),
            "base_url": settings.base_url,
            "model": settings.model,
            "voices_path": voices_path(settings).as_posix(),
            "voices_writable": _writable(settings.config_dir),
        }
        if settings.api_key:
            checks["api_key_masked"] = mask_secret(settings.api_key)
        if output_dir is not None:
            checks["output_dir"] = output_dir.resolve().as_posix()
            checks["output_dir_writable"] = _writable(output_dir)
        try:
            import mutagen  # noqa: F401

            checks["audio_parser"] = "mutagen"
        except ImportError:
            checks["audio_parser"] = None
        problem: CliError | None = None
        if not settings.api_key:
            problem = CliError("CONFIG_MISSING", "API key is missing")
        elif not checks["voices_writable"] or checks.get("output_dir_writable") is False or checks["audio_parser"] is None:
            problem = CliError("OUTPUT_IO", "local dependency or directory is not usable")
        payload: dict[str, Any] = {"ok": problem is None, "provider": PROVIDER, "checks": checks}
        if problem is not None:
            payload["error"] = {"code": problem.code, "message": problem.message, "retryable": False}
            return payload, problem.exit_code
        return payload, 0

    _run(json_output, settings, action)


@app.command()
def synth(
    text: str | None = typer.Option(None, "--text"),
    text_file: Path | None = typer.Option(None, "--text-file"),
    use_stdin: bool = typer.Option(False, "--stdin"),
    voice: str | None = typer.Option(None, "--voice"),
    voice_id: str | None = typer.Option(None, "--voice-id"),
    model: str | None = typer.Option(None, "--model"),
    out: Path = typer.Option(..., "--out"),
    speed: float | None = typer.Option(None, "--speed"),
    volume: float | None = typer.Option(None, "--volume"),
    pitch: int | None = typer.Option(None, "--pitch"),
    emotion: str | None = typer.Option(None, "--emotion"),
    language_boost: str | None = typer.Option(None, "--language-boost"),
    sample_rate: int | None = typer.Option(None, "--sample-rate"),
    bitrate: int | None = typer.Option(None, "--bitrate"),
    task_id: str | None = typer.Option(None, "--task-id"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    retries: int = typer.Option(2, "--retries"),
    timeout: float = typer.Option(120, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        spoken = _read_text(text, text_file, use_stdin)
        resolved = resolve_voice(
            settings,
            alias=voice,
            voice_id=voice_id,
            model=model,
            speed=speed,
            volume=volume,
            pitch=pitch,
            emotion=emotion,
            language_boost=language_boost,
            sample_rate=sample_rate,
            bitrate=bitrate,
        )
        destination = ensure_mp3_destination(out if out.is_absolute() else Path.cwd() / out).resolve()
        chosen_id = task_id or destination.stem
        digest = fingerprint(resolved, spoken)
        _bounds(retries, timeout)
        if dry_run:
            return (
                {
                    "ok": True,
                    "dry_run": True,
                    "provider": PROVIDER,
                    "task_id": chosen_id,
                    "planned_output": destination.as_posix(),
                    "output_exists": destination.exists(),
                    "voice_id": resolved.voice_id,
                    "model": resolved.model,
                    "sample_rate": resolved.sample_rate,
                    "language_boost": resolved.language_boost,
                    "audio_format": "mp3",
                    "input_sha256": digest,
                },
                0,
            )
        if destination.exists() and not overwrite:
            raise CliError("OUTPUT_EXISTS", "output file already exists", task_id=chosen_id)
        prepare_output(destination, overwrite=overwrite)
        prepare_output(destination.with_suffix(".meta.json"), overwrite=True)
        require_api_key(settings)
        with make_client(timeout) as client:
            produced = synthesize(settings, text=spoken, voice=resolved, client=client, retries=retries, timeout=timeout)
        with _saving_result(produced.request_id, produced.trace_id, task_id=chosen_id):
            duration = commit_mp3(destination, produced.audio, overwrite=overwrite)
            write_meta(
                destination,
                {
                    "version": 1,
                    "provider": PROVIDER,
                    "tool_version": __version__,
                    "task_id": chosen_id,
                    "input_sha256": digest,
                    "audio_sha256": sha256_bytes(destination.read_bytes()),
                    "duration_ms": duration,
                    "request_id": produced.request_id,
                    "provider_trace_id": produced.trace_id,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "params": resolved.model_dump(),
                    "usage": {"usage_characters": produced.usage_characters},
                },
            )
        return (
            {
                "ok": True,
                "provider": PROVIDER,
                "task_id": chosen_id,
                "output_path": destination.as_posix(),
                "duration_ms": duration,
                "audio_format": "mp3",
                "sample_rate": resolved.sample_rate,
                "model": resolved.model,
                "voice_id": resolved.voice_id,
                "request_id": produced.request_id,
                "provider_trace_id": produced.trace_id,
                "usage": {"usage_characters": produced.usage_characters},
                "input_sha256": digest,
                "reused": False,
            },
            0,
        )

    _run(json_output, settings, action)


@app.command("batch")
def batch_command(
    manifest: Path = typer.Option(..., "--manifest"),
    output_dir: Path = typer.Option(..., "--output-dir"),
    resume: bool = typer.Option(False, "--resume"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    retries: int = typer.Option(2, "--retries"),
    timeout: float = typer.Option(120, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        _bounds(retries, timeout)
        client = None if dry_run else make_client(timeout)
        try:
            return run_batch(
                settings,
                manifest_path=manifest,
                output_dir=output_dir,
                resume=resume,
                overwrite=overwrite,
                dry_run=dry_run,
                retries=retries,
                timeout=timeout,
                client=client,
            )
        finally:
            if client is not None:
                client.close()

    _run(json_output, settings, action)


@voices_app.command("add")
def voices_add(
    name: str = typer.Argument(...),
    voice_id: str = typer.Option(..., "--voice-id"),
    model: str = typer.Option("speech-2.8-hd", "--model"),
    speed: float = typer.Option(1.0, "--speed"),
    volume: float = typer.Option(1.0, "--volume"),
    pitch: int = typer.Option(0, "--pitch"),
    language_boost: str = typer.Option("auto", "--language-boost"),
    sample_rate: int = typer.Option(32000, "--sample-rate"),
    bitrate: int = typer.Option(128000, "--bitrate"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        chosen = validate_model(model)
        preset = VoicePreset(
            voice_id=voice_id,
            model=chosen,
            speed=check_speed(speed),
            volume=check_volume(volume),
            pitch=check_pitch(pitch),
            language_boost=check_language(language_boost),
            sample_rate=check_sample_rate(sample_rate),
            bitrate=check_bitrate(bitrate),
        )
        check_emotion(preset.emotion, preset.model)
        add_voice(settings, name, preset)
        return {"ok": True, "provider": PROVIDER, "voice": {"name": name, **preset.model_dump()}}, 0

    _run(json_output, settings, action)


@voices_app.command("pairings")
def voices_pairings(
    language: str | None = typer.Option(None, "--language", help="zh、en 或 yue"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列出系统音色搭配和可选的粤语建议。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        return list_pairings(language), 0

    _run(json_output, settings, action)


@voices_app.command("list")
def voices_list(json_output: bool = typer.Option(False, "--json")) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        store = load_voices(settings)
        items = [{"name": name, **preset.model_dump()} for name, preset in sorted(store.voices.items())]
        return {"ok": True, "provider": PROVIDER, "voices": items}, 0

    _run(json_output, settings, action)


@voices_app.command("show")
def voices_show(name: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json")) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        preset = show_voice(settings, name)
        return {"ok": True, "provider": PROVIDER, "voice": {"name": name, **preset.model_dump()}}, 0

    _run(json_output, settings, action)


@voices_app.command("remove")
def voices_remove(name: str = typer.Argument(...), json_output: bool = typer.Option(False, "--json")) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        remove_voice(settings, name)
        return {"ok": True, "provider": PROVIDER, "removed": name}, 0

    _run(json_output, settings, action)


def main() -> None:
    _configure_stdio()
    app()
