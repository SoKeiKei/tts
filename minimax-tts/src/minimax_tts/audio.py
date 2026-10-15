from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mutagen.mp3 import MP3, HeaderNotFoundError

from minimax_tts.errors import CliError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_mp3(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data.startswith(b"ID3"):
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def measure_duration_ms(path: Path) -> int:
    try:
        audio = MP3(path)
        seconds = float(audio.info.length)
    except (HeaderNotFoundError, OSError, ValueError) as exc:
        raise CliError("INVALID_AUDIO", "MP3 duration could not be measured") from exc
    if seconds <= 0:
        raise CliError("INVALID_AUDIO", "MP3 duration could not be measured")
    return int(round(seconds * 1000))


def validate_mp3_bytes(data: bytes) -> None:
    if not data or not looks_like_mp3(data):
        raise CliError("INVALID_AUDIO", "provider audio is not a valid MP3")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        with partial.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CliError("OUTPUT_IO", "failed to write output file") from exc


def commit_mp3(path: Path, data: bytes) -> int:
    validate_mp3_bytes(data)
    partial = path.with_name(path.name + ".partial")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with partial.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            duration = measure_duration_ms(partial)
        except CliError:
            partial.unlink(missing_ok=True)
            raise
        os.replace(partial, path)
    except CliError:
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CliError("OUTPUT_IO", "failed to write output file") from exc
    return duration


def ensure_mp3_destination(path: Path) -> Path:
    if path.suffix.lower() != ".mp3":
        raise CliError("INVALID_ARGUMENT", "output file must use the .mp3 suffix")
    return path
