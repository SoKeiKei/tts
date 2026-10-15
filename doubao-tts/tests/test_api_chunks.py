from __future__ import annotations

import base64
import json

import pytest

from doubao_tts.api import SUCCESS_END, assemble_audio, iter_json_objects
from doubao_tts.errors import CliError
from tests.conftest import minimal_mp3


def _chunks(raw: bytes, size: int = 7) -> list[bytes]:
    return [raw[index : index + size] for index in range(0, len(raw), size)]


def test_audio_and_end_frame_split_across_chunks() -> None:
    audio = minimal_mp3()
    raw = (
        json.dumps({"code": 0, "message": "", "data": base64.b64encode(audio).decode("ascii")}).encode()
        + b"\n"
        + json.dumps({"code": SUCCESS_END, "message": "ok", "data": None, "usage": {"text_words": 2}}).encode()
    )
    frames = list(iter_json_objects(_chunks(raw, 5)))
    merged, words = assemble_audio(frames, secrets=[])
    assert merged == audio
    assert words == 2


def test_end_code_is_not_an_error() -> None:
    audio = minimal_mp3()
    frames = [
        {"code": 0, "data": base64.b64encode(audio).decode("ascii")},
        {"code": 20000000, "message": "ok", "data": None},
    ]
    merged, _words = assemble_audio(frames, secrets=[])
    assert merged.startswith(b"\xff\xfb")


def test_sentence_frame_is_not_audio() -> None:
    audio = minimal_mp3(2)
    frames = [
        {"code": 0, "data": None, "sentence": {"text": "其他人。", "words": [{"word": "其"}]}},
        {"code": 0, "data": base64.b64encode(audio).decode("ascii")},
        {"code": 20000000, "data": None, "usage": {"text_words": 3}},
    ]
    merged, words = assemble_audio(frames, secrets=[])
    assert merged == audio
    assert words == 3


def test_missing_end_frame() -> None:
    audio = minimal_mp3(1)
    frames = [{"code": 0, "data": base64.b64encode(audio).decode("ascii")}]
    with pytest.raises(CliError) as caught:
        assemble_audio(frames, secrets=[])
    assert caught.value.code == "INVALID_AUDIO"
    assert "end frame" in caught.value.message


def test_no_audio_data() -> None:
    frames = [{"code": 20000000, "message": "ok", "data": None}]
    with pytest.raises(CliError) as caught:
        assemble_audio(frames, secrets=[])
    assert caught.value.code == "INVALID_AUDIO"


def test_bad_base64() -> None:
    frames = [{"code": 0, "data": "!!!!"}, {"code": 20000000, "data": None}]
    with pytest.raises(CliError) as caught:
        assemble_audio(frames, secrets=[])
    assert caught.value.code == "INVALID_AUDIO"


def test_other_error_code_and_redaction() -> None:
    secret = "super-secret-key"
    frames = [{"code": 45000000, "message": f"denied {secret}", "data": None}]
    with pytest.raises(CliError) as caught:
        assemble_audio(frames, secrets=[secret])
    assert caught.value.code == "AUTH_OR_RESOURCE"
    assert secret not in caught.value.message


def test_sse_prefix_and_incomplete_final_frame() -> None:
    payload = b'event: 352\ndata: {"code":0,"data":"QQ=="}\n'
    frames = list(iter_json_objects([payload[:11], payload[11:]]))
    assert frames[0]["code"] == 0
    with pytest.raises(CliError):
        list(iter_json_objects([b'{"code":']))
