# P2 当前负荷归因证据

日期：2026-10-15；生产基线 `7f02db44b88c55f9956c766c8f7e978d62f17397`。本轮只增加诊断工具、证据与规划，未修改生产实现。结论见[负荷与优化方案](../../contracts/web_bridge/tactical-p2-load-and-plan.md)。

- `hotspots-mixed-9v9`、`hotspots-guns-9v9`：各自从原 7b 输入完整推进 900 步，每步核对原摘要；仅在 1～60、601～660 步打开调用分析器。发布/读取各累计分析 30 次，读取分析不包括外层响应编码。含子调用耗时有重叠，不能叠加，更不能当作正常运行时间。报告只保留前 60 个自身耗时/前 100 个累计耗时函数；未入榜不能解释为没有调用。
- `diagnostic-mixed-9v9`：逐次观察生产预测分支、锚点复用、运动失效、环境构建、航空接收和预留变化。全部 900 步核对原摘要及 P1c 的 18 类扩展摘要；分类计数与已提交预测指标交叉核对。多项失效标记可重叠；换键字段与上一阶段同身份最新时间戳样本比较，不代表所有历史样本。接收舰重复计数只是复用机会的上界，没有证明其资源保持不变。本目录不报告带诊断包装的计时。
- `timing-mixed-9v9-1/2`：诊断全部结束后串行执行，每次恢复新战局并清空四类物理缓存，不启用分析器/诊断包装。每步核对原摘要，最终 18 类摘要与 P1c 相同；读取包含响应验证和编码，逐步计时含进程 CPU 与墙钟样本。恢复、摘要计算和工具开销不纳入服务调用时间；不是按现实时间调度的自动降速/固定 1× 服务或页面验收。
- `before-timing-process-check.json`：确认正式计时开始前本任务相关 Python 分析进程为零；没有隔离其他桌面负载、锁频或证明历史波动的外部原因。
- 两次计时的轨迹/导弹分段缓存计数与诊断完全一致，工作量与最终负载相同。本次绝对耗时较前日低，不是新的算法收益，也未消除 P1c 的整场回退结论。

所有运行串行结束，无中止/排除的计时样本。两场调用分析使用现有 `profile_tactical_7c_hotspots.py`；新增 `profile_tactical_p2.py` 只观察原函数返回和指标，不替换生产决策。原始来源记录包含代码/输入、工具与扩展参考哈希。`audit.mjs` 检查当前 681 个生产/输入文件、工具指纹、完整报告、摘要一致、计数平衡与证据哈希。

复现（输出目录必须不存在）：

```powershell
python -X utf8 -m tools.profile_tactical_7c_hotspots artifacts/tactical-7b-20261009/mixed-9v9 artifacts/p2-hotspots-new
python -X utf8 -m tools.profile_tactical_p2 diagnostic artifacts/tactical-7b-20261009/mixed-9v9 artifacts/p2-diagnostic-new
python -X utf8 -m tools.profile_tactical_p2 timing artifacts/tactical-7b-20261009/mixed-9v9 artifacts/p2-timing-new
node artifacts/tactical-p2-attribution-20261015/audit.mjs
```

`--write` 仅用于初次生成汇总与证据索引；日后复核使用不带参数的只读校验。这里的归因不替代六场优化前后对照、回归测试、真实服务、完整战斗或原生窗口验收。
