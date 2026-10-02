# 官方 Codex Python SDK 调查

日期：2026-10-02（Asia/Shanghai）。这份文档记录包安装与签名检查，不代表已完成连接 / 模型 Spike。

## 官方来源

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)：Python 包为 `openai-codex`，通过本地 app-server JSON-RPC 运行，发行包依赖固定版本的 CLI runtime。
- [App Server](https://learn.chatgpt.com/docs/app-server)：会话、turn、鉴权、流式事件协议。
- [Authentication](https://learn.chatgpt.com/docs/auth)：本地 Codex 登录机制。
- [GitHub CLI issue list](https://cli.github.com/manual/gh_issue_list)：`--assignee`、`--label`、`--state open` 与 JSON 输出。

## 已安装并检查

在项目专用 `.venv` 中安装 PyPI 当前稳定发行版（没有使用 `--pre`）：

| 依赖 | 实际版本 |
| --- | --- |
| Python | 3.12.14 |
| openai-codex | 0.160.0 |
| openai-codex-cli-bin | 0.160.0 |
| PySide6 | 6.11.2 |
| pytest | 9.1.1 |
| PyInstaller | 6.22.3 |
| 本机 Desktop Codex CLI | 0.159.0-alpha.12.1 |
| GitHub CLI（本次安装） | 2.102.0 |

Windows x64 wheels 已成功安装。尚未运行 GUI 或打包。

## 通过 inspect.signature 确认的 Python 方法

- `Codex.thread_list(...)` 存在；参数包括 `cwd`、`cursor`、`limit`、`source_kinds`、`sort_key`、`archived`。
- `Codex.thread_resume(thread_id, ...)` 存在；`cwd`、`model`、`sandbox`、`approval_mode`、`developer_instructions` 等覆盖参数可选。目标派送时应只给 Thread ID；显式启用模型覆盖才添加 model。
- `Codex.thread_start(...)` 存在；支持 `ephemeral`。仅拟用于独立测试 Thread / 可选临时整理 Thread，不用于替代目标长期 Thread。
- `CodexConfig` 提供 `codex_bin` 和 `launch_args_override`。默认使用 SDK 固定 runtime；指定 Desktop executable / 共享 runtime 的兼容性还需验证。
- 顶层 `Codex.thread_read`、`Codex.model_list`、`Codex.account_read` 在初步检查中**没有**出现。不能臆造这些 Python 方法。官方 app-server 协议有对应 RPC；下一步需检查 SDK 的实际嵌套接口 / raw RPC 入口并集中封装 adapter。

## 协议已文档化，尚待本机验证

协议具备 `thread/list`、`thread/read`、`thread/resume`、`thread/start`、`turn/start`、`turn/interrupt`、`model/list`、`account/read` 和可见 item / turn 流式事件。

Thread 元数据支持 ID、cwd、创建 / 更新时间、preview、可选 name、source、modelProvider、运行状态等。不要将 modelProvider 当成实际 model；实际模型字段需从真实返回验证。

官方 `thread/list` 默认的 source 过滤可能不涵盖所有 app-server / exec 会话，选择器必须明确研究 source_kinds 与分页。`cwd` 过滤是精确路径匹配，应规范化 Windows 路径，并验证大小写 / 分隔符行为。

运行状态是当前 app-server 的状态。独立 SDK 进程是否可靠识别 Desktop 另一进程持有的会话忙碌状态，尚未验证；必须做跨进程冲突 Spike。不能宣称读取一个进程的 idle 就能保证全局没有正在运行的 turn。

ExternalMessage / untrusted input 的 Python 类型和支持边界尚未验证；如无正式支持，应使用 user 输入和显式不可信内容标记，禁止把 Issue 放入 developer instructions。

廉价整理模式不能仅靠“read-only + 请勿调用工具”的提示词就宣称无法读目标项目或执行 Git / 测试；需验证可执行的工具 / 读取隔离策略，否则应保守禁用该模式并说明限制。

## GitHub 与同步

本机起初没有 `gh`，已用 winget 安装官方 GitHub CLI。`gh auth status` 返回尚未登录。Git Credential Manager 有可用 GitHub 凭据，Git 同步可独立验证；它不等于 `gh` 已登录。

仓库 remote 已设置为 `https://github.com/imherro/CodexDispatcher.git`；初次 fetch 没有取得已有分支。

没有读取或打印任何 token 值，没有向真实开发 Thread 发送测试消息，没有创建测试 Issue，没有消耗 Codex 推理额度。

## 继续开发后的 Spike 顺序

1. 查明 SDK 实际 read / model / account / streaming 接口。
2. 只读连接与鉴权检查，列出保存会话并按项目过滤。
3. 在隔离测试目录新建专用测试 Thread，首轮写入无副作用的记忆标记；关闭连接后恢复同一 ID，第二轮验证上下文保留。
4. 核对真实元数据及默认 resume 对配置的继承行为。
5. 用专用测试 Thread 验证两个客户端的忙碌冲突行为及安全 interrupt。
6. 记录测试结果后再完成 Core、SQLite、GUI、监视队列、托盘、测试与打包。
