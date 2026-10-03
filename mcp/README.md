# Zygisk IL2CPP MCP Bridge

当前服务版本：**2.7.0** · 作者：**洋葱落日 && DUM**

## 原生 HTTP 接入（不需要 Python）

当前源码增加了手机模块内的 Streamable HTTP MCP 入口，与原有 Socket 共用配置端口。重新构建并安装本次模块后，进入模块 Web 菜单「MCP 连接」，复制 HTTP 配置到支持该传输的客户端：

```json
{
  "mcpServers": {
    "zygisk-il2cpp": {
      "type": "http",
      "url": "http://127.0.0.1:27184/mcp",
      "headers": { "Authorization": "Bearer <Web菜单中的连接令牌>" }
    }
  }
}
```

客户端配置格式各有不同，也可分别填写 URL 和 Authorization 请求头。不要同时填写 Python `command`。默认只监听设备 `127.0.0.1`，设备本机使用无需 ADB；电脑端可使用 `adb forward tcp:27184 tcp:27184`。不提供旧版 HTTP+SSE `/sse` 接口。

需要局域网直连时，在 Web「MCP 连接」→「连接设置」中把监听地址设为 `0.0.0.0`，保存并重启设备。「MCP 连接」自动检测手机当前 Wi-Fi／热点 IPv4，并读取 `port.txt` 中的控制端口，不再要求手填。例如检测到 `192.168.1.100`、配置端口为 `27184` 时，URL 是 `http://192.168.1.100:27184/mcp`，不能用 `0.0.0.0`。没有检测到有效 IPv4 时会显示原因，不伪造连接地址；连接网络后重新检测。令牌不会保存到 WebView 的 localStorage。

监听设置持久化到 `/data/adb/zygisk_il2cpp_mcp/mcp_listen_address.txt`，仅支持 `127.0.0.1` 或 `0.0.0.0`，缺失或非法值回到本机监听。安装器在缺失时创建默认值，升级保留已有选择。监听地址或端口变更必须重启设备：Root 网关不会随单个目标进程退出而重新绑定。

远端只允许经过 Bearer 认证的 HTTP，旧 raw Socket 仍仅接受设备本机连接；下方 Python stdio 配置保持本机或 ADB 转发方式。HTTP 无 TLS，令牌与请求内容以明文传输，只在可信局域网使用，不要暴露到公网或不可信网络。

HTTP 同时提供共享的具名参数工具和原生命令工具，尚不是整套 Python 工具的一比一替换：

- `process_list`、`process_select`、`process_current`：查看和选择进程；每个 HTTP MCP 会话独立，目标退出后不会自动切换。
- `ping`、`runtime_capabilities`、`debug_help`、`raw_hook_call`：健康检查、兼容能力、帮助和单条原生命令调用。
- `memory_read`、`memory_write`、`analyze_function`、字符串／引用／调用关系、暂停调试与 `il2cpp_symbols_*` 等共享工具：使用与 Python stdio 相同的工具名和具名参数，不需要手工编码原生命令。
- `native_…`：从原生 HELP 共享目录生成工具，覆盖 IL2CPP、内存、调试、渲染、UI、项目及关系链等。用 `debug_help {"command":"MEMORY_READ"}` 或完整工具名查看真实参数语法。
- 位置参数放入 `arguments` 数组；字面值直接传，`{"text":"…"}` 自动转成 UTF-8 hex，`{"json":{…}}` 自动转成 JSON hex。原本只接受一个 JSON 参数的工具直接传 `payload`。64 位地址请使用 `"0x…"` 字符串，避免 JSON 数值精度丢失。

例如：

```text
native_il2cpp_status {}
native_il2cpp_images {"arguments":[64]}
native_il2cpp_classes {"arguments":[{"text":"Assembly-CSharp.dll"},{"text":""},{"text":"Player"},64]}
native_memory_read {"arguments":["0x12340000",16]}
native_memory_chain_scan {"payload":{"target_address":"0x12340000","max_depth":5}}
memory_read {"address":"0x12340000","size":16}
analyze_function {"address":"0x12340000","decompile":false}
```

Unity／对象检查和精确托管调用工具自动进入原有游戏帧队列，返回请求 ID 不代表执行完成。继续调用 `native_workspace_result {"arguments":[请求ID]}`，检查 `pending`、`success` 和 `error`，不要重复提交写操作。工具说明中使用 `WORKSPACE_QUERY … <argsHex>` 时，HTTP 工具的 `arguments` 填的是说明中 Args 对应的内层参数，服务器负责外层编码。

