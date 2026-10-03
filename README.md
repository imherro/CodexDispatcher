# Codex Dispatcher

Windows 桌面通知器：多个 Worker 监测 GitHub 待办 Issue，通知对应的 Codex agent 会话自行读取、执行和收尾。

![主界面](docs/screenshots/main-window.png)

## 使用

1. 解压 Windows 包，运行整个目录中的 `CodexDispatcher.exe`。
2. 确认 GitHub CLI 已安装并通过 `gh auth login` 登录；Codex Desktop / CLI 已登录。
3. 添加 Worker：填写名称、仓库、Label 或 Assignee 分配规则，选择一个已有 agent 会话。
4. 点击 **开始监测**。同一个按钮可停止全部监测；启用的 Worker 各自按配置间隔检查。
5. 关闭窗口可驻留托盘。停止监测只停止后续通知，已通知的 agent 继续执行。

会话目录和名称自动读取，不需要配置模型或填写任务模板。连接成功显示绿灯。通知记录在独立窗口中查看。

## 通知方式

程序只查询 Issue 编号、状态、Label、Assignee 等元数据，不获取标题、正文、评论。匹配新待办后，发送固定通知与 Issue 地址。agent 自己读取 Issue，并遵循原会话的项目规范、模型和权限处理任务。

Dispatcher 没有任务整理模型，没有额外的模型判断调用。收到通知后的 **agent 执行仍正常使用模型额度**。

空轮询和重复待办不发消息，不连接 Codex 或读取目标项目。每个 Worker 对同一仓库 / Issue 自动通知一次，重启后仍去重；Issue 更新时间变化不会自动重发。通知前会重新核对分配和打开状态。默认跳过 `agent:running`、`agent:done`、`agent:blocked`。

已收到 SDK 回执即记为 **已通知**；这不表示 Issue 已完成。程序不转述 agent 输出、不评价执行结果、不代替 agent 关闭 Issue。运行时在后台保持到该 agent 回合结束，避免关闭连接中断它。多个 Worker 可以绑定不同会话；共享同一会话时通知串行排队。

## 会话占用

保存配置只读会话元数据，修复了保存时的 active writer 错误。实际唤醒通过官方 SDK 恢复指定会话；如果 Desktop 或其他 Codex 进程仍持有该会话写入权，通知会等待并有限重试。无法强行接管另一个进程，也不能保证能唤醒仍在 Desktop 中打开的会话。

发送结果不确定时停止自动重发，在“通知记录”中检查原会话并确认；可手动重新通知。通知已确认但运行连接中断时，去重仍保留。已通知的会话或后台操作运行期间，应用会等待结束再退出。

## 数据和升级

数据保存在 `%APPDATA%\CodexDispatcher`，包括 SQLite 配置、去重记录和滚动日志。v0.1 的 Worker 和历史记录可以继续读取；旧模型、模板、路径覆盖、自动更新重发设置不再生效。升级时先退出旧版，然后启动新版。请勿同时运行两个版本使用同一数据目录。

## 开发

Python 3.12、PySide6、官方 `openai-codex==0.160.0`。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m codex_dispatcher.main
.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

构建输出为 `dist/v0.2.0/CodexDispatcher` 和同目录下的 Windows x64 ZIP。必须分发整个应用目录，SDK runtime 已包含在包内。

测试默认使用 mock，不调用真实模型。59 项测试覆盖静默轮询、元数据查询、恶意正文不转述、多 Worker、通知回执、去重、旧配置、共享会话排队和不确定发送恢复。

显式真实验收：

```powershell
.venv\Scripts\python.exe scripts/live_acceptance.py --run-live
```

脚本使用已有的两条测试 Issue 和新建的独立只读会话，不修改 GitHub。真实结果见 [通知验收报告](docs/notification-acceptance-result.json)。旧版设计和验收资料留在 docs 中作为历史记录，当前行为以本文为准。
