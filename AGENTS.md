# Agent 使用说明

按当前任务需要选择工具，不预设供应商优先级。只在用户授权的任务范围内发起真实生成请求。

## 按需读取

| 任务 | 工具与能力 | 详细指南 |
| --- | --- | --- |
| 旁白、对白转为 MP3，或批量生成 | `doubao-tts synth/batch` 或 `minimax-tts synth/batch` | [豆包](doubao-tts/agent-skill/SKILL.md)、[MiniMax](minimax-tts/agent-skill/SKILL.md) |
| 用提示词同时生成环境音、对白和音效 | `doubao-tts create` | [豆包](doubao-tts/agent-skill/SKILL.md) |
| 用样本复刻音色 | `doubao-tts clone` | [豆包](doubao-tts/agent-skill/SKILL.md) |
| 按文字或图片设计音色 | `doubao-tts design` | [豆包](doubao-tts/agent-skill/SKILL.md) |
| 查询训练状态或获取试听 | `doubao-tts voice-status` | [豆包](doubao-tts/agent-skill/SKILL.md) |

两套工具独立安装、配置和调用。尊重用户指定的语言、原文和音色，不擅自改写文本、转换简繁体或切换供应商。音色支持哪些语言，以所选服务的实际能力为准。

## 调用前

1. 在对应工具目录或已配置的工作目录执行 `<工具名> --version`。真实生成前运行 `<工具名> doctor --json`；本地音色管理和 `--dry-run` 不要求 API 密钥。
2. 命令不存在，或真实生成前配置检查失败时，停止并说明具体原因。不要向用户索取密钥原文，也不要把密钥写入命令、日志、结果或仓库。
3. 使用 `--help` 确认当前命令参数。`voices pairings` 只提供静态推荐，不代表当前账号的音色授权。
4. 优先使用项目相对路径。需要绝对路径时从调用方的工作目录或配置解析，不写死开发机路径。

## 结果与费用

- 只在退出码为 0、JSON 中 `ok` 为 true、且 `output_path` 文件存在时，把路径和 `duration_ms` 交给下游。时长来自落盘后的 MP3。
- `CONFIG_MISSING`、`AUTH_OR_RESOURCE`、`QUOTA_EXHAUSTED` 表示本次运行存在配置、权限或额度问题，应停止后续提交。
- `may_have_been_billed` 为 true 时，不自动重新提交。工具只对明确未计费的连接失败或限流进行有限重试。
- 不默认覆盖已有文件。需要继续批量任务时使用 `--resume`，需要替换文件时明确使用 `--overwrite`。
- 复刻或设计音色后先查看状态与试听；状态可用且试听确认后，再按对应资源进行正式合成。

## 修改与验证

文本文件使用 UTF-8 无 BOM，保留正常中文。测试默认使用本地模拟，不发起真实或付费请求。修改后在对应目录运行 `.\.venv\Scripts\python -m pytest`；不能把本地模拟测试当成在线合成或听感验收。
