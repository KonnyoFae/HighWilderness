# O1/O2 舾装资源与最小命令

日期：2026-09-07。O1 与 O2 最小范围完成；O3 画布、用户船壳绑定与空白舾装、武器组设置及 O4 三舰真实 UI 门禁尚未完成。

## 资源与兼容范围

`resource.list` 返回 `gaotian.editor-resource-index/v2alpha1`，保留资源描述及材料选项，新增 `module_options`。每项包含完整规范化 `prototype`、其 `canonical_sha256`，以及原始目录 `catalog` 的精确版本和内容指纹。三份技术目录共 21 个原型、13 类模块；合并时沿用严格目录解析，重复精确身份拒绝，未做推进能力迁移或参数标定。前端按类别、内部/顶挂/侧挂/嵌入方式及版本筛选，显示质量、耐久、功率、人员、自动化、能力和占用/净空数据。

资源描述中的 `editable` 现在同时适用于 HullBlueprint 与 OutfitPlan。使用方必须根据 `kind` 分派，不能把所有可编辑资源都视作船壳。原 `ResourceIndex.listing()` 内部调用仍可获得 v1 形状；新前端兼容 v1 没有模块目录的状态。

舾装复用 `gaotian.editor-session/v2alpha1` 信封和既有 `gaotian.outfit-editor-view/v1alpha1` 领域预览，`draft`/`resource.kind` 明确为 OutfitPlan。没有增加会话字段或改写既有预览；船壳继续使用 H5a 的显式 v2 视图。

本切片只能解析已验证资源目录中的精确版本船壳，支持三份舾装资源及其另存文件。未知船壳引用明确拒绝，不按名称猜测或读任意路径。自建船壳的受控加载、依赖持久化和空白舾装创建在 O3a 接入，不能把本次范围当成完整从空白造舰。

## 命令

均通过 `editor.command` 的 `command` 和 `arguments` 传入；参数必须完整匹配，不接收任意文档替换。

| 命令 | arguments |
| --- | --- |
| `outfit.rename` | `name` |
| `outfit.place_grid`（内部/顶挂） | `instance_id, prototype, deck_id, anchor_half_cell, rotation_deg` |
| `outfit.place_side` | `instance_id, prototype, deck_id, region_id, edge_index, start_slot_index, rotation_deg` |
| `outfit.place_hosted` | `instance_id, prototype, host_instance_id` |
| `outfit.move_grid`（单次位置与旋转） | `instance_id, deck_id, anchor_half_cell, rotation_deg` |
| `outfit.rotate_grid` | `instance_id, rotation_deg` |
| `outfit.move_side` | `instance_id, deck_id, region_id, edge_index, start_slot_index, rotation_deg` |
| `outfit.rehost` | `instance_id, host_instance_id` |
| `outfit.remove` | `instance_id` |

`prototype` 必须为目录内精确 `{id, version}`。半格坐标必须为两个安全整数，不能使用布尔值或小数；前端使用米制输入，严格除以 2.5 后传递，离格输入拒绝。实际占用还须符合模块几何奇偶性、宿主、槽位、净空和船壳安装格。最多 2048 个模块，沿用 64 步历史、8 会话和 8 MiB 传输/存储上限。

普通增删/网格移动形成一个完整候选；结构合法但空间冲突或缺少 CIC 的候选可成为带诊断、不可保存的草稿，最近合法预览带独立修订。畸形参数、未知原型、重复实例等拒绝且不变更历史。侧挂移动和更换宿主在领域文档内部先编译完整替换，失败连文档自身都不修改；成功一次提交、一次撤销，同位置或同宿主不增加修订。更换原型、复制粘贴、批量和镜像后移。

## 保存与恢复

打开、检查、预览、撤销/重做、另存/新版本、关闭和恢复均按资源种类重建领域文档。只有合法规范资源可以写入用户授予的位置；只读资源包、外部文件冲突及失败保存沿用 W2b 保护。

船壳恢复依赖指纹保持原有材料口径，旧船壳草稿仍可恢复。舾装指纹覆盖材料、模块、涂料和目录船壳；保存时重读依赖目录，恢复时比较依赖指纹。发生变化需重新验证，不能把旧历史自动绑定到同名新内容。恢复历史和最近合法源还校验种类一致；派生结果从源重建。

## 验收证据

- 新增 8 项 Python 测试：21 原型/13 类与指纹、四类安装移除后重放、单次网格移动旋转、侧挂成功/失败/撤销、宿主替换原子性、畸形输入和非法空间、三舰服务层文件往返、非法草稿重启恢复、依赖变化拒绝，以及真实 sidecar 进程打开/修改/撤销。更换至第二个可用宿主的正例使用独立合成槽位提供者，不修改正式测试目录。
- 新增 6 项前端测试：联合筛选与精确版本、米/半格转换、离格/非有限输入拒绝、网格移动旋转一次提交、侧挂/嵌入参数以及选定实例移除。
- TypeScript 与 35 项前端测试 PASS；相关 11 / 91 个 Python 脚本 PASS（其余 80 个未运行）；前端生产构建 PASS。H1 原测试筛选从 `editable` 修正为 `kind == HullBlueprint`，仍完整核验原船壳规范往返。阶段 H 历史报告回归通过，未改黄金或 Rust。
- 实际桌面使用原生输入重启后台 `backend.46f10df2bb324a7c95e5d93f273a7971`，打开常规有人舰舾装，显示合法修订 0、18 个模块、21 个目录原型。将货仓从 `(5,10)` m 移到 `(5,15)` m 并应用，得到合法修订 1；设计质量 2,226,780 kg、模块质量 18,850 kg、发电 1,000 kW。撤销恢复合法、与源资源一致的修订 2。既有恢复草稿未操作。

本轮桌面只验收打开、模块目录与一次移动/撤销；其余命令和文件往返为自动服务/领域验证，不能充当 O4 全部真实 UI 往返证据。报告见 `舰艇数据/报告/阶段WebO1O2舾装资源与命令.v1.json`。

复跑：

```powershell
npm --prefix apps/desktop run check
python -X utf8 高天荒野舰艇测试总入口.py --profile full --include '*Web*测试.py' --include '*阶段H*测试.py' --include '*阶段H*回归.py' --include '*舾装编译器测试.py' --include '*规范数据与船壳编译器测试.py'
npm --prefix apps/desktop run build
```
