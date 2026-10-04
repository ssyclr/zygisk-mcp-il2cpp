# Zygisk IL2CPP MCP

把 Android Unity IL2CPP 游戏进程中的运行时查询、方法调用和 Dobby Hook 暴露给 MCP 客户端的 Zygisk 模块。

## 频道
TG：@il2cppmcp QQ：276342773

## 发电 && 推广
https://ifdian.net/a/__mcp/plan
推荐AI中转站(up同款并非本人业务)https://www.codextopapi.com/

## 已实现

- 通过持久目录中的 `apps.txt` 配置多个目标：不带冒号的包名匹配主进程及子进程；带冒号的完整名称仅匹配指定子进程。
- 通过持久目录中的 `port.txt` 自定义 MCP/命令 Socket 端口，默认 `27184`。
- 原生 HTTP MCP 与旧 Socket 共用端口；HTTP 默认使用 Bearer 令牌，可在 Web 手动开启无需鉴权，每个会话独立选择进程。Python stdio 方式继续保留。
- Web 可设置延迟注入／初始化，0–3600 秒，默认 0；等待在后台线程执行，不阻塞游戏启动线程。
- 由 Zygisk Root companion 读取配置并通过 IPC 传给目标进程，兼容应用进程无法访问 `/data/adb` 的环境。
- KernelSU/Magisk 黑白极简 WebUI：添加/移除多个包名、修改端口、连接检测、自动获取局域网连接地址、复制 MCP 配置、一键导出 `MCP.zip`；适配手机、平板和大屏。
- 游戏内 ImGui 悬浮菜单：Java SurfaceView 显示，使用 `zh_Font.h` 字体、中英双语、原生标题栏折叠/展开与尺寸调整；默认 Classic 紫色主题，可切换 Dark/Light。保留 Toast、Dump、运行状态和内存/ARM64 分析入口。
- 对象可视化：自动游戏帧连接、类筛选、多类自动跟踪、对象多选、相机选择、射线、2D/角框/3D 轴对齐方框、名称/距离/计数、人形骨骼；遮挡检测已移除。矩阵接口被裁剪时支持 WorldToScreenPoint 降级，Unity 调用仍只在游戏帧回调执行。
- 渲染/UI/工作台 MCP 工具：对象与规则、独立父/子窗口、控件树、双向绑定、Lua UI 程序、网格/曲线、检查器和导出。手动操作与 AI 配置共享状态，帮助通过 `debug_help` 查询。
- 原生 AI 逻辑区：7 个 `logic_program_*` MCP 工具，支持自定义函数、有界循环、集合/字符串运算、动态对象源、字段读取、绘制和 UI 输出，以及显式启用的带参托管调用、去重与异步回执，不依赖 Lua。
- IL2CPP：跨 Image 模糊搜索类/方法/字段，字段偏移与类型、完整方法签名、对象字段、数组/List/Dictionary 加载、带参静态/实例方法调用，以及方法 Hook。
- IL2CPP 符号覆盖：支持为已确认的 API 指定自定义导出名，或明确允许后的模块相对偏移；Web 与 Python／原生 HTTP MCP 均可管理，保存后重启目标生效，不在运行中替换 API。
- IL2CPP Dump：支持全量、指定类或命名空间，写入目标应用私有目录 `files/zygisk_il2cpp_mcp/il2cpp_dump_<随机后缀>.cs`；MCP 只返回状态、路径和类数量，不返回正文。
- 非 IL2CPP 内存工具：安全读写映射、模块起止地址/重复实例定位、地址反查、多级指针链、并行基址扫描、字节/类型化搜索及多轮过滤。
- 内存后端：默认 KittyMemory；WebUI 可选择外部 Root companion 驱动，只接管数据读写、搜索/改善、冻结和指针链。KittyScanner 扫描受控快照；代码补丁、Lua、元数据及反汇编/反编译仍使用本地 KittyMemory，驱动不可用不静默回退。
- 搜索：多标签、精确多类型/数值范围/联合/有序组、受限未知值快照、变化/不变/增减与指定增减值改善；区域多选、勾选结果、批量编辑/冻结、保存列表和文件/剪贴板导出，均有 MCP 入口。
- 多类型关系链：命名选择集可分批加入任意多个类或字段，支持 `any`、`all`、`ordered` 搜索和最多 15 层的有界路径分析。`ordered` 搜索中首个选择器带字段时，该字段必须成为首边；后续字段选择器表示精确终点。结果中的类、字段、偏移、实例和标量分别提供导航、检查、编辑/冻结或渲染动作，偏移本身不会被误当成绝对地址。
- Dobby：符号解析、原生地址 Hook、固定返回、Instrument 计数、代码 Patch、Destroy 与 Hook 列表。
- 动态调试：内置 LuaJIT+FFI、ARM64 AsmJit 汇编、Capstone 反汇编/指令修改、Ghidra C 风格伪代码还原、perf 硬件断点与命中栈回溯、Dobby 追踪回溯。Ghidra 直接读取目标的实时内存以解析只读字符串和全局数据；输入地址精确命中 IL2CPP 方法时自动注入返回值、参数、声明类及实例字段 Offset 类型。
- 外部暂停调试：Root companion 仅对启动时固定的目标 PID 提供 ARM64 单线程暂停、通用／FP／SIMD／TLS 及已启用 SVE 寄存器检查与受校验写入、按地址停止断点及命中寄存器修改、单步／step-over／step-out、帧指针回溯和恢复。它与不暂停进程的 perf 采样断点是两套能力；实际可用性以返回的权限、硬件槽位和扩展状态为准，不提供任意 PID 附加，并由 1–15 秒租约和断线清理限制停顿时间。
- 字段与地址分析：`field_activity` 结合静态候选与外部数据监视点分析字段来源；字符串支持 Unicode 搜索、受校验修改、代码／字段交叉定位；地址支持类／字段定位与实际内存关系链续扫。
- 调试工作流：MCP 与游戏内“调试项目”页共享持久项目、对象/List/Dictionary/内存快照与差异、后台只读任务、变更记录/受校验撤销和统一停止。项目保存符号配置和笔记，不在重启后重放调用或信任旧对象地址；快照不是全进程一致性快照。
- 持久日志与诊断：Root companion 把会话日志分卷保存在 `/data/adb/zygisk_il2cpp_mcp/journal/`，目标崩溃或重新启动后仍可从 WebUI 分页查看、筛选、导出或明确确认后清理。诊断包只收集有界日志和模块/后端/系统摘要，不自动收集全系统 logcat、tombstone 或完整内存。
- MCP 功能控制：各组开关默认开启（包含 `native_libraries`）。Python stdio 在默认 `127.0.0.1:27185` 浏览器页面管理；原生 HTTP 在模块 Web「功能开关」管理，保存到手机 Root 持久配置。两个入口分别控制自己的工具目录，禁用工具从 `tools/list` 隐藏并拒绝直接调用；管理接口不暴露给 Agent，不自动撤销已运行任务。
- ARM64 Ghidra 默认异步预检，SO 装载、初始化和分析在限时工作进程执行，不再加载进 Unity 主进程；失败仅停用反编译。普通 IL2CPP 优先标准 API，其他可选能力按需启用。
- 自定义 SO：`native_library_inject` 向已连接目标分块上传并显式执行 SO，`native_library_status` 查询结果；校验 ABI/ELF/传输完整性，不需要额外 adb push。自定义代码在目标内运行，可导致崩溃；不提供不安全卸载或任意 PID 附加。
- JNI Toast：显示当前 MCP tool 与参数，可通过 MCP 开关或主动显示自定义内容。
- 注入目标启动提示：目标进程初始化时会通过 Toast 显示 `TG: @il2cppmcp`；如果 Android 应用上下文尚未就绪，模块会在启动后短暂重试，不影响目标进程运行。
- 无第三方 Python 依赖的 stdio MCP Server，默认自动执行 `adb forward`。

