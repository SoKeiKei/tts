from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import typer

from doubao_tts import __version__
from doubao_tts.api import synthesize
from doubao_tts.audio import commit_mp3, ensure_mp3_destination, prepare_output, sha256_bytes
from doubao_tts.batch import run_batch, write_meta
from doubao_tts.errors import EXIT_INTERRUPT, CliError, dumps, redact
from doubao_tts.models import fingerprint, require_text
from doubao_tts.pairings import list_pairings
from doubao_tts.script_rules import reject_cantonese_text
from doubao_tts.settings import Settings, load_settings, mask_secret, require_api_key
from doubao_tts.studio import clone_voice, create_audio, design_voice, get_voice
from doubao_tts.voices import add_voice, load_voices, remove_voice, resolve_voice, show_voice

PROVIDER = "doubao"
app = typer.Typer(add_completion=False, no_args_is_help=True, name="doubao-tts")
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


def _load(json_output: bool) -> Settings:
    try:
        return load_settings()
    except CliError as exc:
        fallback = Settings(
            api_key=None,
            resource_id="seed-tts-2.0",
            base_url="https://openspeech.bytedance.com",
            config_dir=Path("."),
        )
        _fail(exc, json_output, fallback)
        raise AssertionError


def _secrets(settings: Settings) -> list[str]:
    return [settings.api_key] if settings.api_key else []


def _emit(payload: dict[str, Any], json_output: bool, settings: Settings) -> None:
    if json_output:
        sys.stdout.write(dumps(payload) + "\n")
        return
    if payload.get("ok") is True and payload.get("output_path"):
        typer.echo(f"wrote {payload['output_path']} ({payload.get('duration_ms')} ms)")
        return
    if payload.get("ok") is True and payload.get("dry_run") is True:
        typer.echo("dry-run ok")
        return
    if "pairings" in payload:
        rule = payload.get("cantonese_rule") or {}
        typer.echo(rule.get("text_rule", ""))
        if rule.get("example"):
            typer.echo(f"示例：{rule['example']}")
        for item in payload["pairings"]:
            extra = item.get("resource_id") or item.get("language_boost") or ""
            typer.echo(f"{item['language']}\t{item['scene']}\t{item['name']}\t{item['voice_id']}\t{extra}")
        if not payload["pairings"]:
            typer.echo("此工具不提供粤语配音。")
        return
    typer.echo(dumps(payload))


def _fail(exc: CliError, json_output: bool, settings: Settings) -> None:
    message = redact(exc.message, _secrets(settings))
    exc.message = message
    if json_output:
        sys.stdout.write(dumps(exc.payload(PROVIDER)) + "\n")
    else:
        typer.echo(f"error {exc.code}: {message}", err=True)
    raise typer.Exit(exc.exit_code)


def _run(json_output: bool, settings: Settings, action: Any) -> None:
    try:
        payload, code = action()
    except CliError as exc:
        _fail(exc, json_output, settings)
    except KeyboardInterrupt:
        typer.echo("interrupted", err=True)
        raise typer.Exit(EXIT_INTERRUPT) from None
    except Exception as exc:
        hidden = redact(str(exc), _secrets(settings))
        _fail(CliError("INTERNAL", hidden or "internal error"), json_output, settings)
    _emit(payload, json_output, settings)
    if code:
        raise typer.Exit(code)


@contextmanager
def _saving_result(
    request_id: str,
    log_id: str | None,
    *,
    task_id: str | None = None,
    may_have_been_billed: bool = True,
) -> Iterator[None]:
    try:
        yield
    except (CliError, OSError) as error:
        exc = error if isinstance(error, CliError) else CliError("OUTPUT_IO", "failed to save output")
        exc.may_have_been_billed = exc.may_have_been_billed or may_have_been_billed
        exc.with_context(request_id=request_id, log_id=log_id, task_id=task_id)
        if exc is error:
            raise
        raise exc from error


def make_client(timeout: float) -> httpx.Client:
    bounded = max(timeout, 1.0)
    return httpx.Client(
        timeout=httpx.Timeout(connect=min(10.0, bounded), read=min(30.0, bounded), write=min(30.0, bounded), pool=min(10.0, bounded)),
        follow_redirects=False,
    )