HTTP 的共享具名工具直接在原生服务中提供，无需启动 Python。反汇编、反编译、静态分析、文件导出和 `native_library_upload` 分块上传均可使用；能力是否可用以目标返回为准。HTTP 无法读取电脑路径，上传字节由客户端提供，电脑本地文件辅助仍可使用下方 Python stdio 服务。原生 HTTP 的分组开关在模块 Web「功能开关」管理；HTTP 也可在「MCP 连接」页整体关闭，关闭后不会影响旧 Socket 服务。

Web 分组配置保存到 `/data/adb/zygisk_il2cpp_mcp/mcp_features.json`，默认全部开启。保存后下次请求生效，禁用工具会从 `tools/list` 隐藏，直接调用和原始命令也会经过分组检查；客户端可能需要刷新工具列表或重新连接。Python stdio 的浏览器管理器控制其自己的本地配置，两个入口不会自动同步。管理接口不注册为 Agent 工具，关闭分组不会自动撤销已开始的任务，也不等于目标内脚本的安全隔离。

安装器生成独立随机令牌，升级保留，文件为 `/data/adb/zygisk_il2cpp_mcp/mcp_http_token.txt`，Root 所有、0600 权限；切勿把完整配置发到公开反馈。令牌缺失拒绝访问。替换令牌后，旧会话失效，需要重新初始化。会话闲置一小时后过期，同时最多 128 个；初始化响应返回 `MCP-Session-Id`，后续请求携带该头和协商的 `MCP-Protocol-Version`。

传输遵循 [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) 的 JSON 响应模式：POST `/mcp`、`Content-Type: application/json`、`Accept: application/json, text/event-stream`，当前支持 2025-06-18／2025-11-25 协议；GET 返回 405，通知返回 202，DELETE 结束会话。每次 POST 携带单个 JSON-RPC 消息及 `Content-Length`，不接受 chunked 上传或旧协议批处理。HTTP 请求头上限 32 KiB、请求体 1 MiB、原生返回 4 MiB；大结果请分页或导出文件。连接丢失不会重放命令；关闭会话／取消通知不会撤销已经开始的目标操作。

支持 `Expect: 100-continue`（值不区分大小写）：通过报头与认证检查后，在等待非空请求体前返回 `100 Continue`；零长度请求体不发送临时响应。超限请求直接返回 413，认证失败直接返回 401，未知 expectation 返回 417，不会先发送 100 再等待请求体。

## 延迟启动

Web「目标设置」→「启动设置」→「延迟启动（秒）」填 0–3600 秒，保存并重启目标。默认 0。等待结束再启动 Hook、悬浮窗、目标命令服务和反编译预检；计时在独立线程，不阻塞游戏主线程。Zygisk 最初映射引导 SO 仍按框架时机执行，并不被这个选项延迟。进程等待期间尚未注册，暂时无法通过 MCP 选择它；已有其他目标可继续使用。

2.6.2 新增多进程连接与单独子进程注入。Web 配置只保留 `com.example.game:minigame0` 时，不注入该应用主进程或其他子进程；保留 `com.example.game` 则仍匹配整包。

### 选择目标进程

所有进程共用一个对外控制端口，内部连接按进程隔离，不需要给每个子进程配置 ADB 转发。

1. `process_list {}`：查看已成功注册的进程，返回 `pid`、`uid`、`process_name`、`session`。此操作不会切换目标，旧目标退出后仍可使用。
2. `process_select {"process_name":"com.example.game:minigame0"}`：精确选择子进程。也可传 `pid`，或同时传两者；匹配不唯一时拒绝选择。
3. `process_current {}`：验证当前进程。后续内存、IL2CPP、注入、追踪等工具使用同一选择，返回内容带有 `target_process`。

仅一个在线目标时，新 MCP 服务自动绑定它；多个目标且尚未选择时，返回 `MULTIPLE_TARGET_PROCESSES`，不会猜测目标。进程退出或重启后返回 `TARGET_PROCESS_EXITED`，需要重新选择并重新查找地址、对象和任务句柄。切换不会清除旧进程中的 Hook、冻结或调试任务，需要先在旧目标中主动结束。

选择保存在当前 MCP 服务内，不写入手机全局默认值；不同 MCP 服务可以操作不同进程。更改连接地址、端口或设备序列号会清除选择；修改超时不会清除。进程发现/切换需要 2.6.2 手机模块；旧模块仅保留原单进程调用协议。此功能不能替代目标自身的 IL2CPP API 或 Unity 兼容能力。

2.6.1 增加自定义 SO 注入／任务状态工具，改进 IL2CPP 关系链短路径搜索、旧版 API 兼容和 AI 逻辑调用。手机模块、MCP 服务和 WebUI 包使用同一版本；更新文件后请重启 MCP 服务，手机原生功能还需更新模块并重启目标。

