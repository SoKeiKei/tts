# Agent 使用说明

当前只调用豆包。`minimax-tts` 已安装，但账号没有余额，默认禁止发起计费请求。不要把它当成备用供应商，也不要在豆包失败后自动换过去。

## 先确认命令

在 `doubao-tts` 目录执行，这样能读到该目录的 `.env`：

```powershell
doubao-tts --version
doubao-tts doctor --json
```

`doctor` 失败或密钥缺失时停止。不要向用户索取密钥原文，也不要把密钥写进命令、日志、meta 或仓库。

## 选哪种能力

| 需求 | 命令 | 说明 |
| --- | --- | --- |
| 一段对白或旁白变成一个 MP3 | `synth` | 语音合成。豆包语音合成 2.0 用 `--resource seed-tts-2.0` |
| 环境音、对白、音效写在同一段提示词里一次生成 | `create` | 音频生成，模型固定 `seed-audio-1.0`，最长约 120 秒 |
| 用一段样本做声音复刻 | `clone` | 只注册音色并尽量保存试听。确认试听前不要拿它去 `synth` |
| 用文字或图片设计新音色 | `design` | 需要控制台已购买的 `speaker_id` |
| 查看复刻或设计是否完成 | `voice-status` | `Success` 或 `Active` 之后才能合成 |
| 查看推荐系统音色 | `voices pairings` | 不是账号实时音色库 |

粤语配音按项目规则应使用 MiniMax，并且台词必须是繁体字粤语口语。MiniMax 当前未启用，所以遇到粤语任务时停下来说明原因，不要改用豆包假装完成纯粤语配音，也不要调用 `minimax-tts synth` 或 `minimax-tts batch`。

## 命令

```powershell
doubao-tts voices pairings --language zh --json
doubao-tts synth --text "今天学习红色。" --voice-id "zh_female_vv_uranus_bigtts" --resource seed-tts-2.0 --out ".\audio\line.mp3" --json
doubao-tts create --prompt "室内持续有雨声。旁白说：今天学习红色。然后一声轻轻的翻书声。" --speaker "zh_female_vv_uranus_bigtts" --out ".\audio\scene.mp3" --json
doubao-tts clone --audio ".\samples\voice.wav" --speaker-id "S_xxx" --language cn --demo-text "这是试听。" --out ".\audio\demo.mp3" --json
doubao-tts design --speaker-id "S_xxx" --text "夜色渐浓。" --prompt "女性，语速中等，语调温和" --out ".\audio\design-demo.mp3" --json
doubao-tts voice-status --speaker-id "S_xxx" --json
doubao-tts batch --manifest ".\jobs\batch.json" --output-dir ".\audio" --resume --json
```

复刻或设计成功后，正式合成使用：

```powershell
doubao-tts synth --text "正式台词。" --voice-id "<返回的 speaker_id>" --resource seed-icl-2.0 --out ".\audio\final.mp3" --json
```

后付费复刻的 `--speaker-id` 固定为 `custom_speaker_id`，自定义名字放在 `--custom-speaker-id`。首次用该音色调用 `synth` 会收取音色槽位费。

## 结果怎么用

只在退出码为 0、JSON 里 `ok` 为 true、并且 `output_path` 上的文件真实存在时，把路径和 `duration_ms` 交给下游。`duration_ms` 来自落盘后的 MP3。

`error.code` 为 `CONFIG_MISSING`、`AUTH_OR_RESOURCE` 或 `QUOTA_EXHAUSTED` 时停止并说明原因。`NETWORK_TEMPORARY` 可以有限重试，同时看 `may_have_been_billed`。不要猜新音色，不要覆盖已有文件，除非用户明确要求 `--overwrite`。

MiniMax 只有在用户明确说已经充值，并设置 `MINIMAX_TTS_ENABLED=1` 之后才能调用。