WebUI 和 MCP 配置中不包含陀螺仪功能。

## 配置

模块安装后可直接通过 WebUI 保存配置，也可手动编辑：

```text
/data/adb/zygisk_il2cpp_mcp/apps.txt
/data/adb/zygisk_il2cpp_mcp/port.txt
/data/adb/zygisk_il2cpp_mcp/overlay_enabled.txt
/data/adb/zygisk_il2cpp_mcp/injection_delay_seconds.txt
/data/adb/zygisk_il2cpp_mcp/mcp_http_enabled.txt
/data/adb/zygisk_il2cpp_mcp/mcp_http_auth_required.txt
/data/adb/zygisk_il2cpp_mcp/mcp_http_token.txt
/data/adb/zygisk_il2cpp_mcp/mcp_listen_address.txt
/data/adb/zygisk_il2cpp_mcp/memory_backend.txt
/data/adb/zygisk_il2cpp_mcp/driver_node.txt
/data/adb/zygisk_il2cpp_mcp/il2cpp_symbols.txt
```

`apps.txt` 每行一个包名，例如：

```text
com.example.game
com.example.anothergame
```

目标注入配置在目标进程启动时读取，修改后请彻底结束并重新启动目标游戏。监听地址或端口变更则需保存并重启设备：Root 网关不会随单个目标退出而重新绑定。