- `native_library_inject`／`native_library_status`：上传并加载 SO 到已连接目标，支持状态查询与显式 JNI 初始化。
- `il2cpp_relation_find`：默认 `strategy=shortest`，可选有界 `all_paths`，保留字段偏移与实例导航。
- `il2cpp_status`：返回旧版类枚举、数组布局及错误诊断，区分支持与已验证状态。
- `dobby_resolve_symbol`：仅查已加载模块的动态导出，不再扫描磁盘内部符号。
- `logic_program_*`：自定义函数、有界循环、集合运算与显式开启的带参托管调用。
- 原有资源、内存、断点、日志与项目工具继续保留；每项用法可通过 `debug_help` 查询。

新兼容路径和复杂加载场景仍需更多设备验证。自定义 SO 会在目标进程执行，不具备反编译工作进程的隔离能力；

以下是保留的 Python stdio MCP Server，仅依赖标准库。它连接本地 Socket（可按需通过 ADB 转发），并把 IL2CPP、普通 Native 内存、LuaJIT、Dobby、汇编和断点能力暴露为 MCP tools。

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

仅在建立连接失败时自动转发并重试；连接后发生超时/断开不会自动重放命令。目标重启后需重新解析运行时地址。

2026-09-12 测试反馈修复：指针链批量结果/列表兼容原生数组，输入等待不再阻塞并行推送。此修复仅涉及 MCP Python，更新正在使用的 `mcp` 目录并重启 MCP 进程即可，无需为这几项重新编译或刷入模块。

方法查询诊断：方法查询可自动回传执行阶段，断开时 MCP 错误中包含 `last_native_stage`，无需手工抓 logcat。此项需要新版模块配合；旧模块仍按原查询协议工作。

Python stdio 服务还会启动独立的浏览器控制页面，默认地址是 `http://127.0.0.1:27185/`。所有功能开关第一次启动时全部开启，页面修改会立即影响该 Python 服务的 `tools/list`，并默认保存到 `mcp/mcp_features.json`。原生 HTTP 使用上面的手机 Web「功能开关」，不需要启动这个 Python 管理器。Python 可用参数：

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

### IL2CPP 符号覆盖

正常目标不用设置。原有兼容继续保留：标准 API 优先、枚举改名 SO、stripped metadata 只读降级、旧版 API 适配，以及 Unity 6 指针宽度 GC 句柄。自动兼容并不等于万能解密；当导出名被修改且已经确认真实映射时，才需要手工覆盖。

覆盖配置保存在 `/data/adb/zygisk_il2cpp_mcp/il2cpp_symbols.txt`，默认没有映射。支持空行、`#` 注释和 `名称=值`：

```text
# module 可省略；不指定时保留自动模块识别
module=libCustomRuntime.so
il2cpp_domain_get=custom_domain_get
```

左侧必须是工具支持的 IL2CPP API 名，右侧是目标中的真实导出名；不需要把所有标准 API 原样再写一遍。上述名称仅演示格式，请勿直接当作目标配置。

也可使用模块相对偏移，但必须显式指定 `module` 并加入 `allow_offsets=1`，不能仅依赖自动模块识别：

```text
module=libCustomRuntime.so
allow_offsets=1
il2cpp_domain_get=0x123456
```

`0x123456` 是从所选模块基址计算的偏移，不是运行时绝对地址，也不是 ELF 文件偏移；这里仅作格式示例。地址位于可执行映射不代表函数签名、ABI 或语义正确，错误映射可能让目标崩溃。不要凭猜测填写偏移。

Python stdio 和原生 HTTP 使用相同的工具名：

| 工具 | 用途 |
| --- | --- |
| `il2cpp_symbols_list_apis` | 查看允许覆盖的 API 名称。 |
| `il2cpp_symbols_get` | 查看本次启动使用的配置与已保存配置，区分正在使用和等待重启生效的内容。 |
| `il2cpp_symbols_validate` | 只解析和检查配置格式，不调用候选地址，不保存，也不替换当前 API。 |
| `il2cpp_symbols_set` | `confirm=true` 后保存覆盖配置；重启目标后生效。 |
| `il2cpp_symbols_reset` | `confirm=true` 后清除覆盖映射；重启目标恢复原有自动识别。 |

工具参数示例（导出名和模块名须替换为目标已确认的值）：

```text
il2cpp_symbols_get {}
il2cpp_symbols_list_apis {}
il2cpp_symbols_validate {"config":"module=libCustomRuntime.so\nil2cpp_domain_get=custom_domain_get\n"}
il2cpp_symbols_set {"config":"module=libCustomRuntime.so\nil2cpp_domain_get=custom_domain_get\n","confirm":true}
il2cpp_symbols_reset {"confirm":true}
```

