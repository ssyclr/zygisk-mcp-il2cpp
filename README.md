# Zygisk IL2CPP MCP

把 Android Unity IL2CPP 游戏进程中的运行时查询、方法调用和 Dobby Hook 暴露给 MCP 客户端的 Zygisk 模块。

## 已实现

- 通过持久目录中的 `apps.txt` 配置多个目标包名，同时匹配应用主进程和 `包名:子进程`。
- 通过持久目录中的 `port.txt` 自定义 MCP/命令 Socket 端口，默认 `27184`。
- 由 Zygisk Root companion 读取配置并通过 IPC 传给目标进程，兼容应用进程无法访问 `/data/adb` 的环境。
- 全新 KernelSU/Magisk WebUI：添加/移除多个包名、修改端口、连接检测、复制 MCP 配置、一键导出 `MCP.zip`。
- IL2CPP：跨 Image 模糊搜索类/方法/字段，字段偏移与类型、完整方法签名、对象字段、数组/List/Dictionary 加载、带参静态/实例方法调用，以及方法 Hook。
- IL2CPP Dump：直接写入目标应用私有目录的 `files/zygisk_il2cpp_mcp/il2cpp_dump.cs`，MCP 仅返回是否成功。
- 非 IL2CPP 内存工具：安全读写映射、模块起止地址/重复实例定位、地址反查、多级指针链、并行基址扫描、字节/类型化搜索及多轮过滤。
- 可选内核内存后端：WebUI 可选择 System、KPM KMA、ditPro、APRead、ioctl hook、Netlink、GT1/GT2、Paradise 或 QX；仅内存读写/搜索走所选后端。
- 搜索：精确多类型搜索、未知值模糊搜索、变化/不变/增大/减小过滤、结果分页，以及内存区域类型多选。
- Dobby：符号解析、原生地址 Hook、固定返回、Instrument 计数、代码 Patch、Destroy 与 Hook 列表。
- 动态调试：内置 LuaJIT+FFI、ARM64 AsmJit 汇编、Capstone 反汇编/指令修改、Ghidra C 风格伪代码还原、perf 硬件断点与命中栈回溯、Dobby 追踪回溯。Ghidra 直接读取目标的实时内存以解析只读字符串和全局数据；输入地址精确命中 IL2CPP 方法时自动注入返回值、参数、声明类及实例字段 Offset 类型。
- MCP 功能控制：18 组开关全部默认开启，仅通过默认 `127.0.0.1:27185` 浏览器管理页面动态关闭。管理接口不会暴露给 Agent；禁用工具会从 `tools/list` 消失，原始命令也无法绕过开关。
- 除反编译器外的可选能力按需懒加载；ARM64 Ghidra 反编译器在功能默认开启时随注入主体一同初始化。目标 ABI、内核或运行时不支持时只停用对应工具，Socket、内存、Dobby 和其他能力继续工作。
- JNI Toast：显示当前 MCP tool 与参数，可通过 MCP 开关或主动显示自定义内容。
- 注入目标启动提示：目标进程初始化时会通过 Toast 显示 `TG: @il2cppmcp`；如果 Android 应用上下文尚未就绪，模块会在启动后短暂重试，不影响目标进程运行。
- 无第三方 Python 依赖的 stdio MCP Server，默认自动执行 `adb forward`。

WebUI 和 MCP 配置中不包含陀螺仪功能。

## 配置

模块安装后可直接通过 WebUI 保存配置，也可手动编辑：

```text
/data/adb/zygisk_il2cpp_mcp/apps.txt
/data/adb/zygisk_il2cpp_mcp/port.txt
/data/adb/zygisk_il2cpp_mcp/memory_backend.txt
/data/adb/zygisk_il2cpp_mcp/driver_node.txt
```

`apps.txt` 每行一个包名，例如：

```text
com.example.game
com.example.anothergame
```

程序会在目标进程启动时读取配置。修改后请彻底结束并重新启动目标游戏。
`driver_node.txt` 通常留空；GT1/QX 使用随机设备名且自动发现失败时，可填写明确的 `/dev/...` 路径。

## MCP 启动

```powershell
python mcp/mcp_server.py --port 27184
```

启动后浏览器功能控制页面默认位于：

```text
http://127.0.0.1:27185/
```

开关保存到 `mcp/mcp_features.json`。使用 `--admin-port` 修改端口，使用 `--no-admin` 关闭页面；管理端口监听非本机地址时必须同时配置 `--admin-token`。

如果 MCP Server 就运行在目标 Android 设备上，使用直连模式，不需要 ADB 端口转发：

```sh
python mcp/mcp_server.py --port 27184 --direct
```

默认模式会先尝试直连 `127.0.0.1:27184`，连接失败后才自动执行 `adb forward`；`--direct` 会关闭这一回退行为。

客户端配置示例：

```json
{
  "mcpServers": {
    "zygisk-il2cpp": {
      "command": "python",
      "args": ["D:/path/Zygisk-il2cpp-mcp/mcp/mcp_server.py", "--port", "27184"]
    }
  }
}
```

完整工具说明见 [mcp/README.md](mcp/README.md)。

ARM64 伪代码能力集成 [ghidra-native](https://github.com/radareorg/ghidra-native)，使用 Ghidra Decompiler 与 Sleigh 语义恢复 C 风格代码，无需 Java、RetDec 或外部反编译服务。反编译器使用独立 `libghidra_decompiler.so`：功能默认开启并随注入主体加载，通过受限回调读取目标当前的代码、只读数据、字符串与全局变量。对 `libil2cpp.so` 中的精确方法起始地址，模块会反查运行时元数据并锁定 Ghidra 函数原型和类字段布局；未匹配或 IL2CPP API 不可用时自动退回普通 Native 反编译。缺失、ABI 不兼容或初始化失败只会停用反编译，不影响 Hook、内存、Lua、Dobby 与断点功能。

## 风险提示

此项目面向你有权调试的应用。错误的实例地址、replacement 地址、返回 ABI 或机器码 Patch 会直接导致目标进程崩溃。代码 Patch 不会由 `DobbyDestroy` 自动恢复。
