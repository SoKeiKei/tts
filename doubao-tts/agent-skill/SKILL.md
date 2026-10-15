---
name: doubao-tts
summary: Use the independently installed doubao-tts command-line tool to produce speech audio using the user's authorized Doubao/Volcengine voices.
---

# Agent Skill: Doubao TTS CLI

先执行 `doubao-tts --version` 和 `doubao-tts doctor --json`。命令不存在或 doctor 失败时停止，不要假装已经合成成功，也不要向用户索取密钥原文。

## 适用场景

- 任务明确指定豆包语音、火山引擎声音，或已有授权的 speaker ID。
- 粤语配音不要用这个工具。粤语必须改用 minimax-tts，台词必须是繁体字粤语口语。
- 需要把旁白或对白做成独立 MP3，并把真实 `duration_ms` 交回视频流程。
- 剧本已经写好。这个工具不写脚本、不配乐、不剪视频。

## 生成

```powershell
doubao-tts synth --text "早上好，今天学习红色。" --voice narrator --out "./audio/ep01-sc01-line01.mp3" --json
doubao-tts synth --text-file "./scripts/line01.txt" --voice-id "<DOUBAO_SPEAKER_ID>" --resource seed-tts-2.0 --out "./audio/line01.mp3" --json
doubao-tts batch --manifest "./jobs/batch.json" --output-dir "./audio" --resume --json
```

只在退出码为 0、JSON `ok` 为 true、并且 `output_path` 上的文件真实存在时使用结果。用 `duration_ms` 排镜头，不要用字数估算时长。

`error.code` 为 `AUTH_OR_RESOURCE`、`QUOTA_EXHAUSTED` 或 `CONFIG_MISSING` 时停止并说明原因。不要猜测新声线，也不要改调另一个 TTS。`NETWORK_TEMPORARY` 可以有限重试，同时注意 `may_have_been_billed`。没有可信 meta 的旧文件不能当成已经成功。