「延迟注入／初始化」从目标进程完成 Zygisk specialization 后的后台线程开始计时。等待结束才启动 Hook、ImGui、命令服务和反编译预检；多个进程分别计时。它不能推迟 Zygisk 框架最初映射引导 SO 的时机，也不按 Activity 切换重新计时。`injection_delay_seconds.txt` 使用 0–3600 的整数秒，非法值回到 0。延迟期间，该目标不会出现在在线进程列表中。

HTTP 默认开启，`mcp_listen_address.txt` 默认 `127.0.0.1`，可在 Web「MCP 连接」→「连接设置」中改为 `0.0.0.0`；只接受这两个值，缺失或非法值回到本机监听。安装器仅在文件缺失时写入默认值，升级保留选择。监听地址或端口变更保存后必须重启设备；仅重启游戏不足以生效。Web「MCP 连接」自动检测手机当前 Wi-Fi／热点 IPv4，并使用 `port.txt` 中的端口生成连接 URL；`0.0.0.0` 只用于监听，不能当作客户端连接地址。没有检测到有效 IPv4 时会明确提示，不猜测或复用旧地址；连上网络后重新检测即可。保持本机监听时，电脑可使用 ADB 端口转发。

`mcp_http_enabled.txt` 设为 `0` 可关闭 HTTP，保存后对新请求生效，不中断已开始的调用；原 Socket 不受影响。安装器创建 256-bit 随机 Bearer 令牌（Root 所有、0600 权限），升级保留。默认要求鉴权；令牌缺失时拒绝 HTTP 访问，不会自动降级。

Web「MCP 连接」→「连接设置」可开启「无需鉴权」，对 `127.0.0.1` 和 `0.0.0.0` 均有效。对应 `mcp_http_auth_required.txt`：`1` 要求令牌，只有明确的 `0` 才关闭鉴权；缺失、非法内容或不安全的文件权限均保留鉴权。保存后对新请求生效，切换模式会使旧会话失效，客户端需重新连接；复制的免鉴权配置不包含 `Authorization`。此选项不会改变监听范围，也不会删除原令牌。

免鉴权时，任何能连接端口的应用或设备都可调用已开启的工具，包括内存写入和方法调用。远端仍只能使用 HTTP，旧 raw Socket 仅接受设备本机连接，Python stdio 配置不变。HTTP 没有 TLS 加密，仅用于可信环境，不要开放公网或不可信网络。

原生 HTTP 支持 `Expect: 100-continue`：通过报头与认证检查后，在读取非空请求体前返回 `100 Continue`；未知 expectation 仍返回 417。请求仍需 `Content-Length`，不支持 chunked 上传；超限请求直接返回 413，不先返回 100。

WebUI 的“注入 ImGui 窗口”默认开启；关闭并保存后，下次启动不创建 Java 悬浮窗/SurfaceView/ImGui 绘制线程，屏幕对象绘制也停用，MCP 和原生调试仍可使用。配置文件为 `1`（开启）或 `0`（关闭），缺失时默认开启；它不同于 `overlay_set(visible=false)` 临时隐藏窗口。
`memory_backend.txt` 默认 `system`（KittyMemory），不需要驱动。可在 WebUI 切换外部 Root 驱动，GT1/QX 需在 `driver_node.txt` 填写真实 `/dev/...` 字符设备节点；配置生效需重启目标。外部驱动仅支持 ARM64，权限/内核或协议不匹配时明确报错。

