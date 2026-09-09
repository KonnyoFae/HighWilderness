# E2.2a 供电、人员、工作模式与跳闸复位

日期：2026-09-09。状态：`E2_2A_IMPLEMENTED / SCOPED_GATE_PASS / COMMAND_DOMAIN_PENDING / FULL_E2_NOT_PASSED`。

本片把现有供电和人员分配规则接入新飞行会话，支持设备耐久变化、人员数量、工作模式、供电策略以及发动机阶段变化触发分配。缺电/缺员时切断输出并锁存跳闸，恢复资源后须显式复位，再经过启动/响应。完整 CIC/遥控/失联/生命周期指挥接线分到 E2.2b；桌面默认路径未切换。

## 代码与合同

- [`tactical_resources_runtime.py`](../../backend/high_wilderness_sidecar/tactical_resources_runtime.py) 持有已编译模块资源、初始模式/人员/策略，复用既有 `_host_availability`、`_manual_staffing`、`_allocate_power` 和推进阶段映射。进场验证资源身份及与设备领域的模块顺序、宿主和耐久上限；显式策略输入在边界严格解析。
- `ResourceOperation` 是内部领域/测试入口，支持 `crew / mode / policy / reset`，没有客户端接口。每舰连续序号、同边界末次操作幂等，冲突/陈旧/跳号/错场景/非法输入拒绝；只保留末次摘要。状态版本由候选生成，不保存无限请求历史。
- `ShipSeed.resources` 和 `FlightShip.resources` 与设备、推进、运动和回执同一候选提交。启用后，不允许人工可用性事件覆盖供电、人员、模式或跳闸事实。
- 推进状态新增 `phase_revision`，只在发动机阶段变化时递增。资源缓存依赖设备版本、阶段版本和资源输入版本；未变时不遍历发动机阶段、不重新分配、不进行 JSON/SHA 验证。有领域操作时可重算整舰共享分配域，本片没有把电力/人员共享池误建为逐设备独立供给。
- 开/收边界先应用领域输入，再处理推进目标/到期变化并核对新需求。因停机释放资源或宿主停机引起的级联，在当前候选内有界收敛：最多发动机数加二次检查，过程中跳闸只增加，不自动复位，不能收敛则拒绝整个步骤。稳定场景直接缓存命中。未声称这等价于旧完整场景的全部步骤顺序；旧分配和阶段映射单独对照。
- `power_unavailable/crew_unavailable` 表示当前事实，`engine_tripped` 保留锁存；停机后负荷减少、资源恢复不会解除锁存。显式复位要求当前拥有该舰实验直控权限、舰体仍有效、目标已锁存且外部阻塞解除；同边界新损毁/燃料故障不能被复位绕过。复位后回到 off，再由现有控制经过启动/响应。供电不足仍可在重新启动时再次跳闸，符合“复位不保证启动成功”。
- 工作模式关闭阻止设备或宿主关联发动机输出。部分受损发电机继续使用现有发电效率曲线；发动机本身的部分掉血仍不直接缩放推力。发动机是否缺员按节流功能自动化及最低操作人数判断，不能把自动化设备也强制按人工设备停机。

具名样例启用方式为 `build_sample_session(..., with_resources=True)`，自动包含设备领域。它仍从完好技术资源创建；动态损伤/模式/人员变化在会话内产生，尚未推广为任意受损玩家设计出航。

## 验证

新增 [`test_tactical_resources_runtime.py`](../../tools/test_tactical_resources_runtime.py) 9 项测试，相关回归共 **82 项通过**（19.420 秒）。覆盖稳定资源复用、缺员断推与锁存、显式复位和启动、阶段需求突增断电、避免自动反复启停、部分发电机损伤、模式和策略、重复/非法输入、跨舰/投影失败回滚、收边界损失保留已积分运动，以及复位权限和同边界故障拒绝。

六种阶段 off/starting/ready/running/stopping/tripped 的供电结果、人员分配及配员比例，与旧 `_runtime_resources` 的阶段投影逐项一致。它是共享子规则对照，不是完整旧场景等价证明；本片缓存与提交机制由独立故障/回滚测试检验。历史全量门禁未重跑。

现有技术舰的主机/侧推器耗电为零且节流已自动化，因此额外使用具名合成配置：蓝舰发动机节流改为人工，主机 active/standby 负荷改为 150/1 kW（不足场景专项测试另用 600 kW），计入 sensors 分配类别。只改内存测试种子，不改游戏原型或玩家设计。红舰仍为原样观察舰。

测量工具：[`verify_tactical_resources_runtime.py`](../../tools/verify_tactical_resources_runtime.py)。有效证据：[`e2-2a-20260909-resources-r2/result.json`](../../artifacts/t3a-realtime-experiment/e2-2a-20260909-resources-r2/result.json)。三负载各 600 步预热、3600 步测量、三次重复，九次均通过平均 <16.67 ms、P95 ≤12 ms、P99 ≤16.67 ms 及整体循环实时因子 ≥1 的范围内门槛。

| 负载 | 最差平均 ms/步 | 最差 P95 ms | 最差 P99 ms | 最大单步 ms | 供电/人员分配次数（每项） |
| --- | --- | --- | --- | --- | --- |
| 稳定飞行 | 0.207 | 0.277 | 0.349 | 1.117 | 0 |
| 改档、转向与制动 | 0.233 | 0.351 | 0.522 | 1.402 | 37 |
| 缺员、供电启停、复位、发电机损伤/测试重建 | 0.242 | 0.403 | 0.607 | 1.279 | 156 |

计时包含操作校验、设备/资源更新、事件生成、阶段需求检查、跳闸/复位和飞行；创建、输入带构造、正确性核对和画像分开。仍无真实 CIC、炮击、普通修复、存档或 UI/IPC，不是墙钟长跑。

每种负载两个独立会话比较初态及 4200 步完整动态舰状态/回执，共 12603 个边界一致；计时运行终态也一致，另检查能力求和及锁存后零输出。这不是独立的完整资源求解器，独立规则证据来自上述阶段参考与故障测试。蓝舰计时段有输出步数为 3600、2778、708；第三种是密集停机/恢复负载，不能把其耗时宣称为持续满负荷作战性能。

三种画像中重复 SHA、旧配平、运行参数编译、快照指纹检查、时间能力解析均为零；资源分配次数来自单独画像，不污染耗时统计。当前无需恢复 E1d 泛化指纹优化。

首次输出目录 `e2-2a-20260909-resources` 保留 FAIL：输入带第二轮仍向已处于 off、未锁存的侧推器发送 reset，后台合法拒绝。r2 只修正后续复位目标，不放宽生产校验；每次仍创建独立会话并保留固定输入。

## 下一片

E2.2b 接真实 CIC、遥控宿主、失联及指挥/生命周期状态到权限变化，复位权限改由完整领域结果提供；还需核对 fuel/emergency 等未接生产者。普通修复/替换继续待具体合法规则，测试重建不能算修复实现。E2.3 再做保存重建及包含全部已接生产者的验收，E3 接调度和桌面联调。

```powershell
python -X utf8 -m unittest tools.test_tactical_resources_runtime tools.test_tactical_devices tools.test_simplified_flight_gate tools.test_simplified_flight tools.test_simplified_propulsion tools.test_internal_step_proofs tools.test_realtime_flight tools.test_tactical_realtime_baseline 高天荒野WebT3a单步操纵测试 -q
python -X utf8 tools/verify_tactical_resources_runtime.py --out artifacts/t3a-realtime-experiment/e2-2a-new-run
```
