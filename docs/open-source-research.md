# 开源方案调查

调查日期：2026-10-02（Asia/Shanghai）。依据项目官方 README / 规范；尚未安装或运行候选产品。

## 判定标准

用户的完整目标包含：Windows 本地 GUI、GitHub Issue Label / Assignee 轮询、多 Worker、选择任意本地项目、列出该项目本地 Codex Thread、持续恢复指定的已有长期 Thread、空轮询零模型调用、去重、共享 Thread 串行、崩溃后避免不确定任务重复发送。

“支持 Codex”或“支持持续会话”不等于已验证能够接管用户在 Codex Desktop / CLI 中原有的指定 Thread。没有经过实际测试的能力不标记为已实现。

## 候选比较

| 项目 | 官方资料已确认 | 相对完整目标的差异或未验证项 |
| --- | --- | --- |
| [Agent Orchestrator](https://github.com/Untrivial-ai/agent-orchestrator)（原 ComposioHQ 仓库重定向至此） | 开源桌面产品；提供 Windows 安装程序；支持 Codex；任务 Worker、会话、终端及看板；项目级持久规划会话 | 默认每个任务一个 agent / 独立工作区；Git 项目使用分支和 worktree。尚未证实自动按 GitHub assignment 轮询、列出并恢复任意现有 Desktop Thread、空轮询零模型调用、严格一次派送语义 |
| [OpenAI Symphony](https://github.com/openai/symphony) | 官方开源调度规范与 Elixir 参考实现；任务轮询、并发、退避和可观察状态；一个 Worker run 内续用同一 Thread | 面向每 Issue 独立工作区；参考演示使用 Linear；当前规范允许 tracker adapter 扩展，具体 provider 支持须另验。没有证实符合 Windows 原生 GUI / 用户指定长期 Thread 的产品目标 |
| [Claude-Codex-Orchestrator](https://github.com/Bostonvex/Claude-Codex-Orchestrator) | GitHub Issue 驱动；标签路由；通用仓库配置；本地 Codex CLI 路径 | 需要 Claude Code 编排；本地路径创建新 worktree 并启动 codex exec；包含合并 / 发布流程；不能从 README 证明会恢复用户选定的已有长期 Codex Thread |

## 值得讨论的选择

Agent Orchestrator 是最值得先试用的完整桌面候选。如果可以接受每任务独立会话 / worktree，它可能替代相当一部分自研需求，并提供更丰富的监督界面。

如果“所有分配给 Worker 的 Issue 必须进入原先指定的同一个长期 Codex Thread”是硬要求，目前检索和官方资料没有建立现成替代品满足全部要求的证据。此时建议继续开发一个薄调度器，参考上述项目的队列、状态和日志设计，不复制完整编排系统。

这不是断言市面上不存在符合要求的产品；是本次调查范围内尚未验证存在。遵照用户要求，发现值得替代评估的候选后暂停正式编码，先讨论。

## 原始资料

- [Agent Orchestrator README](https://github.com/Untrivial-ai/agent-orchestrator/blob/main/README.md)
- [Agent Orchestrator 当前状态](https://github.com/Untrivial-ai/agent-orchestrator/blob/main/docs/STATUS.md)
- [Symphony README](https://github.com/openai/symphony/blob/main/README.md)
- [Symphony SPEC](https://github.com/openai/symphony/blob/main/SPEC.md)，尤其 10.2 会话启动、14.3 重启恢复、15.2 工作区约束
- [Claude-Codex-Orchestrator README](https://github.com/Bostonvex/Claude-Codex-Orchestrator/blob/main/README.md)，尤其 Local Codex 运行方式

仅参考设计；当前没有复制任何第三方项目代码或提示词。
