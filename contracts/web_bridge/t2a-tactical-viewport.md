# T2a 战术画布与模式切换（2026-09-08）

> 2026-09-12 O4 状态更正：用户已确认 O4 测试实际完成，旧待验收/待留档记录有误，当前为 `O4_COMPLETED_USER_CONFIRMED`，不再安排 O4 收尾。见[验收状态更正](./o4-outfit-ui-acceptance.md)。下文 O4 未完成表述仅保留为历史记录，不代表当前待办；原自动测试与文件检查结果不变。

状态：`IMPLEMENTED / browser_and_native_passed`。当前交付为暂停场景的观察与编辑切换；T3a 操纵、固定步调度与插值尚未实现，M4 未完成。

## 已实现

- PixiJS WebGL 画布显示两阵营、多甲板船壳、部件内部/顶挂/本体占用、船艏箭头、选中轮廓与速度向量。右侧可选舰并查看速度、位置、朝向、船壳完整度及各部件耐久；选择红方不会产生控制命令。
- 点击选舰、拖动平移、滚轮和按钮缩放、聚焦所选舰、适应全场；画布方向键、Home、＋/− 与 Escape 提供键盘操作。
- 局部 +Y 是船艏，航向沿用内核逆时针弧度，屏幕 Y 仅翻转一次。`internal_cells/top_cells` 为 `[层, x格, y格]`，x/y 乘 5 米；`body_points` 为 `[层, x米, y米]`。占用已编译安装旋转，渲染不重复旋转。
- 静态对象按场景/静态指纹缓存；重复读取不重建匹配几何。姿态更新只改容器，耐久改变部件透明度。当前无动画 ticker；离开战术时销毁渲染器、GraphicsContext 和监听器，保留镜头与选择；新场景/新静态指纹重置镜头。
- 编辑器始终保留挂载，未提交名称、检查表单和武器组草稿不因视图切换丢失。绘图、拖动、提交及未保存关闭决定阻止切换；窗口关闭提示通过独立弹层显示，隐藏编辑器仍能拦截退出。

## 模式合同

新增能力 `tactical.set_mode`，经既有 `bridge_tactical_request` 接入。W0 信封不变，编辑 `session_id/expected_revision` 均须为 null。

参数必须且只能是 `{"mode":"editor"}` 或 `{"mode":"tactical"}`。返回 `{mode, paused:true, scene_id}`；无场景时 scene_id 为 null。操作幂等，在既有串行权威工作队列执行，不创建、释放或推进场景。后台重启初始模式为 editor。

战术模式中后台拒绝所有编辑修改、会话变更及文件授权/保存；`resource.list/editor.inspect/editor.preview/editor.recovery_list` 保持只读可用。锁定错误为 `tactical.editor_locked`；非法模式为 `tactical.invalid_mode`，额外字段和错误作用域沿用战术错误。已有 create/inspect/close 合同保持兼容。

前端在匹配的模式与暂停确认到达后启用相应视图。若确认丢失，两个界面暂时锁定，通过再次选择视图发送幂等 set_mode 恢复；不猜测后台已切换。T3a 必须在此入口真正暂停调度和处理临时输入后，才释放编辑权限；当前 paused 为 true 的依据是尚未开放任何运行入口。

## 桌面 CSP 修复

首次桌面验证暴露 `Current environment does not allow unsafe-eval`：普通 Vite 浏览器页没有桌面脚本限制，默认 Pixi 初始化在原生 WebView2 中被 CSP 拒绝。

新增统一 `src/rendering/pixi.ts`，先加载 Pixi 自带 `pixi.js/unsafe-eval` 兼容实现，再导出渲染类。该模块替换动态代码生成，供船壳与战术画布共同使用；`tauri.conf.json` 的 `script-src 'self'` 保持不变。浏览器验收新增禁止 eval 的响应头，原生构建也已重新验证，避免仅凭宽松开发页判断修复生效。

## 实际验证

- Python：T1a、T2a、W2a、W2b 共 31 项通过，含无副作用模式切换、非法输入不解锁、读写所有权、队列和编辑保存回归。
- Rust：20 项通过，真实 Python 子进程覆盖新增模式方法、战术中编辑拒绝、恢复编辑和旧实例拒绝。
- TypeScript 与前端 68 项测试通过；新增实际场景几何、坐标反变换、两舰/外挂选取、全场边界及镜头缩放不改变权威数据的检查。
- 浏览器：`tools/verify_tactical_viewport.mjs` 的 8 组交互检查通过，包含禁止 eval 的 WebGL 初始化、草稿保持、绘图阻止切换、丢失模式确认恢复、资源释放及紧凑窗口。该页使用真实 T1a 几何投影与交互替身，不冒充真实 IPC。
- 桌面：重新构建 debug 程序，在真实 Tauri WebView2/Rust/Python 上执行 `tools/verify_tactical_native.mjs` 的 4 组检查：创建与渲染、选舰/聚焦/回读、切换保持及关闭释放。没有修改设计文件。最终又启动并保留正常暂停场景供用户测试。
- 生产前端和桌面构建通过；既有大包提示保留。未重跑历史全量，也未作实时性能、开火命中或 O4 三舰正式留档结论。

证据：`artifacts/t2a/browser-verification.json`、`native-verification.json` 与同目录截图。浏览器工具通过 `HW_BROWSER_MODULES` 使用已安装 Playwright；原生工具的 `HW_TACTICAL_CDP` 必须指向专门启动的测试桌面 WebView2 端口，不应用于正在编辑的用户窗口。

下一项为 T3a 前段：先补 I9/v7 显式指挥仲裁适配，再实现暂停下的单步、车钟、转向与制动；连续运行、有界未来步输入和快照插值接续实施。
