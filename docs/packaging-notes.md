# Windows 打包记录

Python 3.12.14 / PyInstaller 6.22.3 / PySide6 6.11.2，Windows 10 19045 x64。

使用 windowed onedir；Qt 为独立 DLL，SDK 的 `codex_cli_bin` 包及其固定版本 runtime/data/metadata 一起收集。源码运行不意味着冻结包可用，因此另跑 exe 的 GUI/数据库/登录/runtime/模型目录 smoke，绝不启动模型 turn。

首次冻结包在导入 QtCore 时失败。诊断 console 构建确认 DLL 缺入口；PE 导入/导出检查发现 Qt6Core 使用 Windows ICU 的无版本函数，而 PyInstaller 按 PATH 收集到了其他 runtime 的 ICU 78，导出带版本后缀。最终 spec 排除 `icuuc.dll` / `icudt78.dll`，让 Qt 使用系统提供的 ICU，与源码安装的 PySide6 行为一致。没有替换或修改任何 Windows 系统 DLL。

应用和构建结果仅在当前 Windows 10 x64 上实际运行。其它 Windows 版本/干净机器需额外验证；GitHub CLI、Git 和目标项目开发环境仍是本机依赖。

开发与打包结果分别见 [源码 smoke](development-smoke-result.json)、[打包 smoke](packaged-smoke-result.json)。
