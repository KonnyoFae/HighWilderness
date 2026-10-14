# P1a 纯炮主动箔条验证记录

日期：2026-10-14。基线 `aa301d3` 加工作区改动。此目录为功能验证，不是新的性能基准；原六套 7b 输入不变。完整说明见[交付记录](../../contracts/web_bridge/tactical-p1a-manual-chaff.md)。

- `backend-tests.txt`：71 项相关后台测试通过，含 10 项新增纯炮箔条专项。命令为 `python -X utf8 -m unittest tools.test_tactical_chaff tools.test_tactical_ew tools.test_tactical_observation tools.test_tactical_gunnery tools.test_aviation_ew tools.test_tactical_7c1b`。
- `frontend-check.txt`：在 `apps/desktop` 执行 `npm run check`，类型检查和 49 文件 / 212 项测试通过。
- `browser-4/result.json`：最终页面通过结果、一次投放命令及回复、投放前/遮挡/红外接续/暂停/下一场快照和保存库存。`source.json` 冻结七个本片源码与工具文件的 SHA-256，未改来源以基线提交标识；`fixture.json` 保存实际设计、有限库存与 5 公里部署。PNG 为实际页面截图。
- 页面使用真实 React 与 Python，通过 JSON 文本测试绑定交付；不是原生 Tauri IPC。第一条投放故意丢失成功回执，以验证重新读取不会重复消耗；测试结束按钮主动终止第一场，不申报自然终局或完整战斗验收。
- 测试库存位于各场 `store`，被仓库规则忽略。用户存档未参与测试。

## 保留的失败阶段

| 目录 | 实际进展与失败原因 |
| --- | --- |
| `browser-1` | 漏掉正常准备保存，入战按钮正确禁用，未开始交战 |
| `browser-2` | 已入战，但工具仍使用旧“所选舰艇/设备”定位，未找到当前火控折叠面板；未投放 |
| `browser-3` | 投放、失锁、红外、暂停/恢复已通过；保存后过早关闭后台，打断准备页读取，后续页面无法继续。工具最终等待请求结束并关闭页面，再重启后台；此失败不算在线断连恢复通过 |
| `browser-4` | 完整范围通过：正常准备→真实开炮/锁定→无敌弹投放→红外接续→暂停恢复→测试结束/双方保存→关闭页面并重启后台→正常准备再次入战 |

前三场 `diagnostic.json`、失败截图和页面文字保留，不覆盖。更早的工具语法错误发生在执行前；专项初轮修正了独立传感器开关操作与弹体字段，详情见交付记录。

## 复现与审计

1. 在 `apps/desktop` 启动本地测试服务：`npm run dev -- --port 1423 --strictPort`。
2. 根目录执行 `node tools/verify_tactical_chaff_view.mjs <全新输出目录>`。需要本机 Python、Edge 与 Playwright；工具默认读取已配置的 Codex 依赖目录，也可通过 `HW_BROWSER_MODULES` 指定。输出已存在冻结输入时拒绝覆盖。
3. 执行 `node artifacts/tactical-p1a-chaff-20261014/audit.mjs`，只读核对证据索引、最终源码指纹与关键结果。`--write` 仅用于初次归档索引，不用于把证据变化自动变成通过。

区域到期和共享航迹边界由后台专项覆盖；页面未等待全部 45 秒区域寿命。下一阶段 P1b 在新旧同投放规则下测量优化，并继续验证区域当步失效与恢复。固定 1×、250 ms 响应、7d～7f 均未因本次功能通过而关闭。
