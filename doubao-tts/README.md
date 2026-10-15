# doubao-tts

独立的豆包 / 火山引擎语音命令行工具，提供语音合成、音频生成、声音复刻、音色设计和批量任务。

## 安装

在本目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\doubao-tts.exe --help
```

下文使用 `doubao-tts` 简写；未激活虚拟环境时，使用 `.\.venv\Scripts\doubao-tts.exe`。

## 配置

参考 `.env.example` 创建本目录的 `.env`，或设置当前进程环境变量：

```powershell
$env:DOUBAO_TTS_API_KEY = "<API_KEY>"
$env:DOUBAO_TTS_RESOURCE_ID = "seed-tts-2.0"
doubao-tts doctor --json
```

工具从当前工作目录读取 `.env`。音色别名默认保存在系统用户配置目录，可用 `DOUBAO_TTS_CONFIG_DIR` 覆盖；具体位置可查看 `doctor --json` 的 `voices_path`。

`doctor` 检查本地密钥是否配置、目录保存条件和音频解析依赖，不查询账号余额，也不验证远端音色授权。

## 选择能力

| 命令 | 用途 |
| --- | --- |
| `synth` | 文本生成 MP3，支持文本文件或标准输入 |
| `batch` | 按清单批量合成，支持检查和恢复 |
| `create` | 用提示词生成环境音、对白和音效，使用 `seed-audio-1.0` |
| `clone` | 使用样本注册复刻音色并尝试保存试听 |
| `design` | 用文字或图片设计音色，需要可用的 `speaker_id` |
| `voice-status` | 查询复刻或设计音色的状态 |
| `voices` | 管理本地别名、查看静态推荐搭配 |

语言和文本由调用方选择，不根据文字中的个别汉字拒绝任务，也不强制改用其他供应商。是否支持所需语言由音色与服务决定；推荐列表未列出某语言，不等于服务不支持。

## 示例

```powershell
doubao-tts voices pairings --language zh --json
doubao-tts synth --text "今天学习红色。" --voice-id "zh_female_vv_uranus_bigtts" --resource seed-tts-2.0 --out ".\audio\line.mp3" --json
doubao-tts create --prompt "持续的雨声。旁白说：今天学习红色。随后一声轻轻的翻书声。" --out ".\audio\scene.mp3" --json
doubao-tts clone --audio ".\samples\voice.wav" --speaker-id "<SPEAKER_ID>" --language cn --demo-text "这是试听文本。" --out ".\audio\clone-demo.mp3" --json
doubao-tts design --speaker-id "<SPEAKER_ID>" --text "夜色渐浓。" --prompt "女性，语速中等，语调温和" --out ".\audio\design-demo.mp3" --json
doubao-tts voice-status --speaker-id "<SPEAKER_ID>" --json
```

复刻或设计成功后，先确认状态和试听，再用返回的音色 ID 与 `seed-icl-2.0` 正式合成。后付费复刻使用 `--speaker-id custom_speaker_id --custom-speaker-id <自定义名称>`；首次合成可能涉及音色槽位费用，应先确认所选服务的计费规则。

批量示例使用本地别名 `narrator`，先注册别名或将清单改为自己的 `voice_id`：

```powershell
doubao-tts voices add narrator --voice-id "<VOICE_ID>" --resource seed-tts-2.0
doubao-tts batch --manifest ".\examples\batch.json" --output-dir ".\audio" --dry-run --json
doubao-tts batch --manifest ".\examples\batch.json" --output-dir ".\audio" --resume --json
```

## 结果与恢复

`--json` 返回结果，`duration_ms` 来自实际保存的 MP3。`synth` 和 `batch` 的 `--dry-run` 不请求生成服务，也不返回虚构的音频路径或时长。

已有文件默认不覆盖。`--resume` 会校验任务输入、音频与配套记录，跳过可复用文件。生成前检查保存条件；可能已计费的断线或超时不自动重试，并返回 `may_have_been_billed: true`。批量任务遇到配置、授权或额度问题会停止后续提交，单项保存失败会保留整批完成情况。

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```

默认测试使用本地模拟，不产生服务费用。在线验证需自行设置 `DOUBAO_TTS_LIVE=1`、`DOUBAO_TTS_LIVE_VOICE_ID` 和对应密钥；在线测试可能计费。
