# Codex Dispatcher v0.4.5

- 监测中的系统托盘图标显示浅绿色旋转光环，保留 D 标志；悬停显示正在监测的 Worker 数量。
- 窗口隐藏到托盘后动画继续，多个 Worker 中只要还有一个在监测就保持动画；全部停止后恢复静态图标并停止动画计时器。
- 动画帧预先缓存，每 150 毫秒切换一次，不进行网络查询或模型调用。
- 校验中发现时间差偶发超出 GitHub 请求上限，补充单次请求 45 秒硬上限。

GUI 验收已验证隐藏窗口、多 Worker 启停和静态恢复；帧预览见 `screenshots/tray-animation-frames.png`。升级前从系统托盘退出旧版，再运行 `dist/v0.4.5/CodexDispatcher/CodexDispatcher.exe`。
