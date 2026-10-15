from __future__ import annotations

import hashlib
import os
import secrets
import stat
import tempfile
from pathlib import Path
from pathlib import PureWindowsPath

from mutagen.mp3 import MP3, HeaderNotFoundError

from doubao_tts.errors import CliError


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


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in "¹²³"),
    *(f"LPT{number}" for number in "¹²³"),
}


def _validate_windows_path(path: Path) -> None:
    if os.name != "nt":
        return
    windows_path = PureWindowsPath(str(path))
    for part in windows_path.parts:
        if part in {windows_path.anchor, ".", ".."}:
            continue
        if any(ord(char) < 32 or char in '<>:"|?*' for char in part):
            raise CliError("INVALID_ARGUMENT", "output path contains unsupported Windows characters")
        if part.endswith((".", " ")):
            raise CliError("INVALID_ARGUMENT", "Windows path components cannot end with a dot or space")
        basename = part.split(".", 1)[0].rstrip(" .").upper()
        if basename in _WINDOWS_RESERVED_NAMES:
            raise CliError("INVALID_ARGUMENT", "output path contains a reserved Windows device name")


def _ensure_output_directory(path: Path) -> None:
    _validate_windows_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError("OUTPUT_IO", "output directory is not writable") from exc
    if not path.parent.is_dir():
        raise CliError("OUTPUT_IO", "output directory is not writable")


def _is_read_only(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    if os.name == "nt":
        return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1))
    return not bool(info.st_mode & 0o222) or not os.access(path, os.W_OK)


def _probe_publish(directory: Path, *, overwrite: bool) -> None:
    source_fd: int | None = None
    destination_fd: int | None = None
    source: Path | None = None
    destination: Path | None = None
    try:
        source_fd, source_name = tempfile.mkstemp(prefix=".tts-probe-", suffix=".tmp", dir=directory)
        source = Path(source_name)
        with os.fdopen(source_fd, "wb") as handle:
            source_fd = None
            handle.write(b"tts output probe")
            handle.flush()
            os.fsync(handle.fileno())

        if overwrite:
            destination_fd, destination_name = tempfile.mkstemp(
                prefix=".tts-probe-", suffix=".tmp", dir=directory
            )
            destination = Path(destination_name)
            os.close(destination_fd)
            destination_fd = None
            os.replace(source, destination)
            return

        for _attempt in range(8):
            candidate = directory / f".tts-probe-{secrets.token_hex(16)}.tmp"
            try:
                os.link(source, candidate)
            except FileExistsError:
                continue
            destination = candidate
            break
        else:
            raise FileExistsError("could not reserve a unique output probe path")
    except OSError as exc:
        raise CliError("OUTPUT_IO", "output directory cannot publish files") from exc
    finally:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        if destination_fd is not None:
            try:
                os.close(destination_fd)
            except OSError:
                pass
        for owned_path in (source, destination):
            if owned_path is not None:
                try:
                    owned_path.unlink(missing_ok=True)
                except OSError:
                    pass


def prepare_output(path: Path, *, overwrite: bool = False) -> None:
    _validate_windows_path(path)
    if path.is_dir():
        raise CliError("OUTPUT_IO", "output path is a directory")
    exists = path.exists() or path.is_symlink()
    if exists and not overwrite:
        raise CliError("OUTPUT_EXISTS", "output file already exists")
    if exists and overwrite and _is_read_only(path):
        raise CliError("OUTPUT_IO", "existing output file is read-only")
    _ensure_output_directory(path)
    _probe_publish(path.parent, overwrite=overwrite)


def _publish(partial: Path, path: Path, *, overwrite: bool) -> None:
    if overwrite:
        os.replace(partial, path)
        return
    try:
        os.link(partial, path)
    except FileExistsError as exc:
        raise CliError(
            "OUTPUT_EXISTS",
            "output file already exists",
            may_have_been_billed=True,
        ) from exc


def _create_partial(path: Path) -> tuple[int, Path]:
    try:
        descriptor, name = tempfile.mkstemp(prefix=".tts-", suffix=".partial", dir=path.parent)
    except OSError as exc:
        raise CliError("OUTPUT_IO", "failed to create output file") from exc
    return descriptor, Path(name)


def _write_and_sync(descriptor: int, data: bytes) -> None:
    try:
        handle = os.fdopen(descriptor, "wb")
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    with handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, data: bytes) -> None:
    _ensure_output_directory(path)
    descriptor: int | None = None
    partial: Path | None = None
    try:
        descriptor, partial = _create_partial(path)
        write_descriptor, descriptor = descriptor, None
        _write_and_sync(write_descriptor, data)
        _publish(partial, path, overwrite=True)
    except OSError as exc:
        raise CliError("OUTPUT_IO", "failed to write output file") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass


def commit_mp3(path: Path, data: bytes, *, overwrite: bool = False) -> int:
    validate_mp3_bytes(data)
    _ensure_output_directory(path)
    descriptor: int | None = None
    partial: Path | None = None
    try:
        descriptor, partial = _create_partial(path)
        write_descriptor, descriptor = descriptor, None
        _write_and_sync(write_descriptor, data)
        duration = measure_duration_ms(partial)
        _publish(partial, path, overwrite=overwrite)
    except OSError as exc:
        raise CliError("OUTPUT_IO", "failed to write output file") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
    return duration


def ensure_mp3_destination(path: Path) -> Path:
    _validate_windows_path(path)
    if path.suffix.lower() != ".mp3":
        raise CliError("INVALID_ARGUMENT", "output file must use the .mp3 suffix")
    return path
