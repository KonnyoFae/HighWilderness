# E2.2b 指挥权限与生命周期接线

日期：2026-09-09。状态：`E2_2B_IMPLEMENTED / SCOPED_GATE_PASS / SAVE_REBUILD_PENDING / FULL_E2_NOT_PASSED`。

新会话已从 CIC、具名遥控核心及其宿主、人员/供电/工作模式、升力和舰体完整度派生生命周期与单旗舰直控权限。物理设备恢复不自动解除 I9 的旗舰失权状态；失权后清除旧控制，不能继续接收直控或复位。桌面默认后端未切换。

## 实现与保留规则

[`tactical_command_runtime.py`](../../backend/high_wilderness_sidecar/tactical_command_runtime.py) 编译 CIC、配置指定的遥控核心、相关宿主链及升力参数。运行时仅在设备版本、资源分配版本、舰体完整度或质量改变时重新计算相关功能；未变状态复用结果，不逐步解析整舰/指纹。当前质量固定，动态质量生产仍未接线。

功能效率复用 `RuntimeModuleResult.function_efficiency`；生命周期复用 `project_tactical_ship_lifecycle` 和状态物化规则；失权原因复用 I9 `_direct_loss_reason`。沿用以下条件：

- CIC 毁坏、升力不足或船壳崩溃使舰艇进入 `falling/uncommanded`。已有 falling 状态锁存，测试重建设备不使其重新成为正常舰艇。
- CIC 未毁但基础控制不可用时为 `uncommanded`。遥控模式只读取出航配置指定的遥控核心，不任意挑选其他核心；核心自身及宿主的耐久/模式、供电和功能效率参与判断。
- 遥控链路不可用但 CIC 可用时，有在舰人员则为 `local_only`，无人则为 `uncommanded`；这描述现有设备功能链路，没有新增无线传播距离、干扰或网络心跳模型。
- 单旗舰一旦失去正常场景直控，进入 `command_defeat_withdrawal`，保留失权原因与步号。之后即使恢复 `scene_command`，也不自动恢复旗舰直控或重放旧命令。本片没有舰队撤退 AI、RTS 命令、分旗舰额外损失、晋升或最终动员。
- `crew_lock` 从当前人员数量及进场记录的伤员数量派生，安全载荷与限制使用当前值；伤员数量在本片保持初态，新的伤亡/救治生产者未接入。

`ShipSeed.command`、`FlightShip.command` 与设备、资源、推进、运动和回执一起候选提交。启用命令领域后拒绝外部人工 `AuthorityEvent` 或 `command_unavailable` 事件；旧 E1 人工事件实验仍保留各自入口。新增独立阻塞原因清除后续输出，不用“修改速度为零”模拟失权。

先应用当边界设备/资源变化，再判断指挥权限；推进阶段引起资源分配变化时，指挥依赖也参与有界级联判断。同边界 CIC 失效与一个原本合法的复位请求相遇时，拒绝整个候选，血量、资源、权限和推进均不出现半提交。步末失权保留本区间已积分的运动，只影响后继输出。

内部 `ExitOperation` 支持 `scripted_transfer` 与只允许 falling 舰使用的 `fell_below_scene`，沿用旧场景显式退出的合法性限制。离场后姿态/速度冻结，仅固定步索引随世界推进；拒绝对离场舰写入设备/资源。该入口不连接玩家客户端，不计算自动脱离距离或坠落高度，也不宣称实现新的三维坠落物理。

具名样例通过 `build_sample_session(..., with_command=True)` 启用，自动包含设备和资源领域。仍限于单旗舰实验，没有切换产品默认后端。

## 测试与证据

新增 [`test_tactical_command_runtime.py`](../../tools/test_tactical_command_runtime.py) 11 项测试，相关回归共 **93 项通过**（26.509 秒）。覆盖稳定权限状态复用、CIC 毁坏与永久失权、遥控失联/恢复、手动 CIC 缺员、宿主关闭、无人时安全锁、升力不足、步末失权的因果顺序、人工权限拒绝、有效复位遇到同边界 CIC 失效、显式退出冻结、跨舰/投影失败整体回滚。

