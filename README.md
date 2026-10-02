# Codex Dispatcher

Windows 优先的 GitHub Issue → 指定本地 Codex 长期 Thread 派工工具。

当前阶段：**开发前调查，暂停正式编码，等待讨论现成开源方案。**

目标工作流：配置 Worker、GitHub 仓库和 Label / Assignee 规则，选择本地项目及已有 Codex Thread，开始监视。空轮询只查询 GitHub；发现新任务才恢复目标 Thread 并添加 turn。

## 调查资料

- [开源方案比较](docs/open-source-research.md)
- [官方 Python SDK 与本机环境调查](docs/sdk-research.md)

尚未实现应用、执行模型 Spike、创建测试 Issue 或向任何现有开发 Thread 发消息。当前资料是调查记录，不能作为功能验收报告。
