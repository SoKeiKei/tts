from __future__ import annotations

from typing import Any

CANTONESE_RULE = {
    "available": False,
    "provider": "minimax",
    "command": "minimax-tts",
    "language_boost": "Chinese,Yue",
    "text_rule": "粤语配音必须使用 minimax-tts，台词必须是繁体字粤语口语",
    "example": "今日我哋一齊學英語！",
}

# 配音常用的 seed-tts-2.0 系统音色。不是控制台全量，也不包含粤语搭配。
_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    ("zh", "普通话旁白", "Vivi 2.0", "zh_female_vv_uranus_bigtts", "本机已合成通过"),
    ("zh", "普通话旁白", "小何 2.0", "zh_female_xiaohe_uranus_bigtts", ""),
    ("zh", "普通话旁白", "云舟 2.0", "zh_male_m191_uranus_bigtts", ""),
    ("zh", "普通话旁白", "小天 2.0", "zh_male_taocheng_uranus_bigtts", ""),
    ("zh", "普通话旁白", "刘飞 2.0", "zh_male_liufei_uranus_bigtts", ""),
    ("zh", "普通话旁白", "清新女声 2.0", "zh_female_qingxinnvsheng_uranus_bigtts", ""),
    ("zh", "普通话旁白", "甜美小源 2.0", "zh_female_tianmeixiaoyuan_uranus_bigtts", ""),
    ("zh", "普通话旁白", "开朗姐姐 2.0", "zh_female_kailangjiejie_uranus_bigtts", ""),
    ("zh", "童声", "奶气萌娃 2.0", "zh_male_naiqimengwa_uranus_bigtts", ""),
    ("zh", "童声", "天才童声 2.0", "zh_male_tiancaitongsheng_uranus_bigtts", ""),
    ("zh", "童声", "儿童绘本 2.0", "zh_female_xiaoxue_uranus_bigtts", ""),
    ("zh", "少儿", "少儿故事 2.0", "zh_female_shaoergushi_uranus_bigtts", ""),
    ("zh", "教育", "Tina老师 2.0", "zh_female_yingyujiaoxue_uranus_bigtts", "中文、英式英语"),
    ("en", "美式英语", "Tim", "en_male_tim_uranus_bigtts", ""),
    ("en", "美式英语", "Dacey", "en_female_dacey_uranus_bigtts", ""),
    ("en", "美式英语", "Stokie", "en_female_stokie_uranus_bigtts", ""),
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
        from doubao_tts.errors import CliError

        raise CliError("INVALID_ARGUMENT", "language must be zh, en, or yue")
    rows = []
    if chosen != "yue":
        for lang, scene, name, voice_id, note in _ROWS:
            if chosen is not None and lang != chosen:
                continue
            rows.append(
                {
                    "language": lang,
                    "scene": scene,
                    "name": name,
                    "voice_id": voice_id,
                    "resource_id": "seed-tts-2.0",
                    "text_rule": "普通话用简体中文，英语用英文。不要用来配粤语。",
                    "note": note,
                }
            )
    return {
        "ok": True,
        "provider": "doubao",
        "catalog": "recommended-system-voices",
        "cantonese_rule": CANTONESE_RULE,
        "pairings": rows,
    }
