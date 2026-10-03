# Codex Dispatcher

Windows 桌面通知器：多个 Worker 监测 GitHub 待办，通知对应的 Codex agent 会话自行读取、执行和收尾。

![主界面](docs/screenshots/main-window.png)

## 使用

1. 解压 Windows 包，运行整个目录中的 `CodexDispatcher.exe`。
2. GitHub CLI 通过 `gh auth login` 登录；Codex Desktop / CLI 已登录。
3. 添加 Worker：填写名称，**从列表选择 GitHub 仓库**，选择已有 agent 会话。
4. 分配规则默认 **名称提及**，填写 `codex-1070-rc` 或 `@codex-1070-rc`，Issue 和评论中的这两种写法均匹配。
5. 在该 Worker 行点击 **▶ 开始监测**。同一个图标切换为 **■ 停止监测**；每个 Worker 独立控制。
6. **立即检查**可以手工立即查询新待办，即使尚未开启持续监测也会通知匹配的 agent。
7. 每行显示下次检查倒计时；查询时显示“检查中”，停止后显示“—”。手工检查不重置定时计划，底部显示完成结果；遇到正在执行的查询，会等它结束后立即再查一次。悬停倒计时可查看上次检查时间和结果。

每个 Worker 行都有编辑、开始 / 停止监测、立即检查三个图标按钮，悬停显示说明。编辑时从已登录账号可访问的仓库中选择，支持个人、协作和组织仓库，列表可刷新。已有 Worker 保留原分配规则，新 Worker 默认名称提及。会话目录和名称自动读取，配置中 ID 下显示会话名称，选择后立即更新；手动更换 ID 时清除旧名称。连接成功显示绿灯。

仓库列表刷新遇到网络错误时，编辑窗口保留原仓库和配置，显示错误并允许重新刷新。通知详情、通知格式及预览使用白底深色文字，支持 Windows 深色系统配色。

编辑已有 Worker 且未更换 Agent 会话时，保存只做本机校验，不依赖 GitHub 或 Codex 联网。新建或更换会话时仍读取并验证目标会话；GitHub 仓库可访问性在开始监测时检查。GitHub 只读查询遇到 EOF、超时、连接重置或 HTTP 502/503/504 时最多尝试三次，共用 45 秒时间预算。持续断网时监测退避重试、待发送通知保留，恢复后继续。

Worker 配置页采用紧凑行距，将检查间隔与启用开关放在同一行；小屏幕可上下滚动，保存按钮固定在底部。监测中、会话运行中或存在待确认通知时均可保存。新配置用于后续检查，监测倒计时按保存后的间隔重新计算；当前检查、已排队及已发送通知保留原配置和目标会话。保存不会立即扫描或中断 agent；需要立即检查时使用 Worker 的检查按钮。取消“启用此 Worker”并保存会停止该 Worker 的监测及后续派送，已发送的 agent 继续运行。

关闭窗口可驻留托盘。停止监测只停止后续定时通知，已通知的 agent 继续执行。

有 Worker 正在监测时，右下角系统托盘的 D 图标显示旋转光环，悬停可查看监测数量；窗口隐藏后动画继续。全部 Worker 停止监测后恢复静态图标。

## 三种分配规则

| 规则 | 分配值示例 | 匹配来源 |
|---|---|---|
| **名称提及（默认）** | `codex-1070-rc` | 打开的 Issue 标题、正文及评论中的 `codex-1070-rc` 或 `@codex-1070-rc` |
| Label | `agent:repair` | Issue 标签 |
| **GitHub 账号指派** | `imherro`（不加 `@`） | Issue 的 Assignees（负责人）包含该真实 GitHub 账号；同时检查这些 Issue 的评论 |

虚拟 @ 名称不必对应 GitHub 账号。例如评论：

> @codex-1070-rc 汇报你的ip地址

Dispatcher 只做文本匹配，给绑定会话发送该评论的链接，让 agent 自己读取和处理。不会把“汇报 IP 地址”转述成指令。名称前的 @ 可省略，名称后直接接中文也支持。完整名称匹配、不区分大小写；不会把更长名称、邮箱或路径中的文本当作点名。

为避免 agent 自己的署名再次触发通知，评论以 `我是 @名称`、`我是 名称`、`I am 名称` 或 `I'm 名称` 开头且名称与该 Worker 完整匹配时，视为署名汇报并跳过。派送前也应用这条规则，已排队的旧汇报会标记为“已跳过”。这是明确的格式约定，不根据评论作者或语义推测；人类使用相同开头也会被跳过。正常任务请直接写 `@名称 请…` 或 `名称 请…`。该过滤仅作用于评论，不过滤 Issue 标题和正文。

