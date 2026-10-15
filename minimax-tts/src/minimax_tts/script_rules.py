from __future__ import annotations

from minimax_tts.errors import CliError

COLLOQUIAL = set("嘅咗哋喺冇佢啲嘢嚟畀睇噉嗰囉㗎喎咪仲乜")
# 简体里常见、繁体写法不同的字。粤语台词里出现这些字就拒绝。
SIMPLIFIED = set(
    "这们国学说门东来对发会过还为开时长从经现实听语书习"
    "儿点样头体电气车马鸟鱼龙风云爱亲义乐兴华万与无应该让"
    "进边问间关动种业产认识读写请谢欢见视觉观颜红绿蓝黄齐"
)

CANTONESE_EXAMPLE = "今日我哋一齊學英語！"
CANTONESE_TEXT_RULE = "粤语配音必须输入繁体字粤语口语，不要用简体普通话或书面语"


def require_cantonese_colloquial(text: str) -> None:
    simplified = [char for char in text if char in SIMPLIFIED]
    if simplified:
        shown = "".join(dict.fromkeys(simplified))
        raise CliError(
            "INVALID_ARGUMENT",
            f"{CANTONESE_TEXT_RULE}。发现简体字：{shown}。示例：{CANTONESE_EXAMPLE}",
        )
    if not any(char in COLLOQUIAL for char in text):
        raise CliError(
            "INVALID_ARGUMENT",
            f"{CANTONESE_TEXT_RULE}。示例：{CANTONESE_EXAMPLE}",
        )


def require_cantonese_pairing(voice_id: str, language_boost: str) -> None:
    if voice_id.startswith("Cantonese_") and language_boost != "Chinese,Yue":
        raise CliError(
            "INVALID_ARGUMENT",
            "粤语音色必须搭配 language_boost=Chinese,Yue，并且台词必须是繁体字粤语口语",
        )
