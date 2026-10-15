from __future__ import annotations

import os
from pathlib import Path

import pytest

from doubao_tts.cli import app


@pytest.mark.skipif(os.environ.get("DOUBAO_TTS_LIVE") != "1", reason="live API disabled; set DOUBAO_TTS_LIVE=1 to run")
def test_live_synth(tmp_path: Path) -> None:
    voice_id = os.environ.get("DOUBAO_TTS_LIVE_VOICE_ID")
    if not voice_id or not os.environ.get("DOUBAO_TTS_API_KEY"):
        pytest.skip("DOUBAO_TTS_API_KEY and DOUBAO_TTS_LIVE_VOICE_ID are required")
    from typer.testing import CliRunner

    out = tmp_path / "live.mp3"
    result = CliRunner().invoke(
        app,
        ["synth", "--text", "这是一次显式开启的合成测试。", "--voice-id", voice_id, "--out", str(out), "--json"],
    )
    assert result.exit_code == 0, result.stdout
    assert out.is_file() and out.stat().st_size > 0