CIC 正常/停用/毁坏、升力设备毁坏和发电机毁坏五个案例，与严格旧运行参数编译及生命周期派生对照物理状态、指挥状态和故障原因。共享函数不等于整个新旧场景相等；本片没有重跑仓库历史全量门禁，也没有以修改旧黄金解决差异。

工具：[`verify_tactical_command_runtime.py`](../../tools/verify_tactical_command_runtime.py)，复用已参数化的资源测量工具。冻结结果：[`e2-2b-20260909-command/result.json`](../../artifacts/t3a-realtime-experiment/e2-2b-20260909-command/result.json)。同目录保存输入、完整种子、各终态、每步计时、独立画像、源码/资源归档和校验哈希，运行前后来源清单一致。

三类负载各预热 600 步、测量 3600 步、重复三次；两个独立会话共比较 **12603 个边界**的完整动态舰状态和回执，计时运行终态也一致。采用 E2.2a 的具名人工节流/电力主机蓝舰，加原样红方观察舰；玩家资源未修改。完整遥控和退出机制由专项测试覆盖，吞吐样例使用有人舰。

| 负载 | 最差平均 ms/步 | 最差 P95 ms | 最差 P99 ms | 最大单步 ms | 蓝舰有输出步数 |
| --- | --- | --- | --- | --- | --- |
| 稳定飞行 | 0.321 | 0.506 | 0.715 | 1.757 | 3600 |
| 资源故障、恢复与复位 | 0.298 | 0.448 | 0.768 | 3.278 | 708 |
| CIC 停用后恢复，直控保持失效 | 0.429 | 0.712 | 0.894 | 1.797 | 1500 |

九次均通过平均 <16.67 ms、P95 ≤12 ms、P99 ≤16.67 ms 及整体循环实时因子 ≥1 的范围内吞吐门。计时包含全部已接设备、资源和指挥生产成本；不包含创建、输入带构造、对照/画像、保存和 UI/IPC。故障负载含较长断推阶段，不能把其耗时当作持续满负荷作战性能；本机不同测量存在波动，不据此计算相对前片的精确性能增幅。

三种画像的重复 SHA、旧配平、运行参数编译、快照指纹和时间能力解析均为零；稳定负载供电/人员重算为零，专项测试也确认稳定指挥状态不重新派生生命周期。资源故障和 CIC 失权负载每个分配函数分别调用 156/3 次，来自独立画像。尚无完整战斗、长跑或产品实时结论。

## 后续 E2.3

下一片建立内部状态导出、严格重建与同输入续跑。保存实际耐久/人员/模式、推进响应与排程、跳闸、生命周期、I9 失权原因/时间及退出状态；加载时重建资源/能力/依赖，缓存键和内存证明不持久化。重点防止加载后恢复已失去的权限、解除 falling/跳闸锁存或重放旧控制。

同时列清仍未接入的伤亡/救治、普通修复、燃料/紧急断推来源、动态结构、RTS/自动退出及战斗生产者，决定保存支持范围并明确拒绝未支持状态。E2 完整退出门仍待此轮审计与重建验证；E3 才接墙钟调度、可靠输入、最小画布和长跑。

```powershell
python -X utf8 -m unittest tools.test_tactical_command_runtime tools.test_tactical_resources_runtime tools.test_tactical_devices tools.test_simplified_flight_gate tools.test_simplified_flight tools.test_simplified_propulsion tools.test_internal_step_proofs tools.test_realtime_flight tools.test_tactical_realtime_baseline 高天荒野WebT3a单步操纵测试 -q
python -X utf8 tools/verify_tactical_command_runtime.py --out artifacts/t3a-realtime-experiment/e2-2b-new-run
```
