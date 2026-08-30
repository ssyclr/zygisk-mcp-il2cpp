# Zygisk IL2CPP MCP Bridge

这是一个仅依赖 Python 标准库的 stdio MCP Server。它通过 ADB 转发连接目标进程内的本地 Socket，并把 IL2CPP、普通 Native 内存、LuaJIT、Dobby、汇编和断点能力暴露为 MCP tools。

## Start

先在 WebUI 或 `/data/adb/zygisk_il2cpp_mcp/apps.txt` 中加入目标游戏包名，并在修改配置后重启目标游戏。默认命令端口是 `27184`。

```powershell
python mcp/mcp_server.py --port 27184
```

如果 MCP Server 直接运行在目标 Android 机器上，使用直连模式，无需 ADB 转发：

```sh
python mcp/mcp_server.py --port 27184 --direct
```

默认模式会先连接本机 `127.0.0.1:27184`，失败后再尝试 `adb forward`；`--direct` 会禁用自动转发。

MCP 还会启动独立的浏览器控制页面，默认地址是 `http://127.0.0.1:27185/`。所有功能开关第一次启动时全部开启，页面修改会立即影响 `tools/list` 并保存到 `mcp_features.json`。可用参数：

```text
--admin-host 127.0.0.1
--admin-port 27185
--admin-token <token>
--no-admin
--feature-config <json-path>
```

非回环管理地址必须设置令牌。功能读取、单项切换和全部切换只存在于浏览器管理 API，不注册为 MCP tools，因此 Agent 无法看到或调用管理接口。被禁用的工具不会返回给 Agent，`raw_hook_call` 也不能绕过开关；关闭全部功能后仍可通过浏览器页面恢复。

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
- `il2cpp_list_fields`：枚举字段类型、Offset、Flags、静态/常量状态，可选择父类字段。
- `il2cpp_search`：跨一个或全部 Image 模糊搜索 `class`、`method` 或 `field`，支持 Image/命名空间/类过滤、大小写、包含/前缀/精确匹配及分页。
- `il2cpp_find_method`：精确解析方法。
- `il2cpp_invoke` / `il2cpp_call`：通过 `il2cpp_runtime_invoke` 调用静态方法或指定实例地址的方法。
- `il2cpp_object_inspect`：按地址加载对象及字段值，可包含继承字段。
- `il2cpp_list_items`：分页读取一维数组或 `List<T>`。
- `il2cpp_dictionary_get`：按类型化 Key 调用 `Dictionary<TKey,TValue>.get_Item`。
- `il2cpp_hook`：解析方法后，将其 Dobby Hook 到自定义原生 replacement 地址。
- `il2cpp_hook_return`：解析方法后安装固定返回值 Hook。
- `il2cpp_unhook`：解析方法后通过 `DobbyDestroy` 恢复。

`il2cpp_invoke.arguments` 支持布尔值、数值、字符串、`null` 和枚举。引用对象参数可把对象地址作为字符串传入；实例方法必须提供 `instance_address`。

也支持显式参数类型，适合数值类型、对象地址或枚举容易产生歧义的调用：

```json
[
  {"type":"i32","value":42},
  {"type":"string","value":"test"},
  {"type":"object","value":"0x7abc123000"},
  {"type":"enum","value":"RoleSyncState.Walking"}
]
```

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
- `memory_resolve_address`：解析模块 load bias/start 加有符号 Offset。
- `memory_resolve_pointer_chain`：解析最多 32 级的模块基址或绝对基址指针链，并返回每一级地址。
- `memory_read_pointer_chain` / `memory_write_pointer_chain`：解析指针链后读写类型化数值。
- `memory_scan_base`：多线程扫描指向模块基址、模块 Offset 或绝对地址的指针。
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