MCP 中的 `config` 文本上限为 16 KiB；只填写需要覆盖的 API。`validate` 成功只表示格式及允许项检查通过，不代表函数可安全调用。

建议先读取当前配置、查询允许的 API，再校验、确认保存，最后彻底退出并重启目标。不支持热切换当前进程的 API。持久配置供之后启动的目标读取，填写特定目标的映射时，应避免让其他目标带着不匹配的配置启动。完整参数以 `debug_help` 和工具 Schema 为准；这些是用户要求的符号配置工具，不包含功能限制器的管理接口。

Web 也可以编辑同一份符号配置。菜单采用适配手机、平板和大屏的黑白、黑色文字与直角布局，没有阴影、彩色高亮、圆角、渐变或玻璃效果。以上是当前源码的功能说明，需更新手机模块和 MCP 服务后使用；不代表已经覆盖所有设备或混淆版本。

### 查询、调用与 Hook

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

`il2cpp_dump_file` 可选 `image / namespace / class_name` 精确过滤（`namespace: ""` 选择全局空间）；不传参数导出全量。Dump 文件保存到目标应用的：

```text
files/zygisk_il2cpp_mcp/il2cpp_dump_<随机后缀>.cs
```

对应 Android 路径通常位于 `/data/user/0/<目标包名>/files/zygisk_il2cpp_mcp/`。每次生成独立文件，不覆盖旧 Dump；结果只含 `success/path/class_count`，不返回正文。

流程图使用 `il2cpp_type_graph`；已有 Dump、对象检查器和 `memory_scan_base` 均直接扩展，无重复同义工具。

### 多类型关系链

- `il2cpp_relation_selection`：创建和维护命名选择集，可分批追加任意多个精确类/字段；修改使用 `revision` 防止覆盖并发修改。
- `il2cpp_relation_find`：按 `any`、`all` 或 `ordered` 搜索有界关系路径/网络；中间可经过未选择类型，达到预算会返回 `complete=false` 和 `stop_reasons`。
- `il2cpp_relation_results`：分页读取路径、节点、边和选择器映射。字段偏移是符号偏移，不是绝对地址。
- `il2cpp_relation_resolve`：从实时根对象或现有模块/指针链配方，在游戏帧中校验并加载一条可解引用路径；通过 `workspace_result` 轮询完成结果。

`ordered` 模式中，首个选择器带 `field` 时会约束第一条边，例如 `World.player`；后续选择器带字段时表示精确终点，例如 `Player.health`，标量字段也可以作为终点。因此可表达并验证 `World.player(+0x18) → Player.health(+0x704)`，实际偏移来自当前运行时元数据。返回节点中的类、字段、`storage_address`、引用对象、标量和渲染资格，可分别交给已有类型窗口、`workspace_navigate`、内存编辑/冻结与渲染工具处理；解析工具本身不写内存、不调用 getter、构造器或任意方法。

## Java 层对象渲染与 ImGui 控制

原有 38 个渲染/UI 工具继续保留，新增 33 个工作台与高级 UI 工具，共 71 个；接入默认开启的原有功能组，Lua 程序还依赖 `lua`。浏览器管理接口仍不暴露给 Agent。

新增 `overlay_upsert_window/upsert_node/apply_tree` 支持独立父窗口、子窗口、控件树、表格/分页及绑定；`overlay_program_*` 管理可编程 UI。`render_*_rule(s)` 管理字段规则、血条和自动包围盒。工作台新增帧队列检查器/精确调用、符号书签和目标端剪贴板/文件导出。

- UI：`overlay_status`、`overlay_set`、`overlay_set_window`、`overlay_reset`，管理中英语言、Classic/Dark/Light 主题、可见性、原生折叠、位置尺寸、缩放和透明度。
- 对象：`render_status`、`render_list_objects`、`render_add_object`、`render_update_object`、`render_remove_object`、`render_clear_objects`、`render_set_object_position`、`render_set_object_bones`。
- 样式/相机：`render_set_style`、`render_set_camera`、`render_list_cameras`、`render_set_camera_matrix`、`render_project`。
- Unity 采样：`render_bind_update`、`render_unbind_update`、`render_binding_status`、`render_find_objects`。
- 连续跟踪：`render_track_class`、`render_list_tracked_classes`、`render_untrack_class`、`render_refresh_class`。
- 相机刷新与异步投影：`render_refresh_cameras`、`render_projection_result`。
- 自定义 UI：`overlay_set_panel`、`overlay_set_widget`、`overlay_list_custom_ui`、`overlay_remove_custom_ui`、`overlay_ui_events`。
- 调用日志：`overlay_call_logs`、`overlay_clear_call_logs`，独立于 Toast。
- 图元：`render_set_primitive`、`render_list_primitives`、`render_remove_primitive`、`render_clear_primitives`。

