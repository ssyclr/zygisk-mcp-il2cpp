# Zygisk IL2CPP MCP

把 Android Unity IL2CPP 游戏进程中的运行时查询、方法调用和 Dobby Hook 暴露给 MCP 客户端的 Zygisk 模块。

## 频道

TG:@il2cppmcp QQ:276342773

## 已实现

- 通过持久目录中的 `apps.txt` 配置多个目标包名，同时匹配应用主进程和 `包名:子进程`。
- 通过持久目录中的 `port.txt` 自定义 MCP/命令 Socket 端口，默认 `27184`。
- 由 Zygisk Root companion 读取配置并通过 IPC 传给目标进程，兼容应用进程无法访问 `/data/adb` 的环境。
- 全新 KernelSU/Magisk WebUI：添加/移除多个包名、修改端口、连接检测、复制 MCP 配置、一键导出 `MCP.zip`。
- IL2CPP：镜像、类、方法枚举，方法定位，静态/实例方法调用，方法 Hook 和固定返回值 Hook。
- IL2CPP Dump：直接写入目标应用私有目录的 `files/zygisk_il2cpp_mcp/il2cpp_dump.cs`，MCP 仅返回是否成功。
- 非 IL2CPP 内存工具：安全读写映射、模块起止地址/重复实例定位、地址反查、字节/类型化搜索及多轮过滤。
- 可选内核内存后端：WebUI 可选择 System、KPM KMA、ditPro、APRead、ioctl hook、Netlink、GT1/GT2、Paradise 或 QX；仅内存读写/搜索走所选后端。
- 搜索：精确多类型搜索、未知值模糊搜索、变化/不变/增大/减小过滤、结果分页，以及内存区域类型多选。
- Dobby：符号解析、原生地址 Hook、固定返回、Instrument 计数、代码 Patch、Destroy 与 Hook 列表。
- 动态调试：内置 LuaJIT+FFI、ARM64 AsmJit 汇编、Capstone 反汇编/指令修改、perf 硬件断点与命中寄存器。
- 可选能力全部懒加载；目标 ABI、内核或运行时不支持时只禁用对应工具，Socket、内存、Dobby 和其他能力继续工作。
- JNI Toast：显示当前 MCP tool 与参数，可通过 MCP 开关或主动显示自定义内容。
- 注入目标启动提示：目标进程初始化时会通过 Toast 显示 `TG: @il2cppmcp`；如果 Android 应用上下文尚未就绪，模块会在启动后短暂重试，不影响目标进程运行。
- 无第三方 Python 依赖的 stdio MCP Server，默认自动执行 `adb forward`。

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

## 风险提示

此项目面向你有权调试的应用。错误的实例地址、replacement 地址、返回 ABI 或机器码 Patch 会直接导致目标进程崩溃。代码 Patch 不会由 `DobbyDestroy` 自动恢复。
