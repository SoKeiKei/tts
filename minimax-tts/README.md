# minimax-tts

独立的 MiniMax 语音命令行工具，使用 T2A v2 同步接口提供语音合成、批量任务和本地音色管理。

## 安装

在本目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\minimax-tts.exe --help
```

下文使用 `minimax-tts` 简写；未激活虚拟环境时，使用 `.\.venv\Scripts\minimax-tts.exe`。

## 配置

参考 `.env.example` 创建本目录的 `.env`，或设置当前进程环境变量：

```powershell
$env:MINIMAX_TTS_API_KEY = "<API_KEY>"
$env:MINIMAX_TTS_API_BASE = "https://api.minimaxi.com"
$env:MINIMAX_TTS_DEFAULT_MODEL = "speech-2.8-hd"
minimax-tts doctor --json
```

按所用服务区域选择 `https://api.minimaxi.com` 或 `https://api.minimax.io`，工具不会自动切换区域或供应商。配置密钥即可按需调用，无需额外启用开关；是否有权限或额度以服务端结果为准。

工具从当前工作目录读取 `.env`。音色别名默认保存在系统用户配置目录，可用 `MINIMAX_TTS_CONFIG_DIR` 覆盖；具体位置可查看 `doctor --json` 的 `voices_path`。

`doctor` 只检查本地配置和保存条件，不查询账号余额，也不代表远端合成已验证。

## 按需生成

按任务选择音色、模型、语言增强和文本。粤语可使用 `--language-boost "Chinese,Yue"`，英语可使用 `English`，也可以使用 `auto`。工具不强制简繁体、特定口语写法或供应商偏好，也不擅自改写输入。

`voices pairings` 提供静态推荐，不是账号实时音色库。实际可用的音色与语言取决于所选服务和授权。

```powershell
minimax-tts voices pairings --json
minimax-tts voices pairings --language yue --json
minimax-tts synth --text "你好，欢迎使用语音工具。" --voice-id "<VOICE_ID>" --out ".\audio\line.mp3" --json
minimax-tts synth --text "今日我哋一齊學英語！" --voice-id "Cantonese_GentleLady" --language-boost "Chinese,Yue" --out ".\audio\yue.mp3" --json
```

文本达到或超过 10000 字会失败，不会自动截断或切句。长文本由调用方分段，并为批量任务分配稳定的任务 ID。

批量示例使用 `narrator` 和 `yue_host` 两个本地别名，先配置它们或将清单改为自己的 `voice_id`：

```powershell
minimax-tts voices add narrator --voice-id "<VOICE_ID>" --model speech-2.8-hd
minimax-tts voices add yue_host --voice-id "Cantonese_GentleLady" --language-boost "Chinese,Yue"
minimax-tts batch --manifest ".\examples\batch.json" --output-dir ".\audio" --dry-run --json
minimax-tts batch --manifest ".\examples\batch.json" --output-dir ".\audio" --resume --json
```

## 结果与恢复

`--json` 返回结果，`duration_ms` 来自实际保存的 MP3。`synth` 和 `batch` 的 `--dry-run` 不请求生成服务，也不返回虚构的音频路径或时长。

已有文件默认不覆盖。`--resume` 校验任务输入、音频与配套记录，跳过可复用文件。请求可能已计费时不自动重试，错误中保留 `may_have_been_billed: true`。批量任务遇到配置、授权或额度问题会停止后续提交，单项保存失败会保留整批完成情况。

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```

默认测试使用本地模拟，不产生服务费用。在线验证需自行设置 `MINIMAX_TTS_LIVE=1`、`MINIMAX_TTS_LIVE_VOICE_ID` 和对应密钥；在线测试可能计费。
