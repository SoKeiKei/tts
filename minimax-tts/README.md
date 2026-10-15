# minimax-tts

独立的 MiniMax 语音命令行工具。只调用 MiniMax T2A v2 同步 HTTP 接口，不依赖其他 TTS 程序。

## 安装

```powershell
cd minimax-tts
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
minimax-tts --help
```

## 配置

```powershell
$env:MINIMAX_TTS_API_KEY = "你的密钥"
# 国内账号保持默认。国际账号要显式改域名，失败后不会自动换地域。
$env:MINIMAX_TTS_API_BASE = "https://api.minimaxi.com"
$env:MINIMAX_TTS_DEFAULT_MODEL = "speech-2.8-hd"
```

允许的域名只有 `https://api.minimaxi.com` 和 `https://api.minimax.io`。本地音色别名写在 `%APPDATA%\minimax-tts\voices.json`。

粤语配音只用这个工具。`--language-boost` 必须是完整字符串 `Chinese,Yue`，台词必须是繁体字粤语口语，例如 `今日我哋一齊學英語！`。简体普通话和书面语会被拒绝。

查看全部搭配，或只看粤语：

```powershell
minimax-tts voices pairings
minimax-tts voices pairings --language yue --json
```

文本达到或超过 10000 字会直接失败，不会截断，也不会自动切句。

## 常用命令

```powershell
minimax-tts doctor --json
minimax-tts voices add narrator --voice-id "<MINIMAX_VOICE_ID>" --model speech-2.8-hd
minimax-tts synth --text "你好！欢迎来到英语小课堂。" --voice narrator --out ".\audio\ep01.mp3" --json
minimax-tts synth --text "今日我哋一齊學英語！" --voice-id "Cantonese_GentleLady" --language-boost "Chinese,Yue" --out ".\audio\yue.mp3" --json
minimax-tts batch --manifest .\examples\batch.json --output-dir .\audio --resume --json
```

`--json` 时 stdout 只有一个 JSON 对象。`duration_ms` 来自落盘后的 MP3，不是按字数估算。`--dry-run` 不请求 MiniMax。

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```

默认全部使用 mock，不会产生费用。真实接口要本机设置 `MINIMAX_TTS_LIVE=1` 和 `MINIMAX_TTS_LIVE_VOICE_ID` 后单独跑。