def _read_text(text: str | None, text_file: Path | None, use_stdin: bool) -> str:
    chosen = [text is not None, text_file is not None, use_stdin]
    if sum(chosen) != 1:
        raise CliError("INVALID_ARGUMENT", "choose exactly one of --text, --text-file, or --stdin")
    if text is not None:
        return require_text(text)
    if text_file is not None:
        try:
            return require_text(text_file.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise CliError("INVALID_ARGUMENT", "text file is unreadable") from exc
    return require_text(sys.stdin.read())


def _check_retries(retries: int) -> int:
    if retries < 0 or retries > 8:
        raise CliError("INVALID_ARGUMENT", "retries must be from 0 to 8")
    return retries


def _check_timeout(timeout: float) -> float:
    if timeout <= 0 or timeout > 600:
        raise CliError("INVALID_ARGUMENT", "timeout must be between 0 and 600 seconds")
    return timeout


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
    json_output: bool = typer.Option(False, "--json", help="只向 stdout 输出一个 JSON 对象"),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="检查该目录是否可写"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        checks: dict[str, Any] = {
            "api_key_present": bool(settings.api_key),
            "base_url": settings.base_url,
            "resource_id": settings.resource_id,
            "voices_path": voices_path(settings).as_posix(),
        }
        if settings.api_key:
            checks["api_key_masked"] = mask_secret(settings.api_key)
        writable, voices_error = _writable_dir(settings.config_dir)
        checks["voices_writable"] = writable
        if output_dir is not None:
            out_ok, out_error = _writable_dir(output_dir)
            checks["output_dir"] = output_dir.resolve().as_posix()
            checks["output_dir_writable"] = out_ok
        else:
            out_error = None
        try:
            import mutagen  # noqa: F401

            checks["audio_parser"] = "mutagen"
        except ImportError:
            checks["audio_parser"] = None
        problems: list[CliError] = []
        if not settings.api_key:
            problems.append(CliError("CONFIG_MISSING", "API key is missing"))
        if not writable:
            problems.append(CliError("OUTPUT_IO", voices_error or "config directory is not writable"))
        if out_error:
            problems.append(CliError("OUTPUT_IO", out_error))
        if checks["audio_parser"] is None:
            problems.append(CliError("OUTPUT_IO", "mutagen is not installed"))
        payload: dict[str, Any] = {"ok": not problems, "provider": PROVIDER, "checks": checks}
        if problems:
            payload["error"] = {
                "code": problems[0].code,
                "message": problems[0].message,
                "retryable": False,
            }
            return payload, problems[0].exit_code
        return payload, 0

    _run(json_output, settings, action)


def voices_path(settings: Settings) -> Path:
    from doubao_tts.voices import voices_path as path_for

    return path_for(settings)


def _writable_dir(path: Path) -> tuple[bool, str | None]:
    probe: Path | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        candidate = path / f".doubao-tts-write-probe-{uuid.uuid4().hex}"
        descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        probe = candidate
        try:
            os.write(descriptor, b"ok")
        finally:
            os.close(descriptor)
        probe.unlink()
        probe = None
    except OSError:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        return False, "directory is not writable"
    return True, None


@app.command()
def synth(
    text: str | None = typer.Option(None, "--text", help="要合成的文本"),
    text_file: Path | None = typer.Option(None, "--text-file", help="UTF-8 文本文件"),
    use_stdin: bool = typer.Option(False, "--stdin", help="从标准输入读取文本"),
    voice: str | None = typer.Option(None, "--voice", help="本地音色别名"),
    voice_id: str | None = typer.Option(None, "--voice-id", help="豆包 speaker ID"),
    resource: str | None = typer.Option(None, "--resource", help="X-Api-Resource-Id"),
    out: Path = typer.Option(..., "--out", help="输出 MP3 的路径"),
    speed: int | None = typer.Option(None, "--speed", help="语速，-50 到 100，默认 0"),
    volume: int | None = typer.Option(None, "--volume", help="音量，-50 到 100，默认 0"),
    sample_rate: int | None = typer.Option(None, "--sample-rate", help="采样率，默认 24000"),
    emotion: str | None = typer.Option(None, "--emotion", help="可选情绪，仅部分音色有效"),
    task_id: str | None = typer.Option(None, "--task-id", help="任务 ID，默认使用文件名"),
    overwrite: bool = typer.Option(False, "--overwrite", help="允许覆盖已有文件"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只检查参数，不调用接口"),
    retries: int = typer.Option(2, "--retries", help="临时失败的额外重试次数"),
    timeout: float = typer.Option(120, "--timeout", help="单次请求总超时（秒）"),
    json_output: bool = typer.Option(False, "--json", help="只向 stdout 输出一个 JSON 对象"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        spoken = _read_text(text, text_file, use_stdin)
        reject_cantonese_text(spoken)
        resolved = resolve_voice(
            settings,
            alias=voice,
            voice_id=voice_id,
            resource_id=resource,
            speed=speed,
            volume=volume,
            sample_rate=sample_rate,
            emotion=emotion,
        )
        destination = ensure_mp3_destination(out if out.is_absolute() else (Path.cwd() / out))
        destination = destination.resolve()
        chosen_id = task_id or destination.stem
        digest = fingerprint(
            text=spoken,
            voice_id=resolved.voice_id,
            resource_id=resolved.resource_id,
            speed=resolved.speed,
            volume=resolved.volume,
            sample_rate=resolved.sample_rate,
            emotion=resolved.emotion,
        )
        _check_retries(retries)
        _check_timeout(timeout)
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
                    "resource_id": resolved.resource_id,
                    "sample_rate": resolved.sample_rate,
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
            produced = synthesize(
                settings,
                text=spoken,
                voice=resolved,
                client=client,
                retries=retries,
                timeout=timeout,
            )
        with _saving_result(produced.request_id, produced.log_id, task_id=chosen_id):
            duration = commit_mp3(destination, produced.audio, overwrite=overwrite)
            from datetime import datetime, timezone

            from doubao_tts import __version__ as tool_version

            write_meta(
                destination,
                {
                    "version": 1,
                    "provider": PROVIDER,
                    "tool_version": tool_version,
                    "task_id": chosen_id,
                    "input_sha256": digest,
                    "audio_sha256": sha256_bytes(destination.read_bytes()),
                    "duration_ms": duration,
                    "request_id": produced.request_id,
                    "log_id": produced.log_id,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "params": {
                        "voice_id": resolved.voice_id,
                        "resource_id": resolved.resource_id,
                        "speed": resolved.speed,
                        "volume": resolved.volume,
                        "sample_rate": resolved.sample_rate,
                        "emotion": resolved.emotion,
                        "format": "mp3",
                    },
                    "usage": {"text_words": produced.text_words},
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
                "voice_id": resolved.voice_id,
                "resource_id": resolved.resource_id,
                "request_id": produced.request_id,
                "log_id": produced.log_id,
                "usage": {"text_words": produced.text_words},
                "input_sha256": digest,
                "reused": False,
            },
            0,
        )

    _run(json_output, settings, action)


@app.command("batch")
def batch_command(
    manifest: Path = typer.Option(..., "--manifest", help="批量任务 JSON"),
    output_dir: Path = typer.Option(..., "--output-dir", help="音频输出目录"),
    resume: bool = typer.Option(False, "--resume", help="跳过已校验的成品，只重做变化项"),
    overwrite: bool = typer.Option(False, "--overwrite", help="允许覆盖不是本工具管理的已有文件"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只检查清单，不调用接口"),
    retries: int = typer.Option(2, "--retries", help="临时失败的额外重试次数"),
    timeout: float = typer.Option(120, "--timeout", help="单次请求总超时（秒）"),
    json_output: bool = typer.Option(False, "--json", help="只向 stdout 输出一个 JSON 对象"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        _check_retries(retries)
        _check_timeout(timeout)
        client: httpx.Client | None = None
        if not dry_run:
            client = make_client(timeout)
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


def _long_client(timeout: float) -> httpx.Client:
    bounded = max(timeout, 1.0)
    return httpx.Client(
        timeout=httpx.Timeout(connect=min(10.0, bounded), read=bounded, write=bounded, pool=min(10.0, bounded)),
        follow_redirects=False,
    )


def _destination(out: Path) -> Path:
    path = out if out.is_absolute() else Path.cwd() / out
    return ensure_mp3_destination(path).resolve()


@app.command("create")
def create_command(
    prompt: str = typer.Option(..., "--prompt", help="环境音、对白和音效写在同一段提示词里"),
    out: Path = typer.Option(..., "--out"),
    speaker: list[str] | None = typer.Option(None, "--speaker", help="参考音色，可重复，最多 3 个参考"),
    reference_audio: list[Path] | None = typer.Option(None, "--reference-audio", help="参考音频，可重复"),
    sample_rate: int = typer.Option(48000, "--sample-rate"),
    speech_rate: int = typer.Option(0, "--speech-rate"),
    loudness_rate: int = typer.Option(0, "--loudness-rate"),
    pitch_rate: int = typer.Option(0, "--pitch-rate"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    timeout: float = typer.Option(180, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """用 seed-audio-1.0 一次生成环境音、配音和音效。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        _check_timeout(timeout)
        destination = _destination(out)
        if destination.exists() and not overwrite:
            raise CliError("OUTPUT_EXISTS", "output file already exists")
        prepare_output(destination, overwrite=overwrite)
        require_api_key(settings)
        with _long_client(timeout) as client:
            produced = create_audio(
                settings,
                client=client,
                prompt=prompt,
                speakers=speaker or [],
                reference_audios=reference_audio or [],
                sample_rate=sample_rate,
                speech_rate=speech_rate,
                loudness_rate=loudness_rate,
                pitch_rate=pitch_rate,
                timeout=timeout,
            )
        with _saving_result(produced["request_id"], produced["log_id"]):
            duration = commit_mp3(destination, produced["audio"], overwrite=overwrite)
        return (
            {
                "ok": True,
                "provider": PROVIDER,
                "kind": "audio-generation",
                "model": produced["model"],
                "output_path": destination.as_posix(),
                "duration_ms": duration,
                "original_duration_ms": produced["original_duration_ms"],
                "audio_format": "mp3",
                "sample_rate": sample_rate,
                "request_id": produced["request_id"],
                "log_id": produced["log_id"],
            },
            0,
        )

    _run(json_output, settings, action)


@app.command("clone")
def clone_command(
    audio: Path = typer.Option(..., "--audio", help="复刻样本，最大 10MB"),
    speaker_id: str = typer.Option(..., "--speaker-id", help="控制台音色槽位；后付费时填 custom_speaker_id"),
    out: Path | None = typer.Option(None, "--out", help="可选，保存试听 MP3"),
    custom_speaker_id: str | None = typer.Option(None, "--custom-speaker-id"),
    language: str = typer.Option("cn", "--language"),
    demo_text: str | None = typer.Option(None, "--demo-text"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    timeout: float = typer.Option(180, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """上传样本做声音复刻。正式合成会收取音色槽位费，确认试听后再 synth。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        return _run_voice_job(
            settings,
            timeout=timeout,
            out=out,
            overwrite=overwrite,
            call=lambda client: clone_voice(
                settings,
                client=client,
                audio_path=audio,
                speaker_id=speaker_id,
                custom_speaker_id=custom_speaker_id,
                language=language,
                demo_text=demo_text,
                timeout=timeout,
            ),
            kind="voice-clone",
        )

    _run(json_output, settings, action)


@app.command("design")
def design_command(
    speaker_id: str = typer.Option(..., "--speaker-id"),
    text: str = typer.Option(..., "--text", help="试听文本，最多 300 字"),
    prompt: str | None = typer.Option(None, "--prompt", help="音色描述，最多 200 字"),
    image: Path | None = typer.Option(None, "--image"),
    image_url: str | None = typer.Option(None, "--image-url"),
    out: Path | None = typer.Option(None, "--out"),
    language: str = typer.Option("cn", "--language", help="cn 或 en"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    timeout: float = typer.Option(180, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """按文字或图片设计音色。设计成功后用 seed-icl-2.0 做语音合成。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        return _run_voice_job(
            settings,
            timeout=timeout,
            out=out,
            overwrite=overwrite,
            call=lambda client: design_voice(
                settings,
                client=client,
                speaker_id=speaker_id,
                text=text,
                text_prompt=prompt,
                image_path=image,
                image_url=image_url,
                language=language,
                timeout=timeout,
            ),
            kind="voice-design",
        )

    _run(json_output, settings, action)


@app.command("voice-status")
def voice_status_command(
    speaker_id: str = typer.Option("", "--speaker-id"),
    custom_speaker_id: str | None = typer.Option(None, "--custom-speaker-id"),
    out: Path | None = typer.Option(None, "--out"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    timeout: float = typer.Option(60, "--timeout"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """查询复刻或设计音色的训练状态。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        return _run_voice_job(
            settings,
            timeout=timeout,
            out=out,
            overwrite=overwrite,
            call=lambda client: get_voice(
                settings,
                client=client,
                speaker_id=speaker_id,
                custom_speaker_id=custom_speaker_id,
                timeout=timeout,
            ),
            kind="voice-status",
        )

    _run(json_output, settings, action)


def _run_voice_job(
    settings: Settings,
    *,
    timeout: float,
    out: Path | None,
    overwrite: bool,
    call: Any,
    kind: str,
) -> tuple[dict[str, Any], int]:
    _check_timeout(timeout)
    destination = _destination(out) if out is not None else None
    if destination is not None and destination.exists() and not overwrite:
        raise CliError("OUTPUT_EXISTS", "output file already exists")
    if destination is not None:
        prepare_output(destination, overwrite=overwrite)
    require_api_key(settings)
    with _long_client(timeout) as client:
        produced = call(client)
    payload: dict[str, Any] = {
        "ok": True,
        "provider": PROVIDER,
        "kind": kind,
        "speaker_id": produced["speaker_id"],
        "status": produced["status"],
        "status_name": produced["status_name"],
        "ready_for_tts": produced["ready_for_tts"],
        "available_training_times": produced["available_training_times"],
        "request_id": produced["request_id"],
        "log_id": produced["log_id"],
        "resource_id_for_synth": "seed-icl-2.0",
    }
    if produced["audio"] is not None and destination is not None:
        with _saving_result(
            produced["request_id"], produced["log_id"], may_have_been_billed=kind != "voice-status"
        ):
            duration = commit_mp3(destination, produced["audio"], overwrite=overwrite)
        payload["output_path"] = destination.as_posix()
        payload["duration_ms"] = duration
    return payload, 0


@voices_app.command("add")
def voices_add(
    name: str = typer.Argument(..., help="本地别名"),
    voice_id: str = typer.Option(..., "--voice-id", help="豆包 speaker ID"),
    resource: str = typer.Option("seed-tts-2.0", "--resource", help="资源 ID"),
    speed: int = typer.Option(0, "--speed"),
    volume: int = typer.Option(0, "--volume"),
    sample_rate: int = typer.Option(24000, "--sample-rate"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        preset = add_voice(
            settings,
            name,
            voice_id=voice_id,
            resource_id=resource,
            speed=speed,
            volume=volume,
            sample_rate=sample_rate,
        )
        return (
            {
                "ok": True,
                "provider": PROVIDER,
                "voice": {"name": name, **preset.model_dump()},
            },
            0,
        )

    _run(json_output, settings, action)


@voices_app.command("pairings")
def voices_pairings(
    language: str | None = typer.Option(None, "--language", help="zh、en 或 yue"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列出配音可用的声音搭配。粤语不在此列表。"""
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        return list_pairings(language), 0

    _run(json_output, settings, action)


@voices_app.command("list")
def voices_list(json_output: bool = typer.Option(False, "--json")) -> None:
    settings = _load(json_output)

    def action() -> tuple[dict[str, Any], int]:
        store = load_voices(settings)
        items = [
            {"name": name, **preset.model_dump()}
            for name, preset in sorted(store.voices.items())
        ]
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
