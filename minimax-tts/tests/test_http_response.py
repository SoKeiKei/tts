from __future__ import annotations

import pytest

from minimax_tts.api import decode_audio
from minimax_tts.errors import CliError
from tests.conftest import minimal_mp3, success_payload


def test_success_hex() -> None:
    audio, trace, usage = decode_audio(success_payload(minimal_mp3()), secrets=[])
    assert audio.startswith(b"\xff")
    assert trace == "trace-1"
    assert usage == 12


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (success_payload(b"", status_code=1004, status_msg="bad key"), "AUTH_OR_RESOURCE"),
        (success_payload(b"", status_code=1008, status_msg="insufficient balance"), "QUOTA_EXHAUSTED"),
        (success_payload(b"", status_code=1002, status_msg="rate limit"), "PROVIDER_RATE_LIMIT"),
        (success_payload(minimal_mp3(), status=1), "INVALID_AUDIO"),
        ({"data": None, "base_resp": {"status_code": 0, "status_msg": "success"}}, "INVALID_AUDIO"),
        (success_payload(minimal_mp3(), audio_hex="abc"), "INVALID_AUDIO"),
        (success_payload(minimal_mp3(), audio_hex="zz"), "INVALID_AUDIO"),
        (success_payload(minimal_mp3(), audio_hex=""), "INVALID_AUDIO"),
        (success_payload(b"not-mp3-bytes-here!!"), "INVALID_AUDIO"),
    ],
)
def test_bad_responses(payload: dict, code: str) -> None:
    with pytest.raises(CliError) as caught:
        decode_audio(payload, secrets=["super-secret-key"])
    assert caught.value.code == code


def test_redacts_key_in_provider_message() -> None:
    secret = "super-secret-key"
    payload = success_payload(b"", status_code=1004, status_msg=f"denied {secret}")
    with pytest.raises(CliError) as caught:
        decode_audio(payload, secrets=[secret])
    assert secret not in caught.value.message
