# AV5 航空打捞池与领取契约

更新：2026-09-30。配套[实施记录第 10 节](./tactical-carrier-aviation-implementation.md#10-av5-独立打捞池与连续战局2026-09-30)。首版只领取本方资源，敌方保留。这里描述战术结算和测试接收入口，战略战场归属、航行、搜救、费用和医疗另行接入。

## 1. 所有权与版本

新结算有待打捞资产时使用 `gaotian.battle-settlement/av5-v1`，新增 `aviation_salvage`。没有资产时保持原 p3-v3/6c-v4；旧 p3-v1/v2/v3 和 6c-v4 仍按原校验读取。AV5 保留上下文、残骸及已启用的 6c 离场/概率字段，不另抽飞机幸存率。

`aviation_salvage` 为 `gaotian.aviation-salvage/av5-v1`：

| 字段 | 含义 |
| --- | --- |
| `pool_id` / `scene_id` / `settlement_id` | `salvage.<scene_id>`、实际战局、`settlement.<scene_id>` |
| `player_side_id` | 该次战果的本方阵营 |
| `coordinate_space` | 固定 `tactical_scene`，与未来战略坐标分离 |
| `revision` | 初始 0，每笔领取或合并历史迁移加 1 |
| `sources` | 来源 `instance_id`、`side_id`、完整航空 `catalog`；不依赖来源舰继续存在 |
| `entries` | 唯一身份 `id`、`kind`（aircraft/pilot）、来源舰/阵营、`origin` 和完整 `asset` |

飞机条目保存原航空清单行：只有 `location=salvage` 的完好/受损机，不含机组；剩余挂载和机炮弹仍由机体条目持有。人员另行保存 `health/modifiers`，只允许健康或受伤飞行员，安置为 `salvage`，无舰/舱归属。数量以真实独立身份计，不生成代替机或代替人员。

`origin` 记录 `fixed_step`、`position_m:[x,y]`、`height_layer`（upper/cloud/rain）和 `reason`（aircraft_destroyed/no_capacity/ship_loss/facility_loss/battle_end）。运行时航空状态可选 `salvage_origins` 保存逐身份位置；旧档无此字段继续兼容。历史记录没有可靠位置时 `origin=null`，不从今日舰位倒推。固定步失败不会泄漏新位置。

冻结战场的记录是历史快照；待保存战果的 `after` 已移除待移交条目，新增池持有它们。`save` 才把所有舰艇、初始池、结算提交标记一次性写入 SQLite。提交后池独立持有当前未领取资源；不可变战果仍保留当时快照。重复保存已提交战果不重插池。

## 2. 入口与请求

以下方法均经现有 editor/战前准备模式、Rust 白名单及统一 bridge 调用，`session_id/expected_revision` 为 null；业务版本在参数内校验。

| 方法 | 参数 / 结果 |
| --- | --- |
| `tactical.preparation.salvage_read` | `{}`；返回 `scene_revision/can_claim/legacy_count/pools/receivers`。条目附型号名称、体积和 `claimable`，不返回完整目录 |
| `tactical.preparation.salvage_preview` | 下述领取参数；只在私有候选库存计算，不写舰艇、池或回执 |
| `tactical.preparation.salvage_claim` | 同样参数；提交完整领取事务 |
| `tactical.preparation.salvage_migrate` | `{request_id}`；显式归集旧舰存档已保存的待打捞记录，返回 `{request_id,migrated}` |

领取参数为 `request_id, pool_id, pool_revision, receiver_instance_id, receiver_revision, scene_revision, entry_ids`。条目身份列表非空、不重复，上限 1000。接收舰必须是当前测试编队本方的空闲舰艇，有航空货物配置，舰体尚存且状态 available/disabled；未保存战果、活动战斗占用、旧版本和仍打开的物资草稿均拒绝。测试编队选择不定义未来战略所有权。

选择中的飞机要求源/接收航空目录一致；人员不要求机型目录一致。按实际数据卸弹，健康/负伤人员依次安置到机库、兼容军官舱、临时货舱；伤员不治疗，王牌修正不重生成。全量核算后才提交，任一容量/身份错误均不部分领取。身份不能同时在池、任何已存舰艇或重复条目中。

返回 `gaotian.aviation-salvage-receipt/av5-v1`，含请求/池/接收舰/条目身份、新池与舰版本、飞机/飞行员数、`capacity_after`、战略 `supply_inputs` 和 `saved`。preview 的版本为预计提交后版本；不持久化预览回执。给养输入只计实际在舰人员及临时安置/伤员，不扣战术给养。

## 3. 事务、重试与旧档

池、回执为同一仓库中的 `aviation_salvage_pools`、`aviation_salvage_receipts`，记录均有内容摘要。现有 `BEGIN IMMEDIATE` 事务串行化并发修改，领取事务同时更新舰艇版本、移除条目、递增池版本和写回执；异常全部回滚。

精确同一 `request_id` 和请求摘要先返回已存回执，即使旧版本已经过时；同身份改内容拒绝。新请求使用旧版本或已领取身份也拒绝。预览后丢失提交回执必须保留原请求重试，不创建新资源。

旧档迁移同样原子、可精确重试，普通读取不迁移。根据来源舰最新已提交且带阵营上下文的战果验证待打捞身份；缺少归属/归档目录或记录冲突时明确拒绝，不能猜测。迁移保留历史战果原文，将舰艇中的可领取行移入对应场次池，并增加舰艇版本。重试迁移不反复移动，已经归集后新迁移返回 0。

显式“清空战术测试数据”同时清除池及领取/迁移回执；仅移除来源舰不清池。战略层未来复用身份、容量、回执和给养边界，并增加战场控制、合法接收舰和运输过程。