显示走已有 Java SurfaceView，不 Hook EGL。Java 菜单启动后自动探测已加载 IL2CPP 的 MonoBehaviour 帧方法；检查 `render_binding_status` 的 automatic/auto_error/automatic_probes、thread_id 和 age_ms。探测失败保留普通内存/类型工具；先解除自动探针后仍可通过 `render_bind_update` 手动绑定。`render_find_objects` 返回受理状态，需要轮询结果。普通目标可提供手动坐标、骨骼和相机矩阵；对象操作只改变可视化，不销毁或移动游戏对象。

每条命令可通过 `debug_help` 查询 新工具需要同时更新设备模块与整个 MCP 目录（包括 `render_tools.py`），不能只替换 `mcp_server.py`。

矩阵被裁剪时使用 WorldToScreenPoint；此模式的 `render_project` 返回 pending/request_id，通过 `render_projection_result` 获取结果。所有 Unity 投影仍在游戏帧执行。

面板、控件和图元工具接收 descriptor 对象，按 ID 创建或局部更新。普通控件保存描述不执行动作，用户操作才触发绑定命令；Lua 程序需显式启用才能执行。图元新增 quad/polygon/bezier/mesh、前景层和裁剪区域，屏幕模式无需 Unity。手动菜单隐藏窗口树/控件/Lua 程序创建编辑器，MCP 接口及已创建控件仍可用；语言/主题/Toast 在“设置”页，调用历史在“MCP 调用日志”页。

对象位置和已绑定字段按游戏帧采样，旧 `sample_hz/refresh_ms` 参数保留兼容但不再控制采样周期。`workspace_export_result/text/job/logs` 在目标侧导出，返回状态/路径而非文件正文；文件位于目标 `files/zygisk_il2cpp_mcp/exports`，预设位于 `presets`。预设不会自动恢复游戏动作或运行脚本。

语言、主题、缩放、透明度、Toast 及原生窗口/表格布局自动保存到目标 `files/zygisk_il2cpp_mcp/settings/ui.json` 并加载，不恢复旧对象地址或执行游戏动作。手动浏览器改为场景/IL2CPP 分页表格，检查器、调用与分析各自独立窗口；分析默认自动读取范围，长度选项位于高级设置。

### 实例与集合渲染管理

`render_instance_inventory`、`render_inventory_items`、`render_inventory_set`、`render_inventory_status` 在 Python stdio 和原生 HTTP 中使用相同名称与具名参数，分别用于实时实例/集合表、分页查看元素、批量开启或停止渲染、查询或取消批量任务。请求在目标的游戏帧执行，返回请求 ID 后继续读取 `workspace_result`（Python）或 `native_workspace_result`（HTTP）。

实例表先对全部匹配条目按数量降序排序，再返回指定页，数量相同按条目 ID 排序。回执的 `sort=count_desc`、`count_source=latest_game_frame_scan` 表明数量来自最近一次分帧扫描；UI 和 MCP 使用相同顺序。

```text
render_instance_inventory {"refresh":true,"query":"NPC","limit":64}
render_inventory_items {"entry_id":"上一步返回的条目ID","offset":0,"limit":64}
render_inventory_set {"entry_id":"上一步返回的条目ID","enabled":true}
render_inventory_status {"task_id":1}
```

来源限于存活 Unity 对象及其数组/List/Dictionary 字段引用，不执行 GC 堆遍历。渲染对象总数没有固定上限；批量操作分帧处理，`render_inventory_set` 返回 `render_task_id`，继续用 `render_inventory_status` 读取进度，直到 `render_pending=false`。通过 `processed`、`updated`、`skipped`、`truncated` 和 `error` 说明执行情况，取消或错误才标记不完整；`cancel:true` 仅停止未执行部分。`render_list_objects` 按 `offset/limit` 分页返回 `total/returned/has_more`（每页 1..256 条，不限制总数），类发现/跟踪的 `limit=0` 表示全部。条目 ID 不跨进程保存。悬浮窗「渲染 → 所有实例 / 集合」提供相同操作，点击渲染、长按打开对象管理。手机旧模块需要更新才能支持这些工具。

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

