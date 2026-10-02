# SDK Spike 与实现决策

2026-10-02，Windows 10 x64，Python SDK / runtime 0.160.0。

实测结果见 [spike-result.json](spike-result.json)。

- 本机 Codex 登录复用成功；无需 OPENAI_API_KEY。
- `thread_list(cwd=..., source_kinds=...)` 成功找到本项目已保存会话。
- `Codex.models()` 可动态读取模型；`Codex.account()` 可读取登录状态。
- `CodexClient.thread_read()` 是公开的底层 API，adapter 使用它获取真实元数据。
- 新建专用、read-only 测试 Thread，首轮记住随机标记；关闭原连接后 resume 同一个 ID，第二轮正确返回标记。没有给任何原有开发 Thread 发消息。
- Thread 元数据实际包含 cwd、id、createdAt、updatedAt、name、preview、model、source、status 等。缺失字段显示为空。
- SDK 的 `ExternalMessage` 以 `turn/start.toolOutput` 发送外部内容。生产 adapter 使用等价的公开底层 wire form，内容不会进入 developer instructions。
- SDK 默认 approval handler 会接受 command / file approval。adapter 必须显式替换为拒绝，避免权限升级。

## 人工使用与运行状态限制

测试 Thread 第二轮正在运行时，用另一个 SDK app-server 读取同一个 ID，得到 `notLoaded`；其持久化 turn 视图还可能出现 `interrupted`，而实际拥有任务的客户端随后返回 `completed`。

因此独立 app-server 的状态不是跨进程全局锁。应用可以保证本 Dispatcher 的多个 Worker 串行，并捕获明确 busy / conflict；不能承诺与 Codex Desktop / CLI 同时人工操作时完全互斥。监视期间目标会话应交给 Dispatcher 使用。人工操作前停止监视并等当前任务结束。

恢复检查仅自动认可明确的 completed；其他客户端读出的 interrupted 不能直接当成“任务已经停止”来自动重派。

## 提交与崩溃

SQLite 在发送前记录 dispatching；RPC 接受后记录 turn_id / dispatched；终态再记录 completed / failed。发送边界超时或断线时保留 recovery_required，并阻塞该 Thread 后续任务，禁止自动重发。

SQLite 与 Codex RPC 不具备分布式原子提交，不能承诺数学意义上的 exactly-once。设计选择“不确定时人工恢复”，防止常见的崩溃重复提交。

## 整理模式

默认关闭。专用临时目录、ephemeral Thread、经 runtime 确认的独立 permission profile；拒绝根目录读取、仅允许临时 workspace 读取，不允许写入或工具网络。关闭 shell、unified_exec、MCP、apps、多 agent、hooks、web search 等入口；覆盖只用于整理 runtime。整理失败不会切换模型。

实时整理已通过，见 [normalizer-result.json](normalizer-result.json)。面对试图让整理器读取 Windows 文件的 Issue，只输出摘要和警告，没有工具事件。旧 readOnly.access 已被 runtime 拒绝；通过公开 request 保留实验字段 activePermissionProfile，未确认预期 profile 时不发送整理 turn。