`il2cpp_symbols.txt` 默认没有覆盖映射。标准 API、改名 SO 枚举、stripped metadata 只读降级、旧版 API 适配及 Unity 6 GC 句柄兼容继续保留。它不是通用解密器；手工指定符号只应使用已确认的映射，错误签名或偏移仍可能造成崩溃。

## 游戏内悬浮菜单

目标进程注入后，菜单在前台 Activity 就绪时显示，标题为 `IL2CPP MCP v2.7.9 TG:@il2cppmcp QQ:276342773`。保留 ImGui 原生标题栏移动、三角折叠/展开与边框缩放；横屏浏览与详情分窗，竖屏使用接近屏宽的可切换窗口。

- **场景**：表格形式的场景/对象树，箭头展开、名称打开独立检查器；支持分页、重试和缺失场景接口的降级查询。
- **IL2CPP**：程序集/类列表与可展开方法面板；单击类打开独立类型标签页，双击或“独立类型详情”打开新窗口。支持上下排列/左右分栏、方法调用/固定返回/追踪/断点/分析入口、独立对象选择窗，并自动带入选中实例。字段值、字段类型路径导航、类/命名空间 Dump 与关系流程图保留。
- **工具**：模块、程序集、Hook、断点列表；运行状态与 Dump 放在折叠区域。内存/汇编/伪代码在单独的分析窗口。
- **分析窗口**：按类型/映射自动决定读取长度；本地每页数量可设 1–100,000（默认 32），远程接口保留各自批量上限；内存每行 16 字节。伪代码通过 Ghidra 地址映射双向同步汇编，可从指令启动计数、追踪和断点。未知函数范围仍有 16 KiB 上限与估算提示。
- **渲染**：对象、类跟踪、相机、样式和规则各自分页。对象和类支持搜索、多选、表格编辑与检查器跳转，不依赖 AI 才能操作。
- **扩展控件**：只有 AI/MCP 创建了面板才出现，用于操作已有控件。隐藏面板、窗口树和 Lua UI 程序的手动创建编辑器；对应 MCP 工具及 AI 创建的窗口保持可用。
- **AI 逻辑**：通用 JSON 程序编辑、校验、保存、启停、重启和删除；查看对象源、运行成本及错误，不内置特定游戏的控制面板。
- **MCP 调用日志**：时间、来源、状态、命令、耗时列表与独立详情区；默认只看远程请求，可筛选、暂停、复制和导出。关闭 Toast 不会关闭日志。
- **设置**：中文/英文、Classic 紫色/Dark/Light、缩放、透明度、每页数量、三类屏幕日志开关和 MCP Toast；修改后自动保存加载，窗口/表格布局也自动保存。可选预设和书签放在设置的独立分页。

内存页新增冻结任务、搜索和基址链管理；调试页分别管理追踪与断点。屏幕日志 8 秒后渐隐，显示控制独立于 Toast。

局部 Dump/对象 JSON/Ghidra 异常处理、UI 可调扫描预算、独立全屏关系图、方法调试快捷入口，以及 Root companion 外部硬件采样。安装时随机化 SO 实际文件名，保留 ABI 入口链接；新增改名 IL2CPP 库识别。随机名称不代表注入不可检测，硬件断点仍取决于内核支持。

设置保存于目标应用 `files/zygisk_il2cpp_mcp/settings/ui.json`，后台合并写入，不在绘制线程访问文件。界面偏好、Toast 和布局自动恢复；保存为 `default` 的工作区预设也会尝试加载。原生逻辑程序只有在预设中设置 `auto_start=true` 才自动启动，对象源重新解析；旧对象句柄、游戏写入和方法调用不会恢复，Lua UI 程序保持停止。

菜单复用当前进程接口：普通数据读写使用选定后端，补丁和 IL2CPP 解析保持本地 KittyMemory，托管引用写入保留 IL2CPP GC 写屏障。对象查询/调用在实际游戏帧队列执行，分析在后台串行执行；单次工作台结果最多 512 KiB，大量搜索结果通过文件流式导出。检查器、分析、调用结果、日志与配置支持剪贴板/目标私有文件导出。纯渲染编辑不销毁或移动游戏对象；字段写入与方法调用仍需明确确认。MCP 浏览器开关不是本地菜单权限系统，也不会自动撤销已有 Hook。

