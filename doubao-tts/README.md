# doubao-tts

独立的豆包语音命令行工具。只调用火山引擎豆包 V3 HTTP 单向流式接口，不依赖其他 TTS 程序。

## 安装

```powershell
cd doubao-tts
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
doubao-tts --help
```

## 配置

复制 `.env.example` 为 `.env`，或在当前进程里设置：

```powershell
$env:DOUBAO_TTS_API_KEY = "你的密钥"
$env:DOUBAO_TTS_RESOURCE_ID = "seed-tts-2.0"
```

复刻音色使用 `seed-icl-2.0`，并且要和该音色匹配。工具不会在预设和命令行资源 ID 不一致时静默改掉资源。

本地音色别名写在 `%APPDATA%\doubao-tts\voices.json`，里面没有 API Key。

粤语配音不要用这个工具。即使某些豆包音色标注了粤语方言，本项目的粤语也必须改用 `minimax-tts`，并且台词写成繁体字粤语口语。

可用搭配：

```powershell
doubao-tts voices pairings
doubao-tts voices pairings --language zh --json
```

## 常用命令

```powershell
doubao-tts doctor --json
doubao-tts voices add narrator --voice-id "<DOUBAO_SPEAKER_ID>" --resource seed-tts-2.0
doubao-tts synth --text "今天我们来学 Apple。" --voice narrator --out ".\audio\ep01.mp3" --json
doubao-tts batch --manifest .\examples\batch.json --output-dir .\audio --json
doubao-tts batch --manifest .\examples\batch.json --output-dir .\audio --resume --json
```

`--json` 时 stdout 只有一个 JSON 对象。失败时 `ok` 为 false，进程退出码非 0。`--dry-run` 不调用接口，也不返回虚构的音频路径或时长。

已有文件默认不覆盖。批量任务在输入哈希、音频哈希和 meta 都对得上时，`--resume` 会跳过，避免重复计费。

为避免重复计费，请求可能已经送达后发生的断线或超时不会自动重试，错误结果会带上 `may_have_been_billed: true`。尚未建立连接的失败和明确的限流可以有限重试。批量任务遇到配置、授权或余额问题会停止后续提交；单项保存失败会保留整批的完成情况。

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```

默认不访问真实接口。需要自己验收时，在本机设置密钥和 `DOUBAO_TTS_LIVE=1`、`DOUBAO_TTS_LIVE_VOICE_ID` 后再跑测试。没有跑过的线上结果不能当成验收通过。