指针链从 `module load_bias + base_offset` 或 `base_address + base_offset` 开始。每个 `offsets` 元素执行“读取当前指针，再加该有符号 Offset”；结果返回全部中间步骤。基址扫描的 `workers=0` 自动选择至少 2 个、最多 32 个线程，也可显式指定。默认 KittyMemory 可并行读取，选择驱动时读取在 Root 通道中串行转发；字节匹配使用 KittyScanner。

这些工具不依赖 IL2CPP 初始化，可用于普通 Native、Mono 或其他引擎进程。默认数据读写经 KittyMemory 的严格 `Normal` syscall 模式，也可通过 WebUI 选择外部驱动；只接受完整传输，不以不可读页的伪造数据参与搜索。扫描在有上限的本地快照上进行，保留 nibble 通配符、对齐和区域多选。

### 外部 Root 内核驱动

WebUI 恢复驱动和设备节点设置，保存后重启目标。驱动由外部 Root companion 打开并仅对固定目标 PID 读写；不在注入进程直接打开。`memory_backend_status` 报告后端、transport、state、reason；`unprobed` 表示尚未读写验证，`drivers_enabled:true` 只表示允许选择驱动。KMA 库存在时按 ARM64 条件链接；其他 ABI 使用默认 KittyMemory。失败不静默切换后端。

代码补丁也使用 KittyMemory，临时开放所需页的写权限、写入、恢复权限并刷新指令缓存。Dobby 仍负责安装/解除 Hook 和自己的 trampoline 内部操作；`dobby_patch_code` 保留旧名称以兼容客户端，但不再调用 DobbyCodePatch。

### 模块和重复名称

模块实例通过映射路径与 load bias 区分。同名模块按起始地址排序，`occurrence` 从 1 开始。例如：

```json
{"module_name":"libgame.so","occurrence":2}
```

返回值包含模块整体 `start`/`end`，以及每个 region 的 `start`、`end`、`permissions`、文件 `offset` 和 `path`。

### 搜索和过滤

新增 `memory_search_tabs`：管理独立搜索标签、精确/模糊/联合搜索、改善、结果分页、多选、保存项及文件/剪贴板导出。新增 `memory_batch_edit`：对 1–256 个已选结果或明确保存项进行批量写入/冻结，必须 `confirm:true`，非原子操作、失败即停止。冻结管理复用 `memory_freeze_*`，不增加重复工具。

`memory_search_exact` 新增 `100;200:512` 无序组、`100;200::512` 有序组、`10~20` 范围及混合类型后缀，支持 hex/UTF-8/UTF-16 搜索；高级搜索返回 `sessions` 和兼容的 `searches`。`memory_filter_value` 省略 `value_type` 时按原生结果类型改善，支持变化、大小比较和指定增减值；原普通数值等于/不等于调用保留。`memory_search_results` 支持至 100000 的 offset。请检查 `truncated/stop_reason`，导出的“全部”仅指全部缓存候选。

手动页面和 MCP 使用相同状态。使用 `debug_help` 查看完整参数。断点的 PC/整数寄存器快照和回溯已由 `breakpoint_hits` / `breakpoint_backtrace` 返回，属于采样而非暂停式调试。

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

## 持久日志与调试工作流

- `journal_status`：查询 Root 持久日志、当前精确会话文件、异步队列和写入失败计数，不创建或清理日志。
- `journal_query`：列出会话文件或按字节游标分页读取单个精确会话；一次调用有扫描和返回大小上限，需要按 `next_cursor` 继续。
- `journal_export`：把一个会话复制到 Root 管理的目标隔离目录 `/data/adb/zygisk_il2cpp_mcp/journal/<target>/exports/`，只返回回执、路径和字节数。
- `diagnostic_export`：在同一 Root 导出目录生成有界诊断 JSON，包含日志尾部、模块、后端和系统摘要；不包含原始内存、全系统 logcat 或 tombstone。
- `debug_project`：维护按目标及版本隔离的持久调试项目，支持 list/create/get/update/archive/summary/export。项目只保存符号配置、任务、发现、书签、产物和笔记，不自动重放调用，也不把跨重启的实时地址视为仍有效。
- `debug_snapshot`：捕获对象、List、Dictionary 或一段已校验内存，支持 list/get/diff/export。IL2CPP 捕获在游戏帧中有界执行；它不是全进程一致性快照。跨会话/目标比较必须显式设置 `allow_cross_session=true`。
- `workspace_jobs`：统一查看手动 UI 与 MCP 的会话内后台任务，并可提交严格白名单中的只读命令、查询结果或协作取消。提交成功只表示受理；`cancel_requested` 也不等于已经取消。它不终止线程，不接受写入、Lua、调用、嵌套工作区命令或任意 PID。
- `change_history`：分页读取变更记录、导出或尝试撤销一条当前会话记录。撤销要求精确 session、`confirm=true`，并重新核对后端、映射、当前字节和冻结冲突。
- `debug_stop_all`：在 `confirm=true` 后尽力恢复调试器拥有的暂停线程并停止/暂停冻结、追踪、采样断点、Frida、原生逻辑和 UI 程序。它与撤销分开，不恢复内存、不撤销已调用方法，也不保证回滚任意 Hook 副作用。

