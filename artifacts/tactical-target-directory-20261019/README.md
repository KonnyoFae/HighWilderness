# 目标名册界面实施与验证

日期：2026-10-19。P2a.1c 已完成本片范围：目录摘要与战术目标列表、专项与双尺寸页面、六场 5400 步及 1362 次读取对照均通过。`audit.py` 已核对 688 个来源文件、结果摘要、页面和后台测试记录，见[汇总](./summary.json)。P2a.1b 与 P2a.1c 的代码、规划及验证证据随本次提交一并归档。

## 实施范围

`gunnery.target_directory` 发布非玩家阵营目标的名称、类别和存在信息，单条仅含 `key`、`target_id`、`name`、`category`。摘要自带战局与修订号，包含活动条目和合法观测仍引用的历史元数据；不附带真实位置、层、速度、耐久或退出原因。成员和公开元数据未变时复用摘要，换层、运动、友方目标变化和联系人重排不单独刷新版本。

战术火控区已接入独立目标名册组件，火炮、导弹、航空子页共用。舰艇和飞机默认展开，导弹、炮弹和炸弹默认折叠，每个分类每页 12 条，提供全部/有效观测过滤。未知目标灰色且不可定位/锁定；失联目标仅能查看合法的最后观测；有观测而无舰级火控的目标可以定位，但不能发出舰级锁定命令。历史记录不计活动数，也不被解释成已击毁。前端不新增未知目标的画布位置或插值数据。

观测始终由当前操作舰提供，包括合法共享数据。旧快照没有摘要时仅显示既有联系人。退出后历史名称取自已发布元数据；从未发布过的短命目标使用合法观测类别/编号生成后备标签，不读取消失实体。

## 验证记录

- 后台最终 **81 项**通过，41.431 秒，详见 [unit-tests.txt](./unit-tests.txt)。覆盖摘要字段/身份类型、非本方筛选、无变化复用、失败候选、历史保留/清理、联系人排序和原视图等价，以及名册、侦测、电子战、箔条与实时发布回归。
- 前端 **50 文件 / 217 项**通过；类型检查和生产构建通过。Vite 仍提示已有大包超过 500 kB，未将此提示当作通过整体性能门槛。
- [最终浏览器报告](./browser-final/browser.json)：1366×768、1920×1080 两种尺寸的未知、失联、过期、无指挥机、观察舰切换、退出历史、旧快照、过滤、分页和新战局检查通过。1002 个活动目标仅渲染 14 行（两条舰机记录及当前页 12 条炮弹），名册宽度 340 像素，无水平溢出。
- 同一浏览器脚本另接真实 Python 后台与冻结混合场景，推进至第 41 步后暂停，检查武器页切换和友舰选择；公开摘要包含 13 个活动目标。真实场景与边界夹具分别记录，边界夹具不声称执行战斗模拟。
- `verification-final-2/` 六场通过，合计 **5400 步 / 1362 次读取**。当前 P2a.1b 模拟代码保持一致，仅将旧 `GunneryBattle.view` 与新增摘要的版本对照。旧方法从固定提交 `352f3e5f5f8bca499a6b77cb27332057f2bd3be3` 提取。每场按 900 步核对原冻结摘要及 18 类状态，并逐次比较所有旧公开字段；新增摘要独立核对活动元数据、历史范围、唯一字段和结果摘要。

完整 UI/发布前后性能比较留在 **P2a.1d**，之后接续已规划的 P2a.1e 舰对舰事件驱动侦测。此次页面功能验收不代表固定 1×、250 ms 指令响应、完整战斗或原生 Tauri 窗口通过。

## 过程记录

最终验收仅使用 `browser-final/`、`verification-final-2/` 和最终测试清单。较早目录保留用于追溯，不合并为最终通过数量。

- 最初前端测试受沙箱临时文件改名限制，移至允许的执行环境后通过；浏览器初次无法连接沙箱内页面服务，改为同一允许环境的本地服务。
- 后台初检发现测试辅助入口名称使用错误，修正后 80 项通过。新增历史顺序专项发现后续发布可能因联系人顺序变化而递增修订号，固定元数据保留顺序后最终 81 项通过。
- `verification/`、`verification-final/` 分别在第一场完成约 300/600 步时主动停止，用于先补齐历史顺序行为，再以最终生产来源重新完整对照。
- `browser-2/` 的边界夹具缺少布局样式且真实页未确认实际推进，不作为最终页面验收；`browser-3/`、`browser-4/` 分别修正夹具定位和测试读取参数。`browser-final/` 已检查侧栏宽度、真实推进和暂停，浏览器错误为零。

## 页面截图

- [实际交战 1366×768](./browser-final/live-1366.png)
- [实际交战 1920×1080](./browser-final/live-1920.png)
- [未知目标边界夹具](./browser-final/unknown-1366.png)
- [密集弹体分页夹具](./browser-final/dense-1920.png)

## 复现

```text
python -m unittest tools.test_tactical_target_directory tools.test_tactical_target_roster tools.test_tactical_roster_detection tools.test_tactical_observation tools.test_aviation_ew tools.test_tactical_chaff tools.test_realtime_view
python -m tools.verify_tactical_target_directory artifacts/tactical-7b-20261009 <新对照目录> --all
node tools/verify_tactical_target_directory.mjs <新页面目录> artifacts/tactical-7b-20261009/mixed-9v9
python artifacts/tactical-target-directory-20261019/audit.py
```

浏览器脚本使用随附 Node/Python 运行时及 Playwright，页面服务监听 `127.0.0.1:1423`。独立边界夹具入口为 `target-directory-test.html`，实际战术入口为 `e3b-test.html?entry=tactical`；两者均不进入生产构建入口。
