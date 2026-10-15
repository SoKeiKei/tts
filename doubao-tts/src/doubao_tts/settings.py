from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from pydantic import BaseModel

from doubao_tts.errors import CliError

OFFICIAL_HOST = "openspeech.bytedance.com"
DEFAULT_BASE_URL = "https://openspeech.bytedance.com"
DEFAULT_RESOURCE = "seed-tts-2.0"
ENDPOINT_PATH = "/api/v3/tts/unidirectional"

ALLOWED_RESOURCES = {
    "seed-tts-2.0",
    "seed-tts-1.0",
    "seed-tts-1.0-concurr",
    "seed-icl-2.0",
    "seed-icl-1.0",
    "seed-icl-1.0-concurr",
}
ALLOWED_SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}


class Settings(BaseModel):
    api_key: str | None = None
    resource_id: str = DEFAULT_RESOURCE
    base_url: str = DEFAULT_BASE_URL
    config_dir: Path
    allow_test_base: bool = False

    model_config = {"arbitrary_types_allowed": True}


def config_dir() -> Path:
    override = os.environ.get("DOUBAO_TTS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "doubao-tts"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "doubao-tts"
    return Path.home() / ".config" / "doubao-tts"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (Path.cwd() / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def validate_base_url(url: str, *, allow_test: bool) -> str:
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CliError("CONFIG_INVALID", "API base URL must not contain credentials or a query")
    if parsed.path not in ("", "/"):
        raise CliError("CONFIG_INVALID", "API base URL must not include a path")
    official = parsed.scheme == "https" and host == OFFICIAL_HOST
    local = allow_test and host in {"127.0.0.1", "localhost"} and parsed.scheme in {"http", "https"}
    if not official and not local:
        raise CliError("CONFIG_INVALID", "API base URL must be https://openspeech.bytedance.com")
    return cleaned


def validate_resource(resource_id: str) -> str:
    if resource_id not in ALLOWED_RESOURCES:
        raise CliError("INVALID_ARGUMENT", f"Unsupported resource id: {resource_id}")
    return resource_id


def load_settings(*, read_dotenv: bool = True) -> Settings:
    if read_dotenv:
        load_dotenv()
    key = os.environ.get("DOUBAO_TTS_API_KEY", "").strip() or None
    resource = os.environ.get("DOUBAO_TTS_RESOURCE_ID", DEFAULT_RESOURCE).strip() or DEFAULT_RESOURCE
    base = os.environ.get("DOUBAO_TTS_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    allow_test = os.environ.get("DOUBAO_TTS_ALLOW_TEST_BASE", "").strip() == "1"
    return Settings(
        api_key=key,
        resource_id=validate_resource(resource),
        base_url=validate_base_url(base, allow_test=allow_test),
        config_dir=config_dir(),
        allow_test_base=allow_test,
    )


def require_api_key(settings: Settings) -> str:
    if not settings.api_key:
        raise CliError("CONFIG_MISSING", "API key is missing")
    return settings.api_key


def mask_secret(secret: str) -> str:
    if len(secret) <= 4:
        return "****"
    return f"{secret[:2]}****{secret[-2:]}"
