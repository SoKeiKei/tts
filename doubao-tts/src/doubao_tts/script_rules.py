from __future__ import annotations

from doubao_tts.errors import CliError

# 粤语口语里常见、普通话书面语很少单独出现的字。
COLLOQUIAL = set("嘅咗哋喺冇佢啲嘢嚟畀睇噉嗰囉㗎喎咪仲乜")

CANTONESE_RULE = "粤语配音必须使用 minimax-tts，台词必须是繁体字粤语口语"


def reject_cantonese_text(text: str) -> None:
    if any(char in COLLOQUIAL for char in text):
        raise CliError("INVALID_ARGUMENT", CANTONESE_RULE)