命令日志和 change journal 都不是事务审计。change journal 明确是 best-effort：记录/持久化失败时原始内存写入或代码 Patch 仍可继续，因此缺少记录不能证明写入没有发生。只有成功记录且当前环境仍完全匹配的改动才可能由 `change_history(op=undo)` 撤销。

对象快照的 `next_offset/has_more` 用于跨嵌套对象继续捕获，跳页不会跳过引用发现；每页是一次新的有界捕获，不是同一瞬间的全图快照。List／Dictionary 回执保留实际类型。内存差异比较最多 65536 字节，按数值偏移排序，兼容旧快照格式，`diff` 的 `offset` 可到 65536。项目／快照 `export` 只返回成功状态、文件路径和字节数，不返回完整文档。

## LuaJIT tools

- `lua_status`：只查询状态，不创建 VM。
- `lua_execute`：首次调用时创建持久 LuaJIT VM，支持多行脚本和 FFI。
- `lua_logs`：读取脚本、`hookCPU` 回调和 `call` 闭包的持久日志。
- `lua_reset`：移除 Lua 所拥有的 Hook 和主线程泵，并退回未初始化的懒加载状态。

全局 Lua API 包含 `getBase`、`hookCPU`、`hookfunc`、`removehook`、`remove_all_hook`、`call`、`msleep`，以及 `readByte/readDword/readQword/readFloat/readDouble/readPtr/readBytes/readString` 和对应的写入函数。`call(function)` 优先通过 `eglSwapBuffers`，其次 `ALooper_pollOnce` 在主线程执行；若目标不具备这两个符号，300 ms 后由兜底线程执行。内置 `regs_t` FFI 定义可把 `hookCPU` 的 lightuserdata 转为寄存器上下文。

## 汇编与硬件断点 tools

- `assembly_status` / `assembly_assemble`：查询状态并把单条 AArch64 文本指令汇编为机器码。
- `assembly_disassemble`：使用 KittyMemory 读取和 Capstone 反汇编 ARM64 内存。
- `assembly_patch`：汇编后通过 KittyMemory 修改可执行地址并恢复权限。
- `breakpoint_status` / `breakpoint_set` / `breakpoint_list` / `breakpoint_hits` / `breakpoint_clear` / `breakpoint_clear_all`：管理不暂停进程的 ARM64 perf 硬件执行断点和数据监视点。
- `breakpoint_backtrace`：通过 `breakpoint_hits` 返回的 `hit_id` 读取命中时采样的用户栈回溯，并解析每帧所属映射/模块。

汇编、反汇编与硬件断点当前是 ARM64 能力。ARM32 或禁止 `perf_event_open` 的内核会返回明确的 unsupported/failed 原因，其他 MCP tools 仍正常使用。

### 外部暂停调试器

暂停调试器由目标进程外的 Root companion 执行，只允许启动时已绑定的目标 PID，与上面的 perf 采样断点互相独立：

- `debugger_status`：查询 ARM64 支持、拥有的暂停线程、租约和清理状态；状态可用不代表 ptrace 权限已经通过，权限只在实际暂停时确认。
- `debugger_threads`：分页枚举固定目标的线程，同时返回用于抵抗 TID 重用的 `thread_start_time`；当前请求线程不能由此通道暂停。
- `debugger_control`：pause/resume/resume_all/step/step_over/step_out/continue/set_registers/renew/help。暂停必须提交 TID、匹配的启动时间和 `confirm=true`；租约范围 1–15 秒，过期、Broker 断线或所有者退出会触发清理。`continue` 保留调试器控制至租约到期，`resume` 则解除附加并恢复原硬件断点状态。
- `debugger_registers`：只读取调试器已拥有并停止的线程，返回 X0–X30、SP、PC、PSTATE、`stop_id` 与剩余租约；可独立返回 Q0–Q31、FPSR／FPCR，读取不会续租。
- `debugger_breakpoint_set/remove/list/events`：管理已暂停线程的按地址停止断点，读取命中计数和寄存器事件。设置／删除要求当前 `stop_id`、`confirm=true`；设置后显式 `continue` 才会继续运行至命中，条件和跳过命中数见工具帮助。
- `debugger_backtrace`：读取最多 64 帧的 ARM64 帧指针回溯，并报告不完整原因；省略帧指针或 PAC 可能提前终止。

