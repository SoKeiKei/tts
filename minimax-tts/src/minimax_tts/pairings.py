from __future__ import annotations

from typing import Any

from minimax_tts.script_rules import CANTONESE_EXAMPLE, CANTONESE_TEXT_RULE

CANTONESE_RULE = {
    "available": True,
    "provider": "minimax",
    "model": "speech-2.8-hd",
    "language_boost": "Chinese,Yue",
    "text_rule": CANTONESE_TEXT_RULE,
    "example": CANTONESE_EXAMPLE,
}

_MANDARIN: tuple[tuple[str, str], ...] = (
    ("Chinese (Mandarin)_Lyrical_Voice", "抒情女声"),
    ("Chinese (Mandarin)_News_Anchor", "新闻主播"),
    ("Chinese (Mandarin)_Radio_Host", "电台主持"),
    ("Chinese (Mandarin)_Male_Announcer", "男声播报"),
    ("Chinese (Mandarin)_Gentle_Youth", "温柔青年"),
    ("Chinese (Mandarin)_Warm_Girl", "温暖女孩"),
    ("Chinese (Mandarin)_Sweet_Lady", "甜美女声"),
    ("Chinese (Mandarin)_Crisp_Girl", "清脆女孩"),
    ("Chinese (Mandarin)_Cute_Spirit", "可爱精灵"),
    ("Chinese (Mandarin)_Soft_Girl", "软萌女孩"),
    ("Chinese (Mandarin)_Warm_Bestie", "暖心闺蜜"),
    ("Chinese (Mandarin)_Gentleman", "绅士男声"),
    ("Chinese (Mandarin)_Reliable_Executive", "沉稳高管"),
    ("Chinese (Mandarin)_Mature_Woman", "成熟女声"),
    ("Chinese (Mandarin)_Kind-hearted_Antie", "和蔼阿姨"),
    ("Chinese (Mandarin)_HK_Flight_Attendant", "港风空乘"),
    ("Chinese (Mandarin)_Humorous_Elder", "幽默长者"),
    ("Chinese (Mandarin)_Southern_Young_Man", "南方青年"),
    ("Chinese (Mandarin)_Wise_Women", "智慧女声"),
    ("Chinese (Mandarin)_Warm_HeartedGirl", "热心女孩"),
    ("Chinese (Mandarin)_IntellectualGirl", "知性女孩"),
    ("Chinese (Mandarin)_Pure-hearted_Boy", "纯真男孩"),
    ("Chinese (Mandarin)_Straightforward_Boy", "直率男孩"),
    ("Chinese (Mandarin)_Gentle_Senior", "温和学长"),
    ("Chinese (Mandarin)_Sincere_Adult", "真诚成人"),
    ("Chinese (Mandarin)_Kind-hearted_Elder", "善良长辈"),
    ("Chinese (Mandarin)_Unrestrained_Young_Man", "不羁青年"),
    ("Chinese (Mandarin)_Stubborn_Friend", "固执朋友"),
    ("Chinese (Mandarin)_Laid_BackGirl", "松弛女孩"),
    ("Chinese (Mandarin)_ExplorativeGirl", "探索女孩"),
    ("Chinese (Mandarin)_Warm-HeartedAunt", "热心阿姨"),
    ("Chinese (Mandarin)_BashfulGirl", "害羞女孩"),
    ("Arrogant_Miss", "傲娇小姐"),
    ("Robot_Armor", "机器人"),
)

_ENGLISH: tuple[tuple[str, str], ...] = (
    ("English_expressive_narrator", "富有表现力的旁白"),
    ("English_radiant_girl", "明亮女孩"),
    ("English_Graceful_Lady", "优雅女声"),
    ("English_CalmWoman", "平静女声"),
    ("English_LovelyGirl", "可爱女孩"),
    ("English_PlayfulGirl", "活泼女孩"),
    ("English_FriendlyPerson", "友好男声"),
    ("English_PatientMan", "耐心男声"),
    ("English_Trustworth_Man", "可靠男声"),
    ("English_CaptivatingStoryteller", "故事讲述者"),
)

_CANTONESE: tuple[tuple[str, str], ...] = (
    ("Cantonese_GentleLady", "温柔女声"),
    ("Cantonese_CuteGirl", "可爱女孩"),
    ("Cantonese_KindWoman", "善良女声"),
    ("Cantonese_PlayfulMan", "活泼男声"),
    ("Cantonese_ProfessionalHost (F)", "专业女主持"),
    ("Cantonese_ProfessionalHost (M)", "专业男主持"),
)


def list_pairings(language: str | None = None) -> dict[str, Any]:
    chosen = None if language is None else language.strip().lower()
    if chosen in {"cantonese", "zh-yue", "chinese,yue"}:
        chosen = "yue"
    if chosen in {"chinese", "mandarin", "zh-cn"}:
        chosen = "zh"
    if chosen in {"english"}:
        chosen = "en"
    if chosen not in {None, "zh", "en", "yue"}:
        from minimax_tts.errors import CliError

        raise CliError("INVALID_ARGUMENT", "language must be zh, en, or yue")
    rows: list[dict[str, Any]] = []
    if chosen in {None, "zh"}:
        for voice_id, name in _MANDARIN:
            rows.append(_row("zh", "普通话", name, voice_id, "auto", "普通话可以用简体中文"))
    if chosen in {None, "en"}:
        for voice_id, name in _ENGLISH:
            rows.append(_row("en", "英语", name, voice_id, "English", "英语台词用英文"))
    if chosen in {None, "yue"}:
        for voice_id, name in _CANTONESE:
            rows.append(_row("yue", "粤语配音", name, voice_id, "Chinese,Yue", CANTONESE_TEXT_RULE))
    return {
        "ok": True,
        "provider": "minimax",
        "catalog": "system-voice-pairings",
        "model": "speech-2.8-hd",
        "cantonese_rule": CANTONESE_RULE,
        "pairings": rows,
    }


def _row(language: str, scene: str, name: str, voice_id: str, language_boost: str, text_rule: str) -> dict[str, str]:
    return {
        "language": language,
        "scene": scene,
        "name": name,
        "voice_id": voice_id,
        "model": "speech-2.8-hd",
        "language_boost": language_boost,
        "text_rule": text_rule,
    }
