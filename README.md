# TTS CLI

面向 Agent 和命令行工作流的两套独立语音工具，输出本地 MP3、实际时长和可供下游读取的 JSON 结果。

当前版本：**0.1.2**。更新内容见 [版本记录](CHANGELOG.md)。

| 工具 | 能力 | 使用说明 | Agent 指南 |
| --- | --- | --- | --- |
| `doubao-tts` | 语音合成、音频生成、声音复刻、音色设计、批量任务 | [README](doubao-tts/README.md) | [SKILL.md](doubao-tts/agent-skill/SKILL.md) |
| `minimax-tts` | 语音合成、批量任务、音色搭配查询 | [README](minimax-tts/README.md) | [SKILL.md](minimax-tts/agent-skill/SKILL.md) |

按任务要求、所需能力和已配置的音色选择工具。两套工具分别安装、分别配置，不会在失败后自动切换供应商。语言、音色和文本由调用方选择，实际可用范围取决于所选服务与音色。

## 安装与配置

需要 Python 3.11 或更新版本。以下为 Windows PowerShell 示例，在所选工具目录执行：

```powershell
cd doubao-tts
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\doubao-tts.exe --version
```

使用 MiniMax 时，在 `minimax-tts` 目录按相同方式安装，并使用 `.\.venv\Scripts\minimax-tts.exe`。

参考各目录的 `.env.example` 配置对应服务的密钥，也可以直接设置环境变量。工具从当前工作目录读取 `.env`；密钥文件不要提交到仓库。无需额外的供应商启用开关。

生成前运行所选工具的 `doctor --json`。它检查本地配置和保存条件，不代表远端权限、可用额度或实际生成已经通过验证。

## Agent 使用

从 [AGENTS.md](AGENTS.md) 选择对应能力，需要时再读取上表中的 Agent 指南和命令帮助。

- `voices pairings` 是静态推荐列表，不是账号实时音色清单。
- `--dry-run` 检查语音合成或批量任务参数，不发送生成请求。
- `--json` 提供结果；音频路径和时长以实际落盘文件为准。
- 已有文件默认不覆盖，批量任务支持 `--resume`。
- 真实生成可能产生服务费用，配置、权限和额度以调用时的实际结果为准。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