Java 菜单启动后自动探测已加载 IL2CPP 中合适的 MonoBehaviour `Update/LateUpdate/FixedUpdate`，不再要求用户先填写帧绑定表单。探测有数量上限，需要可用的帧计数接口；最多安装 16 个透传 Dobby 探针，观察到真实回调后才启用游戏帧查询。探测失败显示原因，类型浏览与普通内存工具仍可使用；`render_binding_status` 可查看已安装探针，`render_unbind_update` 会移除它们并停止本进程自动探测，仍保留 MCP 手动绑定作为高级入口。

显示层使用 Java 管理的独立透明 SurfaceView 和 EGL/GLES3 上下文，不 Hook 游戏 EGL；窗口、详情窗和下拉弹窗之外的触摸交还游戏。Activity 暂停/销毁后释放 Surface，恢复时重新挂载。DEX、JNI 或 GLES3 失败只停用显示层，无界面子进程不显示。字体嵌入根目录 `zh_Font.h`。

矩阵缺失时尝试 WorldToScreenPoint，相机枚举缺失时尝试 FindObjectsOfType(Camera)。缺失能力只影响相应功能。屏幕图元无需 IL2CPP，非 IL2CPP 世界坐标绘制可提供相机矩阵。

菜单 Java 源码位于 `module/src/overlay/java`。正常 Gradle 构建会自动执行 `generateOverlayDex`，使用当前 Gradle JVM、Android Gradle 插件配套的 D8 和 Android SDK 类库生成并嵌入 DEX，不需要独立 APK 或额外 JDK；不再直接调用 SDK Build Tools 内可能过旧的 D8。Android 8+ 从内存加载，Android 6/7 使用目标应用私有 `code_cache`。

## MCP 启动

```powershell
python mcp/mcp_server.py --port 27184
```

启动后浏览器功能控制页面默认位于：

```text
http://127.0.0.1:27185/
```

开关保存到 `mcp/mcp_features.json`。使用 `--admin-port` 修改端口，使用 `--no-admin` 关闭页面；管理端口监听非本机地址时必须同时配置 `--admin-token`。

使用原生 HTTP 时，在手机模块 Web「功能开关」管理分组，配置保存到 `/data/adb/zygisk_il2cpp_mcp/mcp_features.json`，下次请求生效；客户端可能需要刷新工具列表或重新连接。它与 Python 管理页面的配置分别保存，不会自动互相同步。功能开关不是目标内脚本的安全隔离，也不会撤销已有 Hook 或冻结。

正常构建模块时，`generateMcpArchive` 会从 `mcp/` 源码生成客户端 ZIP，包含渲染/工作台/调试/逻辑以及关系链、暂停调试、任务和工作流工具模块及相关接口文档。WebUI 一键导出的 `MCP.zip` 不再依赖模板中的旧静态压缩包；使用新工具需同时更新设备模块和客户端文件。

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

ARM64 伪代码能力使用 Apache-2.0 许可的 [ghidra-native](https://github.com/radareorg/ghidra-native)，由 Ghidra Decompiler 与 Sleigh 恢复 C 风格代码，无需 Java 或 RetDec。反编译库的装载、初始化和分析在独立工作进程完成；默认使用代码快照与已缓存的 IL2CPP 类型信息，可显式请求已知方法的类型增强。工作进程崩溃、超时或不兼容会返回具体原因并停用本会话的反编译能力，不保证隔离目标自身故障或自定义 SO 副作用。

## 风险提示

此项目面向你有权调试的应用。错误的实例地址、replacement 地址、返回 ABI 或机器码 Patch 会直接导致目标进程崩溃。代码 Patch 不会由 `DobbyDestroy` 自动恢复。

持久命令日志和 change journal 都用于诊断与辅助恢复，不是事务系统。尤其 change journal 是 best-effort：记录或存储失败不会阻塞原始写入，因此“没有记录”不能证明“没有发生写入”。`change_history` 只能在会话、后端、映射和当前字节仍完全匹配时撤销已记录的写入；`debug_stop_all` 也不会恢复内存、撤销已调用的方法或回滚任意 Hook 副作用。
