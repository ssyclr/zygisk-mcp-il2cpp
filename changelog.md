# Zygisk IL2CPP MCP v2.9.3 正式版更新说明

作者：洋葱落日 && DUM

继续保留原有 IL2CPP、Unreal 与原生调试功能，升级可选 Python 脚本扩展、游戏内悬浮窗 Python 控制台、Web 管理页及 `zmcp` 全功能 SDK。

## Python 脚本扩展与 `zmcp` 全功能 SDK

- 可选扩展 `zygisk-il2cpp-mcp-pyext-v1.0.0.zip`（内置 CPython 3.14 运行时）按需托管目标进程的 Python 子进程，修复工作线程退出导致的宿主进程被提前回收问题，保持常驻宿主稳定运行。
- 悬浮窗 `Python` 页升级为四栏工作台（`进程` / `控制台` / `脚本` / `日志`）：支持查看宿主 PID、运行状态、已加载脚本与定时器；内置交互式 Python 控制台、快捷指令芯片、返回值与输出分栏回显；所有文本与数值输入统一调起 Android Java 输入法，不再依赖硬键盘。
- Web「Python 扩展」页支持在线进程芯片快速选择、在线 REPL 控制台执行、脚本作用域管理、自启信任确认及日志筛选浏览。
- `zmcp` Python SDK 扩展普及全部子系统能力：除 `zmcp.api` 全部 287 个底层工具函数与 `zmcp.mcp` 动态调用外，提供高级面向对象封装（`MethodRef` / `ClassRef` / `ObjectRef`、`zmcp.il2cpp`、`zmcp.mem`、`zmcp.ui` 全部 18 种控件构建器与持久化 UI 树、`zmcp.hook`）以及 15 个领域门面（`dobby`、`asm`、`decomp`、`analysis`、`bp`、`debugger`、`frida`、`lua`、`unity`、`render`、`unreal`、`logic`、`workspace`、`native_lib`、`diag`）。

## Unreal 查询、Lua 提取与对象渲染

- 保留独立 Unreal 页面、多标签搜索、对象关系链、字段路径读写与冻结、受控方法调用、CPU 资源预览导出与反射 Dump。
- 保留 Lua／xLua／tolua 脚本加载捕获、已加载函数补采、VM 对象提取、多线程后台扫描与反编译导出。
- 保留 Unity 与 Unreal 对象渲染、整类跟踪、相机自动获取或手动选择，以及多对象采样和样式规则管理。

## 悬浮窗与使用体验

- 悬浮窗标题更新为 `IL2CPP MCP v2.9.3 TG:@il2cppmcp QQ:276342773`，语言、主题和布局继续自动保存。
- 原有横向／纵向手势隔离、内存搜索与冻结、基址链、汇编／伪代码、追踪断点、JavaScript Frida、持久日志和 AI 工作台继续保留。

## 升级与兼容

主模块升级流程会自动保留已安装的可选 Python 扩展目录（`ext/` 与 `action.sh`）以及目标配置、忽略子进程规则、安装 ID、连接令牌和数据目录。

安装新版模块并按提示重启设备，随后重新启动目标应用。使用 Python 连接的用户请在 Web 重新导出 `MCP.zip`，同步更新客户端文件。模块版本为 `v2.9.3`，versionCode 为 `293`；Python MCP、原生 HTTP MCP、WebUI 和悬浮窗标题同步更新。

项目主体不公开源码，公开内容为版本说明和部分使用文档。使用方式见 [项目首页](README.md)、[MCP 连接说明](mcp/README.md)
