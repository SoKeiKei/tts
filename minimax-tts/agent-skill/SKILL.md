---
name: minimax-tts
summary: Use the independently installed minimax-tts command-line tool to synthesize speech using MiniMax T2A and authorized voices, returning local audio files and actual durations.
---

# Agent Skill: MiniMax TTS CLI

先执行 `minimax-tts --version` 和 `minimax-tts doctor --json`。确认用户指定的 API 域名。不要让用户把密钥贴到对话里。命令不可用时停止，不要假装音频已经生成。

## 适用场景

- 任务明确使用 MiniMax，或已有合法的 voice ID / 本地别名。
- 需要按段输出 MP3，并把真实 `duration_ms` 交给上游编排。
- 需要粤语时，`language_boost` 必须是 `Chinese,Yue`，台词必须是繁体字粤语口语，例如 `今日我哋一齊學英語！`。不要写简体普通话。

先看搭配：`minimax-tts voices pairings --language yue --json`。

长旁白由调用方先分段并分配稳定 task id。不要让 CLI 自动切句。

## 生成

```powershell
minimax-tts synth --text "Hello! Let's learn some colors." --voice narrator --out "./audio/ep01-sc01-line01.mp3" --json
minimax-tts synth --text "今日我哋一齊學英語！" --voice-id "Cantonese_GentleLady" --language-boost "Chinese,Yue" --out "./audio/yue_line01.mp3" --json
minimax-tts batch --manifest "./jobs/batch.json" --output-dir "./audio" --resume --json
```

只在退出码为 0、JSON `ok` 为 true、并且 MP3 真实存在时，把 `output_path` 和 `duration_ms` 传下去。鉴权失败、余额不足、音色无权限时不要盲目重试，也不要自动换成别的供应商。临时网络失败可以有限重试，并留意可能已经计费。
