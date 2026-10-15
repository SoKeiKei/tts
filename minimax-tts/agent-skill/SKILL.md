---
name: minimax-tts
summary: Use the independent MiniMax T2A CLI for speech synthesis and batch tasks with the caller's selected text, voices, model, and language settings.
---

# Agent Skill: MiniMax TTS CLI

任务需要 MiniMax 语音合成或已指定该服务时读取本指南。先执行 `minimax-tts --version`，按需查看子命令 `--help`，真实生成前运行 `minimax-tts doctor --json`。本地音色管理和 `--dry-run` 不要求 API 密钥。未激活虚拟环境时，可在工具目录使用 `.\.venv\Scripts\minimax-tts.exe`。

配置对应 API 密钥和区域即可按需调用，不需要额外启用开关。配置检查只反映当前运行环境，不代表在线权限、额度或生成已通过验证。不要要求用户把密钥贴到对话里。

## 选择参数

- `synth` 生成单段 MP3，`batch` 按清单生成，二者均支持 `--dry-run`。
- 使用调用方指定的音色、模型、语言增强和原文，不强制简繁体或特定口语写法。
- `--language-boost` 可按任务设置；粤语推荐 `Chinese,Yue`，英语可用 `English`，也可以使用 `auto`。
- `voices pairings --language yue --json` 提供静态推荐，不是账号实时授权清单。
- 长文本由调用方分段，批量任务使用稳定的任务 ID，续跑时使用 `--resume`。

## 示例

```powershell
minimax-tts synth --text-file ".\scripts\line.txt" --voice-id "<VOICE_ID>" --out ".\audio\line.mp3" --dry-run --json
minimax-tts synth --text "今日我哋一齊學英語！" --voice-id "Cantonese_GentleLady" --language-boost "Chinese,Yue" --out ".\audio\yue.mp3" --json
minimax-tts batch --manifest ".\jobs\batch.json" --output-dir ".\audio" --resume --json
```

路径从调用项目解析，不写死开发机目录。真实生成只在用户任务授权范围内执行，不在失败后自动改用其他供应商。

## 使用结果

只在退出码为 0、JSON `ok` 为 true、且 MP3 存在时，将 `output_path` 和实际 `duration_ms` 交给下游。配置、授权或额度错误应停止；`may_have_been_billed` 为 true 时不要自动重新提交。不要默认覆盖已有文件，也不要把本地模拟或参数检查当成在线验收。
