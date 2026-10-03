# Codex Dispatcher v0.4.9

默认通知格式更新为：

```text
[Codex Dispatcher · {notification_id}]
有分配给你的待办，请自行读取 #{issue_number} issue  {source}，执行、验证并及时回复issue。
参考网址：{issue_url}
```

新建 Worker 和“恢复默认格式”使用新版模板。已有 Worker 保存的格式、已排队通知不自动覆盖；编辑已有 Worker，点击“恢复默认格式”并保存即可使用新版。

162 项测试通过；GUI 预览和 Windows 打包启动、数据库、内置 runtime、登录状态及桌面桥接配置读取验证通过。验收未发送实际通知，未调用模型。

升级：从托盘退出旧版，再运行 `dist/v0.4.9/CodexDispatcher/CodexDispatcher.exe`。
