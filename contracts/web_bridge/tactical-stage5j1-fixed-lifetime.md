# 5j.1 固定寿命与数据口径

后续进度（2026-09-18）：[5j.2 实际换层、重力与连续碰撞](./tactical-stage5j2-layer-maneuver.md)已交付，下文是 5j.1 交付时的历史范围。

日期：2026-09-17。状态：本片实现、针对性回归、实际界面验收及桌面构建通过。接续 [5j 实施计划](./tactical-missile-layer-pursuit-implementation-plan.md)，下一片为 5j.2 实际换层与碰撞。

## 已交付

- 16 种常规导弹与专用拦截弹采用单一固定无动力时长：常规火箭 30 秒、涡喷 45 秒、拦截弹 10 秒，可逐型号调整。上层、云层、雨层不再改变寿命。
- 总寿命仍为助推、主发动机、无动力三段之和；绝对到期步从实际平面出弹计算。VLS 等待不占寿命，暂停冻结模拟计时，发射时跨层降速不减寿命。
- 新飞行配置 `tactical-missile-flight.5j.json` 使用 `gaotian.missile-flight/5j-v1`。历史 5e/5f/5g 文件保留，已知型号及旧三层时长经显式映射转换；未知版本、未知旧型号或未识别的旧时长拒绝静默迁移。目录生成入口同步生成 5j 配置。
- `Profile.coast_steps` 为标量，`lifetime()` 与 `range(ratio)` 不再接收高度层。性能投影升级为 `gaotian.missile-performance/5j-v1`，`coast_s`、`range_m` 为标量，新增 `lifetime_s`；导引头天气探测距离仍保留三层数值。
- 战前与战中性能面板显示固定无动力时长、总飞行寿命和同层直飞参考射程。在途快照分别提供总速度、水平速度和垂直速度，界面显示总速度、水平速度与剩余寿命；旧显示样本可从速度向量回退。
- 库存、配方、整装弹身份及保存的设计定义不因飞行参数改变而重新编译或补满；旧舾装在新战局使用升级后的飞行参数。

## 验证与证据

- 后端既有 45 项相关回归通过：飞行、制导、自动拦截、型号目录及连续战局。新增固定寿命模块 6 项通过，覆盖三个历史版本映射、17 型参数/阶段、逐型号覆盖、非法数据、三类代表弹各三层实际 VLS 出弹及速度投影。首次合并运行中新增夹具错误写入只读世界属性，修正为已有测试使用的内部世界替换后，该模块重跑全部通过；未修改运行逻辑规避断言。
- 前端 3 个测试文件共 11 项通过，覆盖新性能字段、总/水平速度区别、旧样本回退及相关防御/射界组件；TypeScript 构建检查通过。
- 实际浏览器界面连接真实后端并使用隔离存档：战前火箭 30 秒与涡喷 45 秒、战中一致显示、真实 VLS 延迟出弹/暂停、单次发射回执丢失后不重复扣弹、命中、相邻层地点指令、保存及后端重启通过。报告与截图位于 [验收目录](../../artifacts/tactical-missile-fixed-lifetime-5j1-20260917/result.json)，已检查准备及在途截图。
- `tools/Start-Tactical.ps1 -BuildOnly` 通过，桌面程序已重新构建。

复现入口：`python -X utf8 -m unittest tools.test_missile_fixed_lifetime tools.test_missile_flight tools.test_missile_guidance tools.test_missile_defense tools.test_missile_catalog tools.test_tactical_continuity`。前端使用 `missileLifetime.test.tsx`、`missileDefense.test.tsx`、`launcherArc.test.ts`；实际界面复用 `tools/verify_missile_flight_view.mjs`，设置 `HW_MISSILE_FIXED_LIFETIME=1`，并按现有脚本提供浏览器模块路径、前端服务及隔离输出目录。

## 当前边界与交接

本片只改变寿命规则及显示数据；弹体仍按现有二维运动飞行，垂直速度为零，因此实际战斗中的总速度暂等于水平速度。用于验证 300/400/500 速度合成的快照夹具不代表已经实装垂直运动。

5j.2 继续接通实际 5 公里逐层运动、重力加减速、共享过载、50 米/秒无动力上爬中止与回落、失锁行为及同目标不再上爬记录，并与层边界连续碰撞一同交付。数据链异层改攻、跨层防御协调及完整联合验收依次在 5j.3/5j.4 完成；不能将本片通过等同于整包完成。