@ 规则每个 Worker 对同一 Issue 的标题 / 正文通知一次，对每条评论各通知一次。**同一 Issue 上的新评论再次点名可以再次触发**，重复轮询、重启以及编辑已通知的原评论都不会自动重发。首次开启会检查已有打开 Issue 中尚未通知的提及。匹配不解析 Markdown，因此引用或代码块中的完整点名也会匹配。

使用 **GitHub 账号指派**：编辑 Worker → 分配规则选择“GitHub 账号指派” → 填写实际账号（不加 `@`）→ 保存并开始监测。在 GitHub 的 Issue 右侧 Assignees 中分配给该账号即可，无需在标题、正文或评论中点名。配置的是接收任务的 GitHub 用户名，不是 Worker 名称或本机当前登录账号；读取仓库的账号只需有访问权限。多个负责人中包含该账号也匹配，大小写不敏感。

GitHub 账号指派规则对每个 Issue 自动通知一次，对其中每条评论也各通知一次。评论无需点名；跳过指派账号本人发表的评论，避免 agent 汇报触发循环。作者账号来自 GitHub 返回的 `user.login`，不根据署名推断；如果人与 agent 共用该账号，人的评论也会被跳过，请使用不同账号。作者缺失的评论正常匹配。重复轮询、重启、编辑原评论或重新指派同一已通知 Issue 都不会重发。首次监测会检查已有打开 Issue 及评论中尚未通知的条目。

Label 规则每个 Worker 对同一仓库 / Issue 自动通知一次，不监测新评论。所有规则在发送前重新检查状态和分配；若排队期间取消指派、删除评论或关闭 Issue，则跳过发送；默认跳过 `agent:running`、`agent:done`、`agent:blocked`。

## 通知与数据

名称提及规则读取文字用于确定是否匹配；Label 只查元数据。GitHub 账号指派规则按负责人、评论编号和真实作者匹配，不分析评论正文。SQLite 和通知只保存地址、编号等元数据，不保存或转述标题、正文、评论内容。评论通知带 `#issuecomment-ID`，agent 可以定位原文。

Dispatcher 没有任务整理模型，也没有额外模型判断调用。空轮询、重复匹配不发消息，不连接 Codex 或读取目标项目。**agent 收到通知后的执行仍正常使用它自己的模型额度**。

收到接收回执即记录“已通知”，不代表 Issue 已完成。桌面桥接的回执可能只有会话 ID，程序不会伪造 Turn ID。agent 遵循原会话项目规范、模型和权限，自行执行、验证、收尾。SDK 模式保持连接直到 agent 回合结束；桌面桥接由原桌面应用持有会话，退出 Dispatcher 不会终止 agent。共享会话的通知串行排队。

## 编辑通知格式

每个 Worker 的编辑窗口可以修改通知格式，实时预览，并恢复默认格式。

新建 Worker 和“恢复默认格式”使用以下模板。已有 Worker 保存的格式及已排队通知不自动覆盖；需要更换时点击“恢复默认格式”并保存。

```text
[Codex Dispatcher · {notification_id}]
有分配给你的待办，请自行读取 #{issue_number} issue  {source}，执行、验证并及时回复issue。
参考网址：{issue_url}
```

| 变量 | 含义 |
|---|---|
| `{issue_url}`（必填） | Issue 或触发评论的原文链接 |
| `{notification_id}`（必填） | 本次通知的唯一 ID，供确认发送结果 |
| `{repository}` | owner/repository |
| `{issue_number}` | Issue 编号 |
| `{source}` | 该 Issue / 该条评论及所属 Issue |

例如：`[{notification_id}] 请读取 {issue_url}，按本项目规范处理，完成后验证并关闭 Issue。`

普通花括号写为 `{{` 和 `}}`。不提供正文、标题和评论内容变量，不执行模板代码。已排队通知保留发现时的格式，修改影响后续新通知。

数据保存在 `%APPDATA%\CodexDispatcher`，包括 SQLite 配置、去重记录和滚动日志。旧版配置与历史可原地升级，保留原规则及去重记录；v0.1 的模型和旧 prompt_template 设置不再生效，新 notification_template 缺省使用默认通知。数据库仍为版本 2。

## 会话占用

默认优先使用已登记的 **Codex 桌面桥接**：通过本机已安装 Codex App Tools 插件使用的管道向原桌面应用发送通知，不另起服务恢复目标会话。空闲但仍由 Desktop 持有的会话可以接收；正在工作的会话将通知保留在 SQLite 队列，空闲后自动发送，忙碌不会因为重试六次而丢弃。忙碌重查会退避，最长约 160 秒。

