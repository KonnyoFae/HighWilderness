# 《高天荒野》W0：Web 客户端架构与桥接协议决策

建立日期：2026 年 8 月 28 日。规则代码基线：`3638b61`。

状态：W0 架构与可执行协议合同已落实；W1 真实应用、进程生命周期和 T0 性能尚未实现。

本文落实《高天荒野Web客户端编辑器与战术验证实施计划》的 W0。它不启动桌面应用，不迁移舰艇规则，不建立编辑会话，不标定教程舰。

## 1. 架构决策 ADR-W0-001

采用 Tauri 2 宿主、Vite + TypeScript + React 界面、PixiJS 8 WebGL 二维画布、Python 权威 sidecar。Windows x86_64 为首个开发/发布目标。W1 固定实际依赖版本并提交 npm/Cargo 锁文件，W3 才加入 PixiJS；W0 不声称已验证具体 npm 依赖组合。

```text
React 属性/诊断面板 + PixiJS 画布（W3）
          │ 受控 invoke 请求 / Channel 推送
          ▼
Tauri Rust 宿主：权限、进程、超时、请求配对、字节分帧
          │ stdin/stdout：版本化 UTF-8 JSON Lines
          ▼
Python sidecar：协议适配与串行权威命令
          │ 直接调用，不重新编写公式
          ▼
现有船壳/舾装/出航/运行时编译器与 I1—I12a 规则
```

职责边界：

| 层 | 拥有的职责 | 不拥有的职责 |
|---|---|---|
| TypeScript/React | UI 状态、键鼠、选择、草稿视觉、诊断呈现 | 拓扑、安装合法性、派生性能、战斗裁决、规范文件写入 |
| PixiJS | 坐标显示、几何与精灵、视觉插值 | 权威固定步、碰撞/命中结算 |
| Rust 宿主 | 子进程所有权、路径授权、队列、请求截止时间、IPC、日志限额 | 舰艇规则、编辑历史、将非法草稿判为合法 |
| Python | 资源合同、EditorSession（W2）、编译、战术状态、规范序列化 | DOM/WebGL、窗口和文件对话框 |

双端校验信封不算复制游戏规则。TypeScript 可乐观显示拖动草稿，但不得把预测值作为正式派生结果、战术输入结果或合法保存依据。