指针链从 `module load_bias + base_offset` 或 `base_address + base_offset` 开始。每个 `offsets` 元素执行“读取当前指针，再加该有符号 Offset”；结果会返回全部中间步骤。基址扫描的 `workers=0` 会自动选择至少 2 个、最多 32 个线程，也可显式指定。System 后端允许并行 I/O；驱动后端为了兼容未知 ioctl 线程安全性，底层读操作保持串行，但分片调度和匹配仍为多线程。

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
- `breakpoint_backtrace`：通过 `breakpoint_hits` 返回的 `hit_id` 读取命中时采样的用户栈回溯，并解析每帧所属映射/模块。

汇编、反汇编与硬件断点当前是 ARM64 能力。ARM32 或禁止 `perf_event_open` 的内核会返回明确的 unsupported/failed 原因，其他 MCP tools 仍正常使用。

## Ghidra 反编译

- `decompiler_status`：查询 ARM64 Ghidra Native 引擎、运行时内存读取和 IL2CPP 类型元数据状态。
- `decompile_function`：读取指定地址和范围，返回 Ghidra/Sleigh 生成的 C 风格伪代码。

反编译器使用独立的 `libghidra_decompiler.so`，不依赖 Java、RetDec 或外部服务。输入地址精确命中 IL2CPP 方法起始地址时，会自动注入方法名、返回类型、`this`、托管参数、隐藏的 `MethodInfo*` 参数，以及声明类和继承类的实例字段布局。未命中 IL2CPP 方法时仍可作为普通 ARM64 Native 反编译器使用。

引擎通过受限回调读取目标实时内存，并把 `/proc/self/maps` 中的只读区域传给 Ghidra，用于分析全局数据和已初始化的运行时字符串。函数分析严格限制在请求范围内；范围外直接分支会生成截断桩，不再导致整个反编译请求失败。

当前限制：范围外尾调用可能仍显示为 `halt_missing()`；调用目标尚未批量替换成 IL2CPP 方法名；未初始化的 IL2CPP 编码字符串槽不会自动展开为文本。反编译器缺失、ABI 不兼容或初始化失败只会停用这一功能，不影响内存、Hook、Dobby、Lua 或断点工具。

## Help 与兼容性

- `runtime_capabilities`：一次返回内存后端、LuaJIT、汇编、断点、Dobby 与 IL2CPP 的独立状态，不会提前初始化可选能力。
- `debug_help`：传 MCP 工具名时直接返回该工具的说明、Schema 和功能组；不传时列出定制原生命令，传原生命令时返回 usage。

## JNI Toast tools

- `mcp_toast_status`：读取自动 Toast 开关，默认开启。
- `mcp_toast_set_enabled`：开启或关闭 MCP 调用内容 Toast。
- `mcp_toast_show`：主动显示自定义 Toast，不受自动开关影响。

自动 Toast 显示 MCP tool 名和 arguments；内容过长时由原生端截断，不影响实际调用。

## Dobby tools

- `dobby_resolve_symbol`：通过 `DobbySymbolResolver` 解析符号。
- `dobby_hook`：按 target/replacement 原生地址安装 Hook，并返回原函数 trampoline。
- `dobby_hook_return`：按地址安装固定返回值 Hook。
- `dobby_instrument` / `dobby_trace_get`：插桩并读取执行计数、最新线程和寄存器快照。
- `dobby_trace_backtrace`：读取 Dobby 插桩最近一次命中的 ARM64 帧指针回溯，并解析模块区域。
- `dobby_patch_code`：使用 `DobbyCodePatch` 写入机器码，单次最多 4096 字节。
- `dobby_destroy`：卸载通过 Dobby Hook/Instrument 安装的拦截。
- `dobby_list_hooks`：列出由桥接层记录的 Hook 和插桩。
- `dobby_version`：读取内置 Dobby 版本标识。

桥接层还保留现有 Socket 功能：`ping`、剪贴板、Unity 输入框以及 `raw_hook_call`。因此新增原生命令后，无需先改 MCP Server 也能调用。

## 注意

Hook 和代码 Patch 直接修改目标进程。replacement 地址、ABI 返回类型或机器码错误都可能导致游戏崩溃。`dobby_patch_code` 是直接代码写入，不会被 `dobby_destroy` 自动撤销；需要调用方自行保存并恢复原始字节。
