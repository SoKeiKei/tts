from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from pydantic import BaseModel

from minimax_tts.errors import CliError

ALLOWED_HOSTS = {"api.minimaxi.com", "api.minimax.io"}
DEFAULT_BASE_URL = "https://api.minimaxi.com"
DEFAULT_MODEL = "speech-2.8-hd"
ENDPOINT_PATH = "/v1/t2a_v2"
TEXT_LIMIT = 10000

ALLOWED_MODELS = {
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
}
MODELS_28 = {"speech-2.8-hd", "speech-2.8-turbo"}
EMOTIONS = {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper"}
EMOTIONS_28 = EMOTIONS - {"fluent", "whisper"}
SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100}
BITRATES = {32000, 64000, 128000, 256000}
LANGUAGES = {
    "Chinese",
    "Chinese,Yue",
    "English",
    "Arabic",
    "Russian",
    "Spanish",
    "French",
    "Portuguese",
    "German",
    "Turkish",
    "Dutch",
    "Ukrainian",
    "Vietnamese",
    "Indonesian",
    "Japanese",
    "Italian",
    "Korean",
    "Thai",
    "Polish",
    "Romanian",
    "Greek",
    "Czech",
    "Finnish",
    "Hindi",
    "Bulgarian",
    "Danish",
    "Hebrew",
    "Malay",
    "Persian",
    "Slovak",
    "Swedish",
    "Croatian",
    "Filipino",
    "Hungarian",
    "Norwegian",
    "Slovenian",
    "Catalan",
    "Nynorsk",
    "Tamil",
    "Afrikaans",
    "auto",
}


class Settings(BaseModel):
    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    config_dir: Path
    allow_test_base: bool = False

    model_config = {"arbitrary_types_allowed": True}


def config_dir() -> Path:
    override = os.environ.get("MINIMAX_TTS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "minimax-tts"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "minimax-tts"
    return Path.home() / ".config" / "minimax-tts"


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
    official = parsed.scheme == "https" and host in ALLOWED_HOSTS
    local = allow_test and host in {"127.0.0.1", "localhost"} and parsed.scheme in {"http", "https"}
    if not official and not local:
        raise CliError("CONFIG_INVALID", "API base URL is not an allowed MiniMax host")
    return cleaned


def validate_model(model: str) -> str:
    if model not in ALLOWED_MODELS:
        raise CliError("INVALID_ARGUMENT", f"Unsupported model: {model}")
    return model


def load_settings(*, read_dotenv: bool = True) -> Settings:
    if read_dotenv:
        load_dotenv()
    key = os.environ.get("MINIMAX_TTS_API_KEY", "").strip() or None
    base = os.environ.get("MINIMAX_TTS_API_BASE", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = os.environ.get("MINIMAX_TTS_DEFAULT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    allow_test = os.environ.get("MINIMAX_TTS_ALLOW_TEST_BASE", "").strip() == "1"
    return Settings(
        api_key=key,
        base_url=validate_base_url(base, allow_test=allow_test),
        model=validate_model(model),
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