宿主只通过自己的桥接命令调用后端。初始化 Rust 侧 shell 插件，使用 `set_raw_out(true)` 读取原始字节，再由有界分帧器处理；不可假定一次 Stdout 回调就是一条 JSON。[Tauri Shell Command API](https://docs.rs/tauri-plugin-shell/latest/tauri_plugin_shell/process/struct.Command.html)

前端不获得任意 shell、启动程序、访问任意磁盘路径或加载远端页面的能力。发布版本只载入打包资源；开发版本只允许显式配置的 Vite 页面。宿主为当前主窗口分配单个事件 Channel；不使用全局 event 传递战术高频数据。Tauri 官方将 Channel 用于流式数据，普通 event 不面向低延迟、高吞吐用途。[Tauri 前后端通信](https://v2.tauri.app/develop/calling-frontend/)

第一版不使用 Pyodide，不建立 Python HTTP/WebSocket 服务，不使用后端本地监听端口。Vite 的本机前端页面/HMR 服务仅供开发，不是规则后端，也不进入发布包。

## 2. 文件布局与现有代码映射

本切片已经建立：

```text
contracts/web_bridge/
  envelope.v1alpha1.schema.json    信封 JSON Schema
  examples.v1alpha1.json          正面示例（不是完整运行日志）
  negative-cases.v1.json          跨语言负面语料
  w1-acceptance.v1.json           W1 运行时验收，全部 pending_runtime
  t0-benchmark-plan.v1.json       T0 负载与指标，尚未测量
高天荒野Web桥接协议.py             无 I/O 的协议参考实现
高天荒野Web桥接协议测试.py         总测试入口自动发现
舰艇数据/报告/阶段W0Web桥接协议接口.v1.json
```

W1 起按需建立以下布局；这些是目标路径，不代表文件已经存在：

```text
apps/desktop/
  package.json / package-lock.json / vite.config.ts / tsconfig.json
  src/app/                       React 入口与诊断页
  src/shared/transport/          接口类型、Tauri transport、测试替身
  src/features/hull/             H 阶段加入
  src/features/outfit/           O 阶段加入
  src/features/tactical/         T 阶段加入
  src-tauri/
    Cargo.toml / Cargo.lock / tauri.conf.json
    capabilities/               仅主窗口需要的权限
    src/bridge/                 supervisor、分帧、pending 请求表
    binaries/                   X2 生成 sidecar，不提交构建二进制
backend/high_wilderness_sidecar/
  __init__.py / __main__.py      ASCII 进程入口
  dispatch.py                   仅已注册能力可被调用
  domain_adapter.py             导入现有中文模块
  sessions.py                   W2 加入
tools/                          W1 环境预检、开发入口；X2 打包脚本
```

不在 W0/W1 批量搬迁根目录 Python 文件或改写其资源定位逻辑。开发进程从显式解析的仓库根目录启动：`python -X utf8 -u -m backend.high_wilderness_sidecar --instance-id <宿主分配的ID>`。解释器路径来自受控开发配置，不来自前端请求；不用拼接 shell 命令。发布时改为打包的同一入口。

后端适配映射：

| 能力方向 | 现有权威入口 | 接入切片 |
|---|---|---|
| 船壳预览/保存 | `HullEditorDocument`、`compile_hull` | W2、H |
| 舾装预览/保存 | `OutfitEditorDocument`、`compile_outfit` | W2、O |
| 出航/实例 | 出航配置、运行时参数、实例设计编译模块 | X1 |
| 战术 | `initialize_tactical_scene`、`advance_tactical_scene_step`、I9 指挥包装层 | T0、T1—T4 |
| 领域错误 | `ContractError.code/path/message`、`EditorDiagnostic` | W1 透传测试、W2 正式接入 |

现有 `canonical_json` 和内容 SHA256 语义不改变；桥接 JSON 的压缩序列化不用于重新定义规范资源指纹。

## 3. 协议版本、信封和命名空间

桥接接口固定为 `gaotian.web-bridge/v1alpha1`，与资源合同 `gaotian.ship/v1alpha1` 相互独立。所有请求、响应和事件携带桥接 `interface`；业务对象继续保留自身的 schema/interface。桥接信封不得写入权威存档。

本 alpha 版本采用精确匹配、封闭信封字段。修改信封字段、必填项、数值语义或安全边界须升级接口，不能静默容忍未知字段。同版本可增加独立可选能力，但必须通过握手能力列表显式协商；已有能力参数形状变化必须版本化。未知方法返回 `bridge.method_not_supported`，不可通过反射调用同名 Python 函数。

| 命名空间 | 本轮冻结的用途 | W1 产品能力 |
|---|---|---|
| `system.*` | 握手、健康检查、关闭、就绪通知 | `system.hello/ping/shutdown` |
| `editor.*` | 编辑文档命令、预览、撤销/重做 | 未启用，W2 起实现 |
| `resource.*` | 注册表与精确资源查询 | 未启用 |
| `save.*` | 规范资源/状态保存、导入与恢复 | 未启用 |
| `tactical.*` | 场景命令和表现流 | 未启用，业务字段 T1 冻结 |
| `strategy.*` | 未来战略模式 | 仅保留，明确拒绝执行 |

字段以 JSON Schema 为结构参照，Python 参考模块另外验证分帧、JSON 可移植性和握手语义。W0 不引入第三方 JSON Schema 引擎：当前测试核对 Schema 字段/常量与正反语料，不能声称执行了完整 Draft 2020-12 校验。W1 的 TypeScript/Rust 实现必须复用同一语料。

| 字段 | 约束 |
|---|---|
| `backend_instance_id` | Rust 每次 spawn 生成的新标识，通过受控参数传给 sidecar；包括 hello 在内不得缺失 |
| `request_id` | Rust 分配 `req.N`，N 为 1—15 位正整数；同一实例严格递增，可跳号；不是前端任意传入的 ID |
| `session_id` | 仅指后端编辑文档会话，不是账号会话或战术场景；全局请求为 null |
| `expected_revision` | 编辑会话请求必须携带当前非负修订号；无会话时为 null |
| `revision` | 会话成功响应/事件的权威修订号；未知会话的失败可为 null；无会话只能为 null |
| `sequence` | 事件在单个 backend 实例内从 1 递增；和请求 ID、修订号、战术步号都无关 |
| `params/result/payload` | 对象；命令、结果、事件的具体字段由各能力校验 |
| `ok/result/error` | 成功为 true/对象/null；失败为 false/null/结构化错误，禁止同时成功和失败 |

其他 ID 为 1—128 位小写 ASCII 标识；中文名称、诊断和路径放在业务字符串中。所有整数必须处于 JavaScript 安全范围，禁止用 bool 冒充整数；浮点只允许有限值。禁止重复 JSON 键、孤立 Unicode 代理码位、非 JSON 容器和超过 64 层嵌套。

编辑修订与战术时间严格分离。未来 `tactical.*` 在信封中使用 `session_id/revision = null`，其 payload 用 `scene_id/input_seq/target_step/step_index` 表达场景和固定步。T0 的实验投影不是 T1 稳定协议；示例中的 `gaotian.tactical-render-placeholder/v0` 仅测试透传，不能注册成生产能力。

### 3.1 分帧与限额

- UTF-8、无 BOM、每帧一个 JSON 对象，以 LF 结束；接收方兼容单个 CRLF，发送方统一 LF。
- stdout 只输出协议；日志/traceback 进入 stderr。不能在初始化、导入或业务方法中向 stdout 打印调试信息。
- 8 MiB 是单帧字节上限，包含 LF 和可选 CR；不是字符数，也不是一次 read 的总长度。
- 必须先按字节缓存和分帧，再解码文本；任意 UTF-8 多字节字符都可能被拆开。
- 超长未结束帧、非法编码、非法 JSON 或截断 EOF 使当前解码器失效；宿主终止该后端代次，不跳过坏数据继续运行。
- 初始最大在途请求 32 个。宿主待发送请求总字节预算 16 MiB；溢出在写入前返回 `bridge.busy`，不能悄悄丢弃。
- 8 MiB 超限是明确错误；不能隐式放大限制。若 T0 发现全状态过大，记录该模式失败并比较精简投影/差量，再复核版本与限额。

### 3.2 W1 三个系统方法

`system.hello` 为首条请求。params 精确包含 `client_name`、`client_version`、`supported_interfaces`、`required_capabilities`。成功 result 精确包含 `selected_interface`、排序后的 `capabilities`、`ship_schema`、`max_frame_bytes`。无共同版本或缺少必需能力不能 READY。握手响应成功后 sidecar 发出一次 `system.ready`，宿主收到并核对后才开放普通请求。重复 hello 视为状态错误。

`system.ping` 的 params/result 均为 `{ "nonce": "小写ASCII标识" }`，原样回显，不读取舰艇状态。`system.shutdown` 的 params 为 `{ "reason": "user_exit" }` 或 `{ "reason": "host_restart" }`，成功 result 为 `{ "accepted": true }`，然后关闭进程。退出前不得再接受普通业务请求。

样例文件包含以上完整请求/响应以及编辑命令、保存、冲突和错误示例。`editor.command` 中的 `command/arguments` 只是 W2/H 的路由草案，不代表现阶段可执行。

### 3.3 一次结算、过期结果与错误

Rust 维护 `(backend_instance_id, request_id)` pending 表；只允许一条响应使其成功或失败完成，已完成/超时请求的迟到响应不能再次完成 Promise。sidecar 使用已见请求数字的高水位拒绝不递增 ID：协议违规必须终止代次、不得再次执行。这样无需无限增长的去重缓存。W0 只校验 ID 形状；高水位和 pending 表由 W1 实现。

宿主重启 sidecar 必须生成新实例 ID，清空旧 pending/事件订阅，不接受旧实例数据。事件 sequence 可有间隔（未来允许替换尚未送出的表现快照），但不能倒退或重复；可靠战斗事件完整性另用 T1 的事件序列保证。

W2 编辑命令在串行权威队列中比较 `expected_revision`，过期即原子拒绝，失败不增加修订号、不改变历史。每个成功改变草稿的命令增加一次修订号；预览和保存不增加内容修订。预览结果只能应用到相同实例、相同会话和相同草稿修订，不能覆盖更新的拖动结果。

错误固定包含 `code/path/message/source/retryable/details`。`source` 为 `host/bridge/domain`；领域错误保留现有 code/path/message。用户错误不含 traceback。`retryable` 只表示用户可在条件修复后重新发起，不授权自动重放修改命令。

| 错误组 | 处理 |
|---|---|
| `bridge.invalid_json/frame_too_large/truncated_frame` | 协议流失效，终止代次 |
| `bridge.unsupported_interface/capability_missing` | 握手失败，禁止 READY |
| `bridge.method_not_supported` | 有效请求的一次失败响应，不影响其他能力 |
| `bridge.revision_conflict` | 回传当前 revision，用户刷新/重新编辑，不自动覆盖 |
| `bridge.request_timeout/backend_exited` | 在途请求有限时间失败，实例进入 FAILED |
| `bridge.busy` | 在写入前拒绝，可由用户稍后重新发起 |

若失败发生在请求已写入管道之后，宿主错误的 `details.outcome_unknown = true`：后端可能已经修改内存或完成保存。禁止把超时伪装成回滚成功；W2 必须重开文件并比较内容指纹，草稿从明确的恢复记录恢复。写入前失败则为 false。不能承诺跨进程崩溃的 exactly-once 执行。

## 4. sidecar 生命周期 ADR-W0-002

```text
STOPPED → STARTING → HANDSHAKING → READY → STOPPING → STOPPED
               └──────── 故障/超时/异常退出 ────────→ FAILED
FAILED ── 用户明确重试、回收旧子进程并分配新实例 ──→ STARTING
```

| 状态/事件 | 宿主动作 | 时间边界 |
|---|---|---|
| 启动 | 预先注册当前窗口 Channel，分配实例 ID，启动受控程序、建立 stderr/stdout reader | spawn 到 READY 共 10 秒 |
| 握手 | 发送 hello，核对响应和 ready 事件；业务请求尚不可提交 | 包含在启动期限内 |
| 普通请求 | 校验大小/能力/队列，分配递增 ID，记录单调时钟 deadline，再写入 | 初始 10 秒；未来长任务需显式复核方法级期限 |
| 空闲心跳 | 无普通在途请求时每 5 秒 ping；忙时由请求自身 deadline 监管 | ping 3 秒 |
| 关闭 | 停止接收新请求，允许正在提交的操作有限时间收尾；回收子进程和订阅 | shutdown 最长 3 秒 |
| EOF/窗口退出 | stdin EOF 令 sidecar 退出；宿主等待/回收自己持有的 child | 超时只终止自己拥有的 PID/句柄 |
| 故障 | 在途请求恰好一次失败；关闭管道、停止事件应用、显示诊断 | 不自动无限重启，不自动重放请求 |

后端进程需要独立的输入读取、串行权威命令队列和输出写入边界。领域命令不能并发修改同一权威状态；I/O 管线不能在 UI 线程阻塞。控制响应与可靠事件优先于可替换表现快照。T0 期间测量队列与写出等待，不能以丢弃权威步来追赶渲染。

未来事件积压达到预算时，允许暂停仿真等待消费或降低快照发送频率；不可丢失权威输入/战斗事件。只有完整表现快照可合并；差量基线丢失必须补完整快照，不能把不连续差量直接应用。具体表现数据结构和流控在 T0/T1 复核。

## 5. 资源、日志与打包边界

资源包默认只读；玩家数据写入 Tauri 解析的应用数据目录，具体路径不写死在 Python 中。W2 文件对话框由 Rust 负责，把用户选定且解析后的路径绑定为短期 `destination_handle`；前端保存请求只传句柄。宿主把句柄解析为受控后端上下文，绝不能直接信任请求里的任意路径。

Python 验证和规范序列化成功后才可原子替换目标文件；非法草稿保存在独立恢复位置，不能覆盖合法资源。保存时的外部文件冲突检查、版本派生、恢复和路径授权由 W2 正式实现，W0 不写玩家资源。

日志分三类：

- 开发日志：stderr 结构化记录，可包含堆栈；单条最多 16 KiB、内存环形缓冲最多 1 MiB，超限截断并计数。
- 玩家日志：默认 INFO，保留实例 ID、请求 ID、错误代码和简短消息；绝对路径、文档内容和环境变量默认不记录，不自动上传。
- 性能报告：由显式测试运行生成，包含机器、场景、计时和计数；不混入确定性权威状态和 W0 合同报告。

磁盘日志轮转初始为每文件 5 MiB、最多 5 份；调试日志不是存档。真实限额及日志高压不阻塞协议由 W1 验证。

发布 sidecar 使用控制台模式可执行文件保留 stdin/stdout，由宿主隐藏子窗口；不要因隐藏窗口使用导致 stdio 不可用的打包选项。X2 使用 PyInstaller 等经过验证的打包流程，并通过 Tauri `externalBin` 带入与目标架构匹配的二进制，玩家无需安装 Python。外部二进制名称需带目标 triple 的规则来自 [Tauri sidecar 文档](https://v2.tauri.app/develop/sidecar/)。W1 只要求开发模式真进程握手，不能据此宣布发行打包通过。

## 6. Windows 依赖与本机实查

Tauri 的 Windows 开发依赖 Rust MSVC 工具链、Microsoft C++ Build Tools 和 WebView2。[官方前置要求](https://v2.tauri.app/start/prerequisites/) Vite 当前文档列出 Node 20.19+/22.12+ 要求，模板可另有要求；W1 按实际锁定版本再核对。[Vite 入门](https://vite.dev/guide/) PixiJS 采用显式 WebGL 初始化，WebGPU 不作为首版前置条件。[PixiJS Application](https://pixijs.com/8.x/guides/components/application)

2026-08-28 的本机只读检查：

| 项目 | 结果 | W1 动作 |
|---|---|---|
| Python | `3.14.7`；既有 46 项回归已复跑通过 | 使用该解释器，W1 再跑新总入口 |
| Node | `24.15.0` | 可用，版本写入构建记录 |
| npm | 主程序 `11.12.1` 可运行；当前 `npm.ps1` 入口误指向缺失的用户目录 CLI | 使用已验证绝对路径，或在开发启动器中进程级修正；不擅改全局安装 |
| Rust / Cargo | 均 `1.97.1`；`stable-x86_64-pc-windows-msvc` | 已安装，但当前 PATH 未暴露；开发启动器显式定位 |
| C++ Build Tools | vswhere 找到 VS 2022 BuildTools 且含 x86/x64 C++ Tools | W1 用真实 Cargo 构建验证 linker/SDK，不能只凭安装目录宣布编译通过 |
| WebView2 | 安装目录可见 `151.0.4129.101`、`151.0.4129.107` | W1 真实窗口核对实际加载版本 |
| PyInstaller | 当前 Python 环境未安装 | X2 前在项目环境安装并锁定版本；不阻塞 W1 开发握手 |

已验证的本机诊断入口（环境特定，不硬编码进发布应用）：

```powershell
python --version
node --version
& 'C:\Program Files\nodejs\node.exe' 'C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js' --version
& 'C:\Users\Yichen\.cargo\bin\rustc.exe' --version
& 'C:\Users\Yichen\.cargo\bin\cargo.exe' --version
& 'C:\Users\Yichen\.cargo\bin\rustup.exe' show active-toolchain
```

未安装新软件，未改系统 PATH、npm 或 Rust 配置。W1 必须先建立可重复环境预检和启动入口，再下载/锁定项目依赖。

## 7. W1 与 T0 的验收安排

W1 的 18 项运行时用例保存在 `contracts/web_bridge/w1-acceptance.v1.json`，覆盖真窗口握手、中文分片、版本/能力拒绝、启动失败/超时、请求超时、重复请求、进程崩溃/代次隔离、坏帧、日志、Channel、健康检查、退出回收、白名单和有界队列。必须分别报告 Python、TypeScript、Rust 和真实桌面检查；浏览器 mock 不能替代真实 Tauri/sidecar 验收。

T0 的 `contracts/web_bridge/t0-benchmark-plan.v1.json` 固定 6/20/30 舰档、普通弹/制导弹/武器事件目标负载，60Hz 权威固定步，20/30Hz 快照，600 步预热、3600 步测量、3 次重复。比较无界面基线、全权威 JSON、实验表现全量和差量；先分级加入运动、普通弹、制导弹和脚本战损重编译。

这些数量只是技术压力目标。场景生成器必须使用现有技术替身和合法运行时状态，保持弹药、装填、制导与生命周期约束；不能为了凑数量改写规则。若目标负载无法维持，要报告实际活动数量并修正夹具，不得将低负载测试标作目标档通过。每个资源/输入流都保存 hash；未冻结概率仍使用可重复脚本裁决。

性能计时必须用同一进程的单调时钟比较起止。跨进程往返由宿主计时并收取 WebView ACK，不相减 Python 与 JavaScript 的绝对时间。单步整体耗时与弹丸/重编译子耗时有包含关系，不得相加重复计算。

目标 20 舰每次重复实时因子均须达到 1.0；总墙钟包含仿真、序列化、背压和尾部发送排空。P95 用最近秩定义，每次重复分别列均值/P95/峰值；不能只给最好一次。固定步预算为约 16.67ms，P95 超出须解释抖动来源。30 舰压力档可降表现帧率，但不能改变权威结果。不同模式最终权威状态和事件 hash 必须与同输入的无界面基线相同。

T0 不测尚未建立的 PixiJS 战术画面帧率；真正渲染、GPU、完整交互的验收在 T4。T0 若不达标，要给出测量证据和优化切片，不能直接延伸大量 UI 后再处理主瓶颈。

## 8. 本轮门禁与下一步

W0 参考合同提供 18 个正面示例、45 个可移植负面用例，以及字节级切分、单帧上限、EOF、非法 JSON/Unicode、确定性和领域错误透传检查。报告显式保留 `w1_runtime_verified = false`、`t0_performance_measured = false`。

2026-08-28 验证结果：W0 定向测试通过；舰艇测试总入口 `47/47 PASS`，包含既有 46 项与新增 W0；复核后的 W0 语料与报告再次定向通过。当前尚无 TypeScript/Rust 工程，因此本轮不宣称其类型检查、单测或桌面运行通过。

验证命令：

```powershell
python -X utf8 '高天荒野Web桥接协议测试.py'
python -X utf8 '高天荒野舰艇测试总入口.py'
git diff --check
```

下一切片是 W1：环境预检与局部路径适配 → 最小 React/Tauri 工程及锁文件 → ASCII Python 入口 → Rust 进程监管与 Channel → 三个 system 方法 → 故障注入和真实窗口验收。W1 通过后先进入 T0 性能风险验证，再进入 W2 编辑会话。

未实现边界：真实应用和启动脚本、编辑器 UI、EditorSession、业务命令调度、权威资源保存、战术表现协议和测试场景、发行打包、正式教程舰。W0 不扩大这些范围。

## 9. W1 实现附录（2026-08-31）

W1 已按本 ADR 的协议和安全边界完成，18/18 运行时用例及真实窗口复核通过。实现报告为《阶段W1Web应用与Sidecar生命周期接口.v1.json》；W0 报告中的 `w1_runtime_verified = false` 保留为 W0 当时事实，不回写历史报告。

实现阶段对进程 API 作了一项受控调整：Rust 宿主使用标准库 `std::process::Command` 持有 `Child`、原始 stdin/stdout/stderr 字节管道和确切 PID，而未引入 `tauri-plugin-shell`。该选择仍只启动宿主配置的受控程序，不暴露前端 shell 权限，并更直接满足字节分帧、单进程回收和“不得按名称批量杀 Python”的验收要求。X2 改用 Tauri `externalBin` 打包 sidecar 时重新复核启动适配层；协议、监管状态机和前端命令接口不因此改变。

W1 锁定的开发入口为 `tools/Invoke-HighWildernessWeb.ps1`。脚本只修改自身及子进程环境，使 Tauri CLI 可发现 Rust 工具链，不修改系统级 PATH、npm 或 Rust 配置。真实窗口验证了自动握手、协商信息、空闲心跳、Ping、显式重启的新实例 ID、优雅停止和窗口关闭后的 sidecar 回收。

下一切片为 T0。尚未实现：编辑会话和资源保存、编辑器领域命令路由、PixiJS 共用画布、战术表现协议、发行 sidecar 打包和正式教程舰。