桥接由 Codex 桌面任务环境提供 `CODEX_APP_TOOLS_PIPE_PATH` 和 `CODEX_THREAD_ID` 后登记，desktop-bridge.json 保存真实调用来源会话 ID 和本机管道路径线索，不保存令牌。此机器已登记，可直接运行新版，无需继承 Codex 环境变量。桥接在桌面应用重启后通过只读接口列表重新发现本机通知管道，区分名称相似的浏览器管道。只调用读取会话状态、发送通知两个工具，目标会话必须由用户配置。

**兼容性限制：桥接是已安装桌面插件的本机接口，不是公开稳定的独立 SDK API。** Codex 更新可能改变管道或响应格式；遇到未知发送结果不自动重发。当前集成针对 Windows 验证，不支持多实例歧义选择。原 Codex 桌面应用需要保持运行。

未登记桥接时使用官方 SDK 模式。该模式不能向另一个进程持有写入权的会话派送，空闲并不代表释放写入权；占用时有限重试。保存配置仍只读验证。不要把 SDK 模式的会话占用误认为 agent 正在执行。

发送结果不确定时不自动重发，在“通知记录”中检查和确认。已通知的会话或后台操作运行期间，程序等待结束再退出。升级前先退出旧版。

## 开发与验证

Python 3.12、PySide6、官方 `openai-codex==0.160.0`。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m codex_dispatcher.main
.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

构建输出为 `dist/v0.4.9/CodexDispatcher` 及同目录下的 Windows x64 ZIP。分发整个应用目录，SDK runtime 已包含。

默认测试使用 mock，不消耗真实模型额度，覆盖三种规则、评论去重、旧版迁移、图标按钮、格式验证与预览、桌面回执、持续忙碌排队及不确定发送恢复。

v0.4.1 通过 104 项测试；独立 GUI 验收覆盖三次打开编辑窗口、仓库 EOF 失败与恢复、两次打开通知记录和详情，以及深色系统配色下的完整文本可读性。见 [GUI 验收报告](docs/gui-acceptance-result.json)。这些检查没有发送真实通知或调用模型。

v0.4.2 通过 111 项测试。桌面模式验证会话时直接读取原桌面应用；SDK 只读操作遇到 `workspace routing discovery timed out` 时最多尝试三次。派送前持续出现该超时，通知保留在队列中等待恢复；发送结果不确定时仍需人工确认，不自动重发。界面操作失败的日志包含操作名称，便于定位。

v0.4.3 通过 121 项测试，新增已有配置离线保存、GitHub 查询临时失败恢复、重试上限、鉴权错误不重试、写操作不重试，以及持续断网后队列与监测恢复检查。

v0.4.4 通过 130 项测试，新增手动检查与定时检查重叠、明确的检查反馈、会话名称显示及无 @ 评论匹配；只读验收确认 RepairComputer #4 的两条回复均能匹配，未发送测试通知。

```powershell
.venv\Scripts\python.exe scripts/live_mentions.py --run-live
```

显式真实验收脚本在已有测试 Issue #1 创建两条临时点名评论，向新的独立只读会话发送链接，验证 agent 自己读取评论、同一 Issue 的新评论可再次通知和重复轮询静默；随后删除其创建的测试评论。结果见 [@ 规则验收报告](docs/mention-acceptance-result.json)。

实现使用官方 [GitHub 评论 API](https://docs.github.com/en/rest/issues/comments) 和 [已登录用户仓库 API](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user)。@ 模式每轮扫描打开的 Issue 和分页评论；大仓库查询可能较慢，打开 Issue 达到 1000 条时明确报错，不静默漏掉候选。

桌面桥接真实验收：`scripts/live_desktop_bridge.py --run-live` 使用新的隔离只读会话和两条临时测试评论。本次空闲 Desktop 会话派送、忙时排队、空闲自动发送、上下文继承已验证；隔离会话读取 GitHub 时被网络代理拒绝，因此完整端到端验收仍标为失败，两条测试评论已删除。报告见 [桌面桥接验收](docs/desktop-bridge-acceptance-result.json)。官方 [App Server 文档](https://learn.chatgpt.com/docs/app-server)区分读取、恢复和会话订阅；本地插件桥接实现依据本机 codex-app-tools 0.1.5 的实际协议。

开发同步约定见 [AGENTS.md](AGENTS.md)：每轮修改前拉取远端，完成验证后自动提交、推送。旧版文档留在 docs 作为历史记录，当前行为以本文为准。
