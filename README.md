# Coder

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB) ![learn-cc 参考代码采用 MIT](https://img.shields.io/badge/learn--cc-MIT-green)

Coder 是一个由 DeepSeek 驱动的本地 Python 编程助手。它通过命令行读取和修改工作区文件、执行 Shell 命令，并可在一次会话中跟踪待办和任务。项目从 learn-cc 的 s15 集成运行时中提取，加入了网页搜索。

适合第一次使用终端编程助手的开发者，以及想通过代码了解工具调用、上下文管理和任务协作的学习者。

## 目录

- [核心功能与技术栈](#features)
- [快速开始](#quickstart)
- [使用与配置](#usage)
- [项目结构](#structure)
- [开发与测试](#development)
- [贡献方式](#contributing)
- [许可证](#license)
- [致谢](#acknowledgements)

<a id="features"></a>

## 核心功能与技术栈

| 能力 | 用途 |
| --- | --- |
| 终端对话与工具调用 | 在指定工作目录读取、修改文件或运行 Shell 命令。运行时校验参数并记录工具执行结果。 |
| 技能与上下文管理 | 按需加载技能，整理长输出和对话上下文，保存跨会话记忆。 |
| 任务与协作 | 管理待办和带依赖的任务，建立独立子会话，通过 Git worktree 隔离协作目录。 |
| 后台命令和定时事件 | 运行后台命令，在后续对话轮次处理任务输出和定时事件。 |
| MCP 与网页搜索 | 从本地配置加载 MCP 工具，通过 DeepSeek Search API 获取公开网页标题、链接和摘要。 |

CLI 与离线测试使用 Python 3.10+ 标准库。模型、搜索服务、Shell 和 Git 是按功能使用的外部依赖。

<a id="quickstart"></a>

## 快速开始

从仓库根目录执行。运行时没有第三方 Python 包需要安装。先确认 Python 版本和 CLI 装配状态。这两步无需 API 密钥。

```powershell
python --version
python agent.py --describe
```

要使用模型，在 Windows PowerShell 中复制配置模板，编辑 `.env` 并填写 `DEEPSEEK_API_KEY`。

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python agent.py --shell powershell
```

在 macOS 或 Linux 上使用 `cp -n .env.example .env` 复制模板，再执行 `python agent.py`。默认 Shell 是 Bash。

首次对话可以输入「读取 example.py，解释运费函数的规则」。默认项目目录是 [`workspace/`](workspace/)，其中包含该示例。输入 `/exit` 退出。

<a id="usage"></a>

## 使用与配置

### 命令行示例

在 PowerShell 中指定完整项目路径，并执行单条任务。

```powershell
python agent.py --shell powershell --workspace "D:\projects\example" --prompt "阅读 README.md，概括项目的启动步骤。"
```

`--workspace` 省略时使用内置示例目录。使用 `--prompt` 执行一条任务，省略时进入交互模式。

| 交互命令 | 用途 |
| --- | --- |
| `/tools` | 查看当前工具。 |
| `/clear` | 清空当前对话。 |
| `/search 关键词` | 直接请求 DeepSeek Search API，并查看标题、摘要和来源链接。 |
| `/exit` | 结束会话。 |

```powershell
python agent.py --shell powershell --prompt "/search Python pathlib site:docs.python.org"
```

普通对话也可由模型调用 `web_search`。搜索由 DeepSeek 按其服务规则计费，连接检查会向服务发送真实请求。

### 配置

| 参数 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 模型请求和搜索使用的密钥。填在本地 `.env` 或系统环境变量中。 |
| `DEEPSEEK_MODEL` | 模型名称，模板值为 `deepseek-flash`。 |
| `DEEPSEEK_PROXY_MODE` | `system` 使用系统代理配置，`direct` 仅对本应用的 DeepSeek 请求直连。 |
| `--shell` | 可选 `bash` 或 `powershell`，对应解释器须安装。 |

已设置的系统环境变量优先于项目 `.env`。团队 worktree 需要 Git 和至少有一个提交的目标仓库。工具以当前用户权限运行，权限策略会识别并提示部分敏感操作。需要文件系统隔离时，在独立的测试目录或受限环境中运行。

### 服务接口与 MCP

Coder 的用户入口是 CLI。它通过 HTTP 调用 DeepSeek 模型和搜索服务，并通过本地 MCP 配置加载工具。

- 运行 `python -m coder_harness.connection_check` 检查已配置的模型连接。
- [`mcp/mcp_servers.json`](mcp/mcp_servers.json) 配置了一个以标准输入输出通信的示例服务。[示例服务](mcp/demo_server.py) 提供 `text_stats` 工具。启动后可用 `/tools` 查看装配结果。
- 审计、任务、定时事件、记忆和压缩归档按需写入 `.runtime/`。密钥及运行状态保存在本地。

<a id="structure"></a>

## 项目结构

| 位置 | 职责 |
| --- | --- |
| [`agent.py`](agent.py) | 命令行入口，装配并启动运行时。 |
| [`coder_harness/`](coder_harness/) | 对话循环、权限与审计、工具、任务、上下文、DeepSeek 通信和 MCP 集成。 |
| [`skills/`](skills/) | 项目内置技能、参考资料和模板。 |
| [`mcp/`](mcp/) | MCP 配置与本地示例服务。 |
| [`workspace/`](workspace/) | 默认工作区和练习文件。 |
| [`tests/`](tests/) | CLI、工具行为与搜索回归测试。 |
| [`.env.example`](.env.example) | 本地环境变量模板。 |

<a id="development"></a>

## 开发与测试

CLI 直接由 Python 解释执行。当前仓库没有单独的构建步骤或 CI 工作流。修改后，可在仓库根目录运行以下离线检查。

```powershell
python agent.py --describe
python -m unittest discover -s tests -v
```

两条命令均无需真实 API 密钥。运行 Git worktree 相关用例时需要已安装 Git。在 Windows 上如果遇到 `$GIT_DIR too big`，可将仓库和测试工作目录放在更短的路径下重试。

<a id="contributing"></a>

## 贡献方式（Contributing）

请通过 Issue 提供操作系统、Python 版本、所用 Shell、最小复现命令与实际结果。提交 Pull Request 时，说明修改的机制、可能影响的 CLI 行为以及离线测试结果。提交前移除真实密钥、私有工作区内容和运行记录。

<a id="license"></a>

## 许可证（License）

从 learn-cc 引入的部分保留 shareAI Lab 的 MIT 许可证与版权声明，见 [LICENSE.learn-cc](LICENSE.learn-cc)。仓库当前没有单独声明新增代码的许可证。

<a id="acknowledgements"></a>

## 致谢（Acknowledgements）

感谢 shareAI Lab 的 [learn-claude-code s15](https://github.com/shareAI-lab/learn-claude-code/tree/main/s15_integrated_harness) 提供集成运行时的教学基础，以及 DeepSeek 提供模型和搜索接口。
