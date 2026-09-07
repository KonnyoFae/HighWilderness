# H1 船壳几何命令（最小交付）

日期：2026-09-07。命令集 `gaotian.hull-edit-commands/v1alpha1`，W0 传输和 W2b 会话 v2alpha1 保持不变。实现复用 HullEditorDocument 和正式船壳编译器，未增加第二套几何合法性或派生规则。

## 新建与命令入口

`editor.create {resource_id, name}` 必须无 session_id/expected_revision，返回无甲板、无文件绑定、dirty=true 的空白会话。版本为 1，固定五米网格；业务资源标记 prototype_unbalanced。正式资源合同当前只允许两种 fixture_level，因此仍使用 canonical_blueprint_fixture，不能据此视为正式平衡舰。创建成功即写恢复记录，空白状态不可保存为正式资源。

其余操作通过 `editor.command {command, arguments}`，绑定后台实例、会话和精确预期修订。以下参数必须恰好匹配，不接收额外字段。material 是精确的 {id, version}，边装甲是 {material, thickness_m}。

| command | arguments | 行为 |
| --- | --- | --- |
| hull.add_deck | deck_id, level, material | 增加空甲板；第一层自动为基底，后续不自动切换 |
| hull.remove_deck | deck_id | 删除甲板及所属区域；删除基底后不猜测替代层 |
| hull.set_base_deck | deck_id | 原子切换唯一基底标记 |
| hull.add_region | deck_id, region | 提交至少三个端点的闭合区域，region 含 id/vertices_m/edge_armor |
| hull.replace_region | deck_id, region | 按 region.id 原子替换已有区域及逐边装甲；拒绝缺失目标和离格端点，不增加区域 |
| hull.remove_region | deck_id, region_id | 删除区域，允许留下空甲板 |
| hull.move_vertex | deck_id, region_id, vertex_index, point_m | 修改一个端点，边装甲顺序不变 |
| hull.insert_vertex | deck_id, region_id, edge_index, point_m | 在指定边之后插点，两条子边继承原边装甲 |
| hull.remove_vertex | deck_id, region_id, vertex_index, merged_armor | 删除点并使用显式的新连接边装甲，覆盖首尾循环情况 |
| hull.mirror_region | deck_id, source_region_id, target_region_id | 沿 Y 轴镜像，创建或整体替换不同的目标区域 |
| hull.set_edge_armor | deck_id, region_id, edge_index, material, thickness_m | 设置指定边装甲 |
| hull.set_structure_material | deck_id, material | 设置甲板结构材料，复用已有语义 |
| hull.rename | name | 修改名称，复用已有语义 |
| hull.batch | commands | 1—32 个 {command, arguments}，禁止嵌套，一次提交和撤销 |

所有点/边下标从零开始。边 i 从点 i 指向点 (i+1)%n；闭合由资源多边形的首尾连接表达，不添加重复终点或 closed 字段。画布未完成的折线将在 H2/H3 本地绘制，闭合后用 add_region 一次提交；专用拆边/删边工具继续后移。

插点可改变原边走向，继承规则不暗示新点一定在原边线上。删除端点必须显式指定合并边装甲，不猜测采用较厚、较薄或平均值；区域至少保留三点，否则只能删除整个区域。镜像保持顶点索引及对应边装甲，后续方向规范化交给既有编译器。

## 草稿、原子性与恢复

领域命令先修改独立候选数据并验证字段，失败不改变原文档、会话或历史；batch 中间字段结构也必须有效，几何合法性在最终候选上编译。无变化操作不增加修订；有效修改只增加一次修订并清除重做分支。

编辑草稿允许空甲板列表和空区域列表，其他字段仍调用既有解析器严格校验。验证器只在临时副本中使用解析见证来检查空容器的头部字段，不将见证几何写入草稿或交给编译器。自交、非对称、无支撑、缺少基底等几何不合法状态可保留、撤销和恢复，正式保存仍必须通过完整编译。

限制：最多 64 甲板、每甲板 256 区域、每区域 4096 端点；新增几何坐标绝对值不超过一百万米，数值必须有限，下标不得用布尔值冒充整数。修订、会话数量、历史长度、文件大小沿用 W2 限制。

恢复写入版本升级为 `gaotian.editor-recovery/v2alpha1`，支持空白/空甲板草稿和相应历史；读取兼容 v1alpha1，仍校验完整性、依赖与字段。恢复不重新授予文件路径权限。持久化失败拒绝本次修改或创建；正式保存格式与战术存档均不变。

## 验证与后续

H1 专项 14 项通过：空白→空甲板→合法船壳→保存→撤销/恢复，边装甲插点继承、首点删除、非均匀装甲镜像，批次原子性、非法字段/修订/磁盘失败、旧资源规范往返与 v1 恢复读取。

相关 Web、阶段 H 和船壳编译器共 8 个 Python 脚本通过（发现 89 个，未执行完整矩阵）。Rust 18 项通过，其中新增真实后台 create→几何 batch→undo；其余生命周期和保存恢复测试通过。本切片未改前端，未将画布操作标为已交付，也未重跑桌面 UI 或完整前端测试。

下一项 H2/H3 最小范围：画布本地绘制与拖动草稿、防抖权威预览、编辑操作入口和派生显示。

## 2026-09-07 硬网格修正

新增区域、插入/移动端点及镜像目标必须落在 2.5 m 半格上，离格坐标返回 editor.coordinate_off_grid；batch 中此错误回滚整个批次。正式编译器原有半格规则不变。历史草稿的结构恢复仍可保留离格坐标供诊断，不自动取整、更改既有设计；正式保存依旧拒绝离格几何。

五米安装格以 (5*i,5*j) 为中心，因此格边界为 5*n+2.5。绘图辅助网格覆盖格角、边中点与中心。新增测试确认：x=[-7.5,-2.5]、y=[-20,20] 的窄矩形包含七个完整格；平移至 x=[-5,0] 则没有完整格。不能将已有半格坐标简单取整至五米倍数。
