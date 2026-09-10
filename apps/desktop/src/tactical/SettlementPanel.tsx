import { endingLabel, resourceRows, serviceLabel } from "./settlement";
import type { SettlementEnvelope, SettlementLibrary } from "./settlement";

export function SettlementPanel({ current, library, busy, canDeploy, onSave, onInspect, onRefresh, onDeploy }: {
  current: SettlementEnvelope | null; library: SettlementLibrary | null; busy: boolean; canDeploy: boolean;
  onSave: (id: string) => void; onInspect: (id: string) => void; onRefresh: () => void;
  onDeploy: (id: string, revision: number) => void;
}) {
  return <section aria-label="战后结算与舰船存档" className="settlement-panel">
    <h3>战后结算与舰船存档</h3>
    {current && <>
      <p role="status">{endingLabel[current.result.reason]} · {(current.result.fixed_step/60).toFixed(1)} 秒 · {current.saved ? "已保存全部参战舰船" : "待保存结算"}</p>
      {!current.saved && <p>有效的在装批次已完成。保存将一并记录双方战损和资源余量；{current.error ? "恢复记录写入失败，请保留当前场景并重试保存。" : "重启后可从下方结算记录恢复。"}</p>}
      {current.error && <p role="alert">{current.error}</p>}
      <button disabled={busy || current.saved} onClick={() => onSave(current.result.settlement_id)}>{current.saved ? "结算已保存" : "保存全部战后结果"}</button>
      <div className="settlement-ships">{current.result.ships.map(ship => <article key={ship.after.state.instance_id}>
        <h4>{ship.after.ship_id === "ship.web.blue" ? "本方舰船" : "敌方舰船"} · {serviceLabel[ship.after.state.service.status]}</h4>
        <p>船壳 {(ship.before.state.hull_integrity_fraction*100).toFixed(1)}% → {(ship.after.state.hull_integrity_fraction*100).toFixed(1)}%</p>
        <div className="propulsion-tables"><table><thead><tr><th>资源</th><th>战前</th><th>战后</th><th>变动原因</th></tr></thead><tbody>
          {resourceRows(ship).map(row => <tr key={row.key}><td>{row.name}</td><td>{row.before}</td><td>{row.after}</td><td>{row.detail}</td></tr>)}
        </tbody></table></div>
        <p>货舱容积 {(ship.capacity_before.capacity_cm3/1e6).toFixed(1)} → {(ship.capacity_after.capacity_cm3/1e6).toFixed(1)} m³；已用 {(ship.capacity_after.used_volume_cm3/1e6).toFixed(1)} m³{ship.capacity_after.over_capacity ? " · 已超容，现有货物保留，暂不能新增装载" : ""}</p>
        <details><summary>模块战损</summary>{ship.after.state.modules.map(m => {
          const before = ship.before.state.modules.find(v => v.module_id === m.module_id)!;
          return <p key={m.module_id}>{ship.module_names[m.module_id] ?? m.module_id}：{before.durability_points.toFixed(1)} → {m.durability_points.toFixed(1)}{m.durability_points === 0 ? " · 已损毁" : ""}</p>;
        })}</details>
        <details><summary>局部装甲战损</summary>
          {ship.after.armor.filter((a, i) => a.durability !== ship.before.armor[i].durability).map(a => {
            const before = ship.before.armor.find(v => v.deck_id === a.deck_id && v.region_id === a.region_id && v.edge_index === a.edge_index)!;
            return <p key={`${a.deck_id}/${a.region_id}/${a.edge_index}`}>第 {a.deck_level} 层 · {a.region_id} · 边 {a.edge_index+1}：{before.durability.toFixed(2)} → {a.durability.toFixed(2)}</p>;
          })}
          {!ship.after.armor.some((a,i) => a.durability !== ship.before.armor[i].durability) && <p>本场局部装甲耐久无变化。</p>}
        </details>
      </article>)}</div>
    </>}
    <div className="editor-row"><button disabled={busy} onClick={onRefresh}>刷新存档与待保存结果</button></div>
    <details open={!current}><summary>结算记录</summary>
      {!library?.results.length && <p>暂无结算记录。</p>}
      {library?.results.map((r, i) => <p key={r.settlement_id}><button disabled={busy} onClick={() => onInspect(r.settlement_id)}>查看结算 {i+1} · {endingLabel[r.reason]} · {r.saved ? "已保存" : "待保存"}</button></p>)}
    </details>
    <details open><summary>本方舰船 · 再次出航</summary>
      {!library?.ships.length && <p>保存第一次战后结算后，可使用保留下来的舰船进入新交战。</p>}
      {library?.ships.map((s,i) => <p key={s.instance_id}>舰船 {i+1} · 船壳 {(s.hull_integrity*100).toFixed(1)}% · {serviceLabel[s.service.status]} · 保存版本 {s.revision} <button disabled={busy || !canDeploy || !s.can_deploy} onClick={() => onDeploy(s.instance_id, s.revision)}>使用舰船 {i+1} 进入新交战</button>{!s.can_deploy && s.service.status === "available" ? " · 尚有未保存结算" : ""}</p>)}
      <p>重新出航保留战损与资源，重置部署位置和瞄准，敌方使用新舰船。当前入口仍使用测试舰设计。</p>
    </details>
  </section>;
}
