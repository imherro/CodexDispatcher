> 未发布的旧版草稿。开发暂停后改为 v0.2.0；当前功能和验证见 release-0.2.0.md。

# 0.1.1：配置验证与会话写入权分离

2026-10-03（Asia/Shanghai）。

用户在保存配置时收到 `JSON-RPC error -32600: thread ... already has an active writer`。旧实现为了验证可恢复性，在保存与测试配置时调用 thread_resume；这会尝试取得会话写入权，即使不发送模型消息，也可能与 Codex Desktop/CLI 冲突。

新实现保存/验证只调用 thread_read，检查真实 Thread ID 和 cwd，允许配置已被其他窗口加载或正在运行的会话。真正提交 Issue 时才精确恢复原 ID。官方 [App Server 文档](https://learn.chatgpt.com/docs/app-server) 区分读取存储会话与恢复/订阅会话。

右上角 GitHub/Codex 连接状态新增灯号：Connected 绿色，Not connected 红色，未检查/检查中灰色。文字与详细错误 tooltip 继续保留。

开始/停止监视合并成单个按钮，随选中的 Worker 当前状态切换文本与颜色。开始/停止的后台操作进行中暂时禁用，防止重复点击；表单有未保存编辑时仍可停止原 Worker。停止不会强杀已有 turn，原有安全中断选项保留。

resume 或 turn_start 明确返回 active-writer/busy/conflict 时，转为中文 ThreadBusy，保留任务 queued 并使用已有的 6 次有限重试。结果不确定的请求仍按 recovery_required 处理，不能混同成可重试拒绝。没有强制解除其他进程锁、终止任务或替换 Thread。

实际对截图中的 Thread 进行了 metadata-only 读取和验证，成功；未调用 resume 或任何模型 turn。回归覆盖只读验证、已活动 Thread 配置、resume/turn 两阶段占用拒绝、无关 RPC 错误，以及实际派送记录维持 queued。

输出使用独立 `dist/v0.1.1` 目录，避免覆盖仍在运行的旧 exe。已保存 Worker 继续使用相同 APPDATA 数据库；升级前需从托盘菜单退出旧 Dispatcher。未保存的表单应先记录再关闭。

所有权由原 Codex 进程管理；即使任务已经完成，原进程仍可能持有写入权。需要等待原任务完成，再让原应用释放/卸载该会话后交给 Dispatcher。不能承诺切换聊天一定立即释放。
