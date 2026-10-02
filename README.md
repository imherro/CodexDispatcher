# Codex Dispatcher

Windows 优先的 GitHub Issue → 指定本地 Codex 长期 Thread 派工工具。

已实现桌面 GUI、监视队列、托盘、SQLite、官方 SDK adapter 和 Windows 打包。

目标工作流：配置 Worker、GitHub 仓库和 Label / Assignee 规则，选择本地项目及已有 Codex Thread，开始监视。空轮询只查询 GitHub；发现新任务才恢复目标 Thread 并添加 turn。

![界面](docs/screenshots/main-window.png)

## 开始使用

打开 `dist/CodexDispatcher/CodexDispatcher.exe`，或解压 `dist/CodexDispatcher-0.1.0-windows-x64.zip`。分发时保留整个文件夹和 `_internal`，不能只复制 exe。随包包含 Python、Qt 和固定版本 Codex runtime；GitHub CLI、Git 和项目开发工具使用本机安装。

1. 安装 [GitHub CLI](https://cli.github.com/)，运行 `gh auth login`、`gh auth status`。安装后重新打开终端使 PATH 生效；应用也查找 `C:\Program Files\GitHub CLI\gh.exe`。
2. 在 Codex Desktop 或 `codex login` 完成本地登录。应用复用本地登录，不保存 PAT、API Key 或 OAuth token。
3. 在本地项目准备长期 Codex 会话，由你在**那个会话内**明确授权处理 Dispatcher 后续的 `github_issue` 外部任务，并设定项目规范、验证及权限范围。Issue 本身不是用户授权，也不能授权发布、删除或覆盖安全规则。
4. 新建 Worker，填写名称、仓库、分配规则，选择项目目录，点击“获取此项目的 Codex 会话”并选择准确的 Thread，也可粘贴 ID 后验证。
5. 保存、测试配置、开始监视。立即检查一次，之后每 1–60 分钟检查，默认 5 分钟。

监视期间让 Dispatcher 独占目标会话；人工操作前先停止监视并等待任务完成。第一版重启后需要手动开始监视。

## 分配与派送

- Label 适合虚拟 Worker，例如 `agent:codex-1070-1`，Worker 名称无需对应 GitHub 账号。
- Assignee 必须是真实 GitHub 用户名，只匹配实际分配给该用户的 Issue。
- 仅处理 open Issue；默认忽略 `agent:running`、`agent:done`、`agent:blocked`，可编辑。
- 提交前再次读取正文、完整分页评论、分配与关闭状态，防止队列中已取消分配的任务执行。
- 不自动改标签、关闭 Issue 或 merge PR。目标 Codex 会话按其已有项目规范执行任务。

外部正文通过官方 ExternalMessage 等价的 `turn/start.toolOutput` 发送，归属 `codex_dispatcher/github_issue`，不会写入 developer instructions。模板支持界面列出的 `{{变量}}`，采用单次替换，正文无法注入额外模板变量。

目标 Thread 只用已有 ID resume；不创建替代 Thread。默认不传 cwd/模型/权限覆盖，继承原会话。仅显式开启“Override target model”才传 model。权限升级、动态工具、交互式授权请求默认拒绝。

## 额度与可选任务整理

空轮询及所有已处理的轮询只运行 gh 和 SQLite，零 Codex adapter 调用，零目标仓库读取。登录、模型和会话列表、配置验证是 metadata RPC，不发起模型 turn。

廉价整理默认关闭，只有找到符合规则的新任务后才运行。模型列表动态获取，无硬编码默认模型；用户自行选择。整理使用独立临时目录、ephemeral Thread、经 runtime 确认的 `dispatcher-normalize` 权限 profile，拒绝根目录读取，仅允许临时 workspace 读取，不允许写入或工具网络；禁用 shell/MCP/apps/web search 等入口，输出严格 JSON。

整理失败明确报错，不切换模型。全局旧 `sandbox_mode` 或无法安全隔离的 MCP/plugin 名称会使整理拒绝启动，可关闭整理直接派送。整理与覆盖目标模型是独立开关。

## 去重、队列、停止与恢复

默认每个 Worker 的同一 Issue 只进入派送流程一次，成功后更新也不自动重派。失败或结果不确定不会被普通轮询自动重试，使用历史中的“重新派送”。打开“Issue 更新后允许重新派送”才按更新版本重新排队。

多个 Worker 共享同一 Thread 时采用 FIFO 队列、进程内锁和 SQLite 原子 claim。同一数据库的消费者无法并发提交；明确 busy/conflict 最多重试 6 次，等待有上限。

状态顺序：`queued → dispatching（先写 SQLite）→ dispatched（持久化真实 turn_id）→ completed / failed`。提交/读取结果不确定或重启遗留的进行中任务变为 `recovery_required`，阻塞后续任务，禁止自动重发。

“检查恢复状态”仅自动接受明确 completed。其他 app-server 可能读出虚假的 interrupted，需在原会话确认后“标记已处理”，再决定是否重新派送。数据库与 RPC 没有分布式事务，不宣称严格 exactly-once。

停止监视会暂停轮询与尚未提交的队列，已开始的 turn 继续执行；可另外选择安全请求中断已知 turn，不强杀 runtime。关闭窗口进入托盘，退出需等待任务安全结束。

## Dry Run、输出和数据

Dry Run 查询 GitHub、过滤/去重并预览 Prompt、项目与 Thread，默认零模型调用，不提交目标 turn，不写派送历史。真实整理测试需要额外勾选，默认关闭。验证会话不发送消息；“发送测试消息”单独确认且会消耗额度。

实时显示 assistant 可见消息、命令/文件修改摘要与工具状态，排除 reasoning 原始内容。历史可查看 Prompt/回复/错误/Dispatch ID/turn_id，打开 Issue、复制 Thread ID、重新派送及恢复检查。常见凭据格式会脱敏；避免把敏感信息放进 Issue。

数据位于 `%APPDATA%\CodexDispatcher\codex-dispatcher.db`，日志位于同目录 `logs\dispatcher.log`，每文件 2 MB、5 个备份。SQLite 使用 WAL，包含 Worker JSON、派送历史、settings、runtime_state。备份时安全退出后复制数据库及仍存在的 `-wal`/`-shm`；损坏时明确停止，不自动删除。`--data-dir` 可指定隔离数据目录。

## 开发、测试、打包

Windows PowerShell，Python 3.11+（本机测试 3.12.14）：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m codex_dispatcher.main
.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

默认 pytest 使用 mock/fake GitHub/Codex，不需登录、不使用模型、不修改真实项目。打包脚本先测试再生成 onedir 应用与 ZIP，包含 `codex_cli_bin` 和发行 metadata。

明确运行以下实时脚本会消耗额度，最后一个还创建新 label 和两个测试 Issue：

```powershell
.venv\Scripts\python.exe scripts\spike.py --run-model
.venv\Scripts\python.exe scripts\live_normalizer.py
.venv\Scripts\python.exe scripts\live_acceptance.py --run-live --repository owner/test-repo
```

实时验收使用新测试目录、新测试 Thread，GUI 开始监视动作驱动真实一分钟定时轮询。优先 gh 登录；仅测试脚本允许 GCM 凭据作为 gh 子进程内存环境变量，不落盘、不传给 Codex。

无模型的打包 smoke：

```powershell
dist\CodexDispatcher\CodexDispatcher.exe --no-tray --data-dir C:\Temp\dispatcher-smoke --smoke-test-output C:\Temp\dispatcher-smoke.json
```

## 验证与限制

默认自动测试与两个真实 GitHub Issue → 同一个真实 Thread 的流程已通过。版本、证据和完整 17 项说明见 [验收报告](docs/acceptance-report.md)、[实时结果](docs/live-acceptance-result.json)、[SDK Spike](docs/spike-report.md)。

独立 SDK app-server 无法可靠判断 Desktop/CLI 中同一 Thread 是否忙，跨应用互斥需遵守独占规则。只能访问本地 runtime 可列出/恢复的 Codex Thread，不能给任意 ChatGPT Web ID 发任务。Desktop 专有工具/交互审批不保证兼容；动态工具请求默认拒绝。候选 Issue 达到 1000 条时明确失败。第一版没有开机自启动、自动升级、安装器或代码签名。

## 调查资料

- [开源方案比较](docs/open-source-research.md)
- [官方 Python SDK 与本机环境调查](docs/sdk-research.md)

生产 SDK 协议集中在 `services/codex_service.py`；`domain` 纯规则，`storage` SQLite，`runtime` 锁与队列，`ui` PySide6。测试只向专门新建的测试 Thread 发送模型消息。