单步、继续、寄存器写入等变更要求当前 `stop_id` 和确认，写 PC/SP 还会检查执行/写映射及对齐。按地址断点和 step-over／step-out 取决于实际权限及硬件槽位，FP/SIMD 读取独立报告可用状态；不提供 SVE、信号抑制或任意 PID 附加。step-out 会拒绝未映射或带 PAC 的 LR，不猜测返回地址。暂停一个线程时其他线程仍运行，也可能等待它持有的锁；暂停期间不要发起依赖 Unity 游戏帧或目标锁的调用。传输错误后先查 `debugger_status`，不要盲目重复变更命令。

Root 通道允许传输失败后重新鉴权，但不重放已经发送的操作；变更响应丢失标为结果未知。重连保留暂停清理状态，且不会复用旧 perf 事件 ID 或 `stop_id`。仍持有暂停线程时，Root 日志／驱动操作会返回忙，避免慢磁盘阻塞租约清理；应先恢复线程，再查询日志或导出诊断。

## Ghidra 反编译

- `decompiler_status`：查询 ARM64 Ghidra Native 引擎、运行时内存读取和 IL2CPP 类型元数据状态。
- `decompile_function`：读取指定地址和范围，返回 Ghidra/Sleigh 生成的 C 风格伪代码。

反编译器使用独立的 `libghidra_decompiler.so`，不依赖 Java、RetDec 或外部服务。输入地址精确命中 IL2CPP 方法起始地址时，会自动注入方法名、返回类型、`this`、托管参数、隐藏的 `MethodInfo*` 参数，以及声明类和继承类的实例字段布局。未命中 IL2CPP 方法时仍可作为普通 ARM64 Native 反编译器使用。

引擎通过受限回调读取目标实时内存，并把 `/proc/self/maps` 中的只读区域传给 Ghidra，用于分析全局数据和已初始化的运行时字符串。函数分析严格限制在请求范围内；范围外直接分支会生成截断桩，不再导致整个反编译请求失败。

当前限制：范围外尾调用可能仍显示为 `halt_missing()`；调用目标尚未批量替换成 IL2CPP 方法名；未初始化的 IL2CPP 编码字符串槽不会自动展开为文本。反编译器缺失、ABI 不兼容或初始化失败只会停用这一功能，不影响内存、Hook、Dobby、Lua 或断点工具。

## 原生 AI 逻辑工具

`logic_program_schema/validate/set/list/get/control/remove` 提供独立于 Lua 的通用 JSON 逻辑运行时。AI 通过 MCP 查询语法并提交变量、条件、遍历、动态对象源、字段读取及绘制/UI 变量输出；目标游戏的字段、筛选规则和窗口布局由调用方下发，底层不预装业务程序。

原生界面的“AI 逻辑”页可编辑、校验、启停和查看诊断。设置 `auto_start=true` 后保存 `default` 工作区预设，可在下次启动重新解析对象源。当前不支持原生逻辑中的方法调用、字段写入或 Hook。

## Help 与兼容性

- `runtime_capabilities`：一次返回内存后端、LuaJIT、汇编、断点、Dobby 与 IL2CPP 的独立状态，不会提前初始化可选能力。
- `debug_help`：传 MCP 工具名时返回本地说明、Schema、来源和功能组，并遵从功能开关；不传时只列出当前对 Agent 可见的 MCP 工具。传未知于 MCP 目录但受支持的原生命令主题时，应用相同功能门控后返回原生 usage，不能用 HELP 绕过已禁用能力。

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
- `dobby_patch_code`：兼容名称；使用 KittyMemory 写入机器码，单次最多 4096 字节。回滚需要自行保留并写回原字节；`dobby_destroy` 不撤销独立字节补丁。
- `dobby_destroy`：卸载通过 Dobby Hook/Instrument 安装的拦截。
- `dobby_list_hooks`：列出由桥接层记录的 Hook 和插桩。
- `dobby_version`：读取内置 Dobby 版本标识。

桥接层还保留现有 Socket 功能：`ping`、剪贴板、Unity 输入框以及 `raw_hook_call`。因此新增原生命令后，无需先改 MCP Server 也能调用。

## 注意

Hook 和代码 Patch 直接修改目标进程。replacement 地址、ABI 返回类型或机器码错误都可能导致游戏崩溃。`dobby_patch_code` 是直接代码写入，不会被 `dobby_destroy` 自动撤销；需要调用方自行保存并恢复原始字节。
