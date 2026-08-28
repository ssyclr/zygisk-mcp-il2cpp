# Zygisk IL2CPP MCP Bridge

这是一个仅依赖 Python 标准库的 stdio MCP Server。它通过 ADB 转发连接目标进程内的本地 Socket，并把 IL2CPP、普通 Native 内存、LuaJIT、Dobby、汇编和断点能力暴露为 MCP tools。

## Start

先在 WebUI 或 `/data/adb/zygisk_il2cpp_mcp/apps.txt` 中加入目标游戏包名，并在修改配置后重启目标游戏。默认命令端口是 `27184`。

```powershell
python mcp/mcp_server.py --port 27184
```

也可以从项目根目录使用整理好的启动脚本：

```powershell
powershell -ExecutionPolicy Bypass -File mcp/start_mcp.ps1 `
  -Python "D:\Program Files (x86)\python3.14.6\python.exe" `
  -Adb "D:\ASWJ\platform-tools\adb.exe" `
  -Port 27184
```

可直接复制并修改的客户端配置位于 `mcp/client-config.example.json`。MCP 只依赖 Python 标准库，不需要安装第三方包。

本机第一次连接失败时会自动执行：

```text
adb forward tcp:<port> tcp:<port>
```

连接多个 Android 设备时使用 `--serial <device>`。若目标地址可直接访问，可使用 `--no-adb-forward`。

## MCP client config

```json
{
  "mcpServers": {
    "zygisk-il2cpp": {
      "command": "python",
      "args": ["D:/AndroidStudioProjects/Zygisk-il2cpp-mcp/mcp/mcp_server.py", "--port", "27184"]
    }
  }
}
```

## IL2CPP tools

- `il2cpp_status`：初始化并附加 IL2CPP 线程，返回基址和 domain。
- `il2cpp_dump_file`：把完整 C# 元数据 Dump 直接写入目标应用私有目录，不通过 MCP 返回 Dump 内容；MCP 只收到 `success`。
- `il2cpp_list_images`：枚举已加载的程序集镜像。
- `il2cpp_list_classes`：按命名空间/类名过滤镜像内类型。
- `il2cpp_list_methods`：枚举方法、参数类型、返回类型、绝对地址和 RVA。
- `il2cpp_find_method`：精确解析方法。
- `il2cpp_invoke`：通过 `il2cpp_runtime_invoke` 调用静态方法或指定实例地址的方法。
- `il2cpp_hook`：解析方法后，将其 Dobby Hook 到自定义原生 replacement 地址。
- `il2cpp_hook_return`：解析方法后安装固定返回值 Hook。
- `il2cpp_unhook`：解析方法后通过 `DobbyDestroy` 恢复。

`il2cpp_invoke.arguments` 支持布尔值、数值、字符串、`null` 和枚举。引用对象参数可把对象地址作为字符串传入；实例方法必须提供 `instance_address`。

枚举参数会根据目标方法元数据自动读取真实底层整数类型，可使用以下任一写法：

```json
0
"Walking"
"RoleSyncState.Walking"
{"enum": "RoleSyncState.Walking"}
```

Flags 枚举成员可用 `|` 组合，例如 `{"enum":"Read|Write"}`。枚举返回值按其底层整数类型返回。

Dump 文件固定保存到目标应用的：

```text
files/zygisk_il2cpp_mcp/il2cpp_dump.cs
```

对应 Android 绝对路径通常是 `/data/user/0/<目标包名>/files/zygisk_il2cpp_mcp/il2cpp_dump.cs`。每次调用会原子替换上一份文件。

## Memory tools

- `memory_read`：从目标进程完整可读的映射区间读取原始字节，返回小写十六进制数据。
- `memory_write`：向目标进程完整可读写的映射区间写入十六进制字节，返回覆盖前的数据并校验写入结果。
- `memory_read_value`：按小端序读取 `bool`、整数、浮点数或指针值。
- `memory_write_value`：编码并写入类型化数值，同时返回覆盖前的类型化数值。
- `memory_list_modules`：列出当前进程的可执行模块、起止地址、load bias、映射段数量和同名实例序号。
- `memory_find_module`：通过精确模块名/完整路径和从 1 开始的 `occurrence` 定位指定模块实例，并返回全部映射段。
- `memory_address_info`：定位地址所在的映射区域、权限、文件偏移、所属模块和相对偏移。
- `memory_search`：在模块实例或指定地址范围内搜索字节特征并创建过滤会话。
- `memory_search_value`：编码并搜索类型化数值。
- `memory_search_exact`：把同一个数值按多个勾选的类型分别执行精确搜索。
- `memory_search_fuzzy`：创建未知初值快照，再按变化、不变、增大或减小持续过滤。
- `memory_search_results`：分页读取地址和当前快照，避免一次返回过多数据。
- `memory_filter`：按新字节、变化状态或无符号大小关系过滤现有结果。
- `memory_filter_value`：按类型化数值执行相等/不等过滤。
- `memory_search_clear`：释放搜索会话和快照。

单次读写范围为 1 到 65536 字节。普通写入不会修改只读或仅可执行页面；修改原生代码请使用 `dobby_patch_code`。调用示例：

```json
{"address":"0x7abc123000","size":16}
{"address":"0x7abc123000","hex_bytes":"01000000"}
{"address":"0x7abc123000","value_type":"f32","value":1.5}
```

类型化调用支持 `bool`、`i8`、`u8`、`i16`、`u16`、`i32`、`u32`、`i64`、`u64`、`f32`、`f64`、`ptr32` 和 `ptr64`。指针类型需要按目标进程 ABI 选择；整数和指针写入值可使用 `0x...` 字符串。

这些工具不依赖 IL2CPP 初始化，可用于普通 Native、Mono 或其他引擎进程。默认内存访问使用 `process_vm_readv/process_vm_writev`，并在操作前校验完整映射区间权限。WebUI 选择驱动后，只有内存读写和搜索切换到驱动；模块枚举、地址归属、IL2CPP、Dobby、Lua 调度、汇编和断点仍走原系统路径。

### 可选内核驱动

WebUI 支持 `system`、`kpm_kma`、`dit_pro_kpm`、`kpm_ap_read_ioctl`、`kpm_memory_ioctl_hook`、`kpm_tear_ioctl_hook`、`dit_netlink`、`gt1_rtdev`、`gt2_rthook`、`paradise` 和 `qx`。驱动在第一次内存操作时才探测；探测或 I/O 失败后该后端会进入 disabled 状态，可用 `memory_backend_status` 查看原因，不会退出目标进程。

除 System 外的当前驱动适配器只在 ARM64 启用。随机设备节点可在 WebUI 的 Driver node 中明确填写 `/dev/...`。

### 模块和重复名称

模块实例通过映射路径与 load bias 区分。同名模块按起始地址排序，`occurrence` 从 1 开始。例如：

```json
{"module_name":"libgame.so","occurrence":2}
```

返回值包含模块整体 `start`/`end`，以及每个 region 的 `start`、`end`、`permissions`、文件 `offset` 和 `path`。

### 搜索和过滤

按模块搜索机器码特征：

```json
{
  "module_name":"libgame.so",
  "occurrence":1,
  "pattern":"48 8B ?? A?",
  "max_results":1024
}
```

也可以使用 `start_address` 和 `end_address` 指定范围，或用 `memory_search_value` 搜索小端序数值。搜索返回 `session_id` 和地址列表；之后可调用：

```json
{"session_id":1,"mode":"changed"}
{"session_id":1,"mode":"equals","pattern":"01000000"}
```

过滤模式：

- `equals` / `not_equals`：按相同长度的新特征过滤，支持 `?` 通配半字节。
- `changed` / `unchanged`：与上次搜索或过滤时保存的快照比较。
- `increased` / `decreased`：把最多 8 字节的数据按小端无符号整数比较。

每次过滤后会更新保留结果的快照。最多同时保存 16 个搜索会话，超过后自动替换最旧会话；单次最多扫描 512 MiB、返回 10000 个地址，特征长度最多 256 字节。`memory_types` 可多选 `anonymous`、`heap`、`stack`、`app_code`、`system_code`、`app_data`、`ashmem`、`java` 和 `other`。

## LuaJIT tools

- `lua_status`：只查询状态，不创建 VM。
- `lua_execute`：首次调用时创建持久 LuaJIT VM，支持多行脚本和 FFI。
- `lua_logs`：读取脚本、`hookCPU` 回调和 `call` 闭包的持久日志。
- `lua_reset`：移除 Lua 所拥有的 Hook 和主线程泵，并退回未初始化的懒加载状态。

全局 Lua API 包含 `getBase`、`hookCPU`、`hookfunc`、`removehook`、`remove_all_hook`、`call`、`msleep`，以及 `readByte/readDword/readQword/readFloat/readDouble/readPtr/readBytes/readString` 和对应的写入函数。`call(function)` 优先通过 `eglSwapBuffers`，其次 `ALooper_pollOnce` 在主线程执行；若目标不具备这两个符号，300 ms 后由兜底线程执行。内置 `regs_t` FFI 定义可把 `hookCPU` 的 lightuserdata 转为寄存器上下文。

## 汇编与硬件断点 tools

- `assembly_status` / `assembly_assemble`：查询状态并把单条 AArch64 文本指令汇编为机器码。
- `assembly_disassemble`：使用原系统读取路径和 Capstone 反汇编 ARM64 内存。
- `assembly_patch`：汇编后通过 DobbyCodePatch 修改可执行地址。
- `breakpoint_status` / `breakpoint_set` / `breakpoint_list` / `breakpoint_hits` / `breakpoint_clear` / `breakpoint_clear_all`：管理不暂停进程的 ARM64 perf 硬件执行断点和数据监视点。

汇编、反汇编与硬件断点当前是 ARM64 能力。ARM32 或禁止 `perf_event_open` 的内核会返回明确的 unsupported/failed 原因，其他 MCP tools 仍正常使用。

## Help 与兼容性

- `runtime_capabilities`：一次返回内存后端、LuaJIT、汇编、断点、Dobby 与 IL2CPP 的独立状态，不会提前初始化可选能力。
- `debug_help`：不传 `command` 时列出定制原生命令；传入例如 `ASM_PATCH` 时返回 usage 和说明。

## JNI Toast tools

- `mcp_toast_status`：读取自动 Toast 开关，默认开启。
- `mcp_toast_set_enabled`：开启或关闭 MCP 调用内容 Toast。
- `mcp_toast_show`：主动显示自定义 Toast，不受自动开关影响。

自动 Toast 显示 MCP tool 名和 arguments；内容过长时由原生端截断，不影响实际调用。

## Dobby tools

- `dobby_resolve_symbol`：通过 `DobbySymbolResolver` 解析符号。
- `dobby_hook`：按 target/replacement 原生地址安装 Hook，并返回原函数 trampoline。
- `dobby_hook_return`：按地址安装固定返回值 Hook。
- `dobby_instrument` / `dobby_trace_get`：插桩并读取执行计数。
- `dobby_patch_code`：使用 `DobbyCodePatch` 写入机器码，单次最多 4096 字节。
- `dobby_destroy`：卸载通过 Dobby Hook/Instrument 安装的拦截。
- `dobby_list_hooks`：列出由桥接层记录的 Hook 和插桩。
- `dobby_version`：读取内置 Dobby 版本标识。

桥接层还保留现有 Socket 功能：`ping`、剪贴板、Unity 输入框以及 `raw_hook_call`。因此新增原生命令后，无需先改 MCP Server 也能调用。

## 注意

Hook 和代码 Patch 直接修改目标进程。replacement 地址、ABI 返回类型或机器码错误都可能导致游戏崩溃。`dobby_patch_code` 是直接代码写入，不会被 `dobby_destroy` 自动撤销；需要调用方自行保存并恢复原始字节。
