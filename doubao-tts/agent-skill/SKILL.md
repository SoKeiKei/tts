---
name: doubao-tts
summary: Use the independent Doubao CLI for speech synthesis, audio generation, voice cloning, voice design, and batch tasks when those capabilities fit the user's request.
---

# Agent Skill: Doubao TTS CLI

需要对应能力时读取本指南。先执行 `doubao-tts --version`，按需查看子命令的 `--help`，真实生成前运行 `doubao-tts doctor --json`。本地音色管理和 `--dry-run` 不要求 API 密钥。未激活虚拟环境时，可在工具目录使用 `.\.venv\Scripts\doubao-tts.exe`。

命令不存在或配置检查失败时说明原因，不假装生成成功，不要求用户把密钥贴到对话里。使用已有授权的音色 ID 或本地别名，语言与文本按当前任务决定，不强制切换供应商或改写原文。

## 选择命令

- `synth`：一段文本生成 MP3。语音合成 2.0 使用 `--resource seed-tts-2.0`。
- `batch`：按清单生成多段 MP3，支持 `--dry-run`、`--resume`。
- `create`：用一段提示词描述环境音、对白和音效，使用 `seed-audio-1.0`。
- `clone`：用样本注册复刻音色，按需保存试听。
- `design`：按文字或图片设计音色，需要可用的音色槽位。
- `voice-status`：查询状态和试听。音色可用且试听确认后，再以 `seed-icl-2.0` 正式合成。
- `voices pairings`：静态推荐搭配，不是实时音色库或账号授权清单。

## 示例

```powershell
doubao-tts synth --text-file ".\scripts\line.txt" --voice-id "<VOICE_ID>" --resource seed-tts-2.0 --out ".\audio\line.mp3" --dry-run --json
doubao-tts batch --manifest ".\jobs\batch.json" --output-dir ".\audio" --resume --json
doubao-tts create --prompt "雨声持续，随后有一句轻声旁白。" --out ".\audio\scene.mp3" --json
```

路径从调用项目解析，不写死开发机目录。真实生成只在用户任务授权范围内执行。

## 使用结果

只在退出码为 0、JSON `ok` 为 true、且 `output_path` 文件存在时，将路径与实际 `duration_ms` 交给下游。配置、授权或额度错误应停止；`may_have_been_billed` 为 true 时不要自动重新提交。不要默认覆盖已有文件，也不要把本地模拟或参数检查当成在线验收。
