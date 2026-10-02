# Codex Dispatcher 第一版验收报告

2026-10-02，Windows 10 x64。本报告分别标明 mock 自动测试、真实服务联调和可执行文件检查，不把模拟结果当作实时结果。

## 实际结果

- 默认 `pytest -q`：**51 passed**，全部 fake/mock 服务，零模型调用。
- 实际 SDK Spike：新建独立只读测试 Thread，关闭连接后恢复同一个 ID，正确记住上一轮标记，见 [spike-result.json](spike-result.json)。
- 实际 GitHub：在 `imherro/CodexDispatcher` 创建专用 label 和 [测试 Issue #1](https://github.com/imherro/CodexDispatcher/issues/1)、[测试 Issue #2](https://github.com/imherro/CodexDispatcher/issues/2)。两次通过一分钟定时轮询完成，均续接 `01a0fd22-ceb4-7e51-ab8c-f457ade42af7`，回复包含既有上下文标记与不同 Dispatch ID。
- 开始监视后的空轮询：0 模型调用；两次完成后的重复轮询：0 新目标 turn；整理关闭：0 整理调用；测试项目无文件变更。完整证据见 [live-acceptance-result.json](live-acceptance-result.json)。
- GUI 开始监视动作通过真实 Qt slot 驱动，并非人工鼠标逐项点击；界面通过 Qt 渲染截图检查。用户手动使用体验仍应在实际项目试用。
- 真实可选整理：运行隔离 profile，恶意“读取 Windows 文件”要求没有产生工具事件，只返回摘要与警告，见 [normalizer-result.json](normalizer-result.json)。
- 源码 GUI/runtime smoke：初始化 GUI/SQLite、复用登录、读取 8 个模型、固定 runtime 0.160.0，0 模型调用，见 [development-smoke-result.json](development-smoke-result.json)。
- Windows PyInstaller 无控制台 exe 已在原生 Windows Qt 平台启动并正常退出（退出码 0）；GUI/SQLite 初始化、固定 runtime 0.160.0、登录复用和 8 个模型目录读取通过，0 模型 turn，见 [packaged-smoke-result.json](packaged-smoke-result.json)。

## 17 项说明

| # | 项目 | 实现与证据 |
| --- | --- | --- |
| 1 | 目录结构 | `src/codex_dispatcher/{domain,services,storage,runtime,ui}`；`tests` 默认自动测试；`scripts` 构建与显式实时测试；`docs` 调查与证据；`CodexDispatcher.spec` 打包配置。 |
| 2 | SDK 版本 | 官方 `openai-codex==0.160.0`、`openai-codex-cli-bin==0.160.0`。Python 3.12.14、PySide6 6.11.2、PyInstaller 6.22.3。 |
| 3 | Thread API 实际情况 | 高级 Codex 有 thread_list/resume/start、models/account；公开 CodexClient 有 thread_read/list/resume、turn_start/interrupt、next_turn_notification 等。生产代码统一 adapter；没有假造高级 API。 |
| 4 | 找项目的 Thread | GUI 选择本地目录，传 cwd 并请求所有已知来源，分页取完整列表、客户端再次校验路径；显示真实名称、预览、时间、模型、cwd、source、ID，缺失字段留空。 |
| 5 | Resume 现有 Thread | 仅向 `thread_resume(原 ID)` 提供 ID；显式覆盖模型才提供 model；真实两次 Issue 的 ID 一致、上下文保留。 |
| 6 | GitHub 分配 | gh issue list --state open，Label 对应虚拟 Worker，Assignee 对应真实账号；本地复核忽略标签；提交前重新检查。 |
| 7 | 空轮询零调用 | discover 只查询 GitHub/SQLite，测试阻断 adapter 和目标路径读取；真实空轮询目标/整理调用数均 0。保存/验证属于用户主动 metadata 操作。 |
| 8 | 整理启用/关闭 | 高级配置开关默认 false；动态选模型；隔离 ephemeral runtime、严格 JSON；关闭时直接模板派送。 |
| 9 | Issue 送入指定 Thread | 分配过滤→原子预留→重新获取正文/评论→构造带 Dispatch ID 的模板→精确 resume→官方 toolOutput→记录 turn_id/通知/最终回复。 |
| 10 | 避免重复 | Worker/repo/Issue 历史去重、事务预留；成功默认一次；更新重派需 opt-in；失败显式手动；不确定提交不自动重发。 |
| 11 | 同 Thread 并发 | 全 Worker 共享 Thread FIFO 与进程锁，SQLite 原子 claim；明确 busy 有限重试。跨 Desktop/CLI 活跃状态不能可靠共享，要求独占使用。 |
| 12 | 数据 | `%APPDATA%\\CodexDispatcher\\codex-dispatcher.db`，WAL；同目录滚动日志。支持隔离 --data-dir；不保存认证秘密。 |
| 13 | 开发运行 | `.venv\\Scripts\\python.exe -m pip install -e ".[dev]"`；`.venv\\Scripts\\python.exe -m codex_dispatcher.main`。 |
| 14 | 测试 | `.venv\\Scripts\\python.exe -m pytest -q` 默认零模型；实时脚本单独明确执行。覆盖分配、去重、崩溃、未确定提交、串行、停止、Dry Run、脱敏、权限、GUI 保存/输出。 |
| 15 | exe 打包 | `scripts/build.ps1` 测试后调用 PyInstaller spec，windowed onedir，携带固定 codex_cli_bin 及 metadata；保留整个 dist/CodexDispatcher 文件夹。 |
| 16 | 已知限制 | 独立 app-server 不是 Desktop 全局锁；无分布式 exactly-once；Web 聊天 ID 不支持；部分 Desktop 工具/交互审批不可用；查询 1000 条上限；无安装器、自启动、签名、自动升级。 |
| 17 | 兼容 fallback | gh PATH 失败查标准安装目录；缺 metadata 留空；cwd 服务端过滤后本地复核；公开底层 SDK wire form；实验权限 provenance 使用公开 request 保留字段；Windows 子进程隐藏控制台；打包排除 PATH 中不兼容的 ICU，让 Qt 使用 Windows 系统 ICU；严格隔离失败时提示关闭整理，绝不绕过限制。 |

## 操作边界

两个测试 Issue 保留作证据，应用没有自动改标签或关闭 Issue。测试只给新建测试 Thread 发消息，没有给已有开发 Thread 发测试任务。测试认证使用现有 GCM 凭据仅注入 gh 子进程内存；生产版仍需正常 `gh auth login` 或已有 GH_TOKEN 环境变量。

目标 Thread 必须由用户先明确授权接收 Dispatcher 的外部任务；Issue 不能替代授权。应用不把它提升到 user/developer 指令，不自动批准升级权限。

SDK 在跨 runtime 读取时曾返回 `notLoaded` 和虚假的 interrupted；恢复不据此自动重发。强制结束应用可能影响它拥有的 runtime，因此 GUI 停止监视后等待活动任务结束，或请求已知 turn 中断。
