# 两套独立的 TTS CLI

当前版本：**0.1.1**，两套工具同步。更新内容见 [版本记录](CHANGELOG.md)。

当前主要使用 `doubao-tts`。`minimax-tts` 已实现，但账号未充值，默认不会发起计费请求。Agent 请先读 `AGENTS.md`。

粤语配音按规则应使用 MiniMax，台词必须是繁体字粤语口语。在 MiniMax 恢复之前，遇到粤语任务先停下来说明，不要改用豆包冒充纯粤语配音。

普通话和英语用豆包。豆包现在有四类能力：`synth` 语音合成，`create` 一次生成环境音、配音和音效，`clone` 声音复刻，`design` 音色设计。

## 查看声音搭配

```powershell
doubao-tts voices pairings
minimax-tts voices pairings --language yue
```

`voices pairings` 列的是系统音色搭配，不是账号实时清单。

## 安装

```powershell
cd doubao-tts
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
cd ..\minimax-tts
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

密钥写在各自目录的 `.env` 里，不要提交。豆包已用 `zh_female_vv_uranus_bigtts` 合成通过。MiniMax 上次测试因额度不足没有生成文件。
