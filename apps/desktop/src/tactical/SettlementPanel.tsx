import { endingLabel, resourceRows, serviceLabel, fuelTankName } from "./settlement";
import type { SettlementEnvelope, SettlementLibrary } from "./settlement";
import { ammunitionName } from './ammunition';

export function SettlementPanel({ current, library, busy, canDeploy, onSave, onInspect, onRefresh, onDeploy, onPrepare, canPrepare = false }: {
  current: SettlementEnvelope | null; library: SettlementLibrary | null; busy: boolean; canDeploy: boolean;
  onSave: (id: string) => void; onInspect: (id: string) => void; onRefresh: () => void;
  onDeploy: (id: string, revision: number) => void;
  onPrepare?: () => void; canPrepare?: boolean;
}) {
  return <section aria-label="战后结算与舰船存档" className="settlement-panel">
    <h3>战后结算与舰船存档</h3>
    {current && <>
      <p role="status">{endingLabel[current.result.reason]} · {(current.result.fixed_step/60).toFixed(1)} 秒 · {current.saved ? "已保存全部参战舰船" : "待保存结算"}</p>
      {!current.saved && <p>有效的在装批次已完成。保存将一并记录双方战损和资源余量；{current.error ? "恢复记录写入失败，请保留当前场景并重试保存。" : "重启后可从下方结算记录恢复。"}</p>}
      {current.error && <p role="alert">{current.error}</p>}
      <button disabled={busy || current.saved} onClick={() => onSave(current.result.settlement_id)}>{current.saved ? "结算已保存" : "保存全部战后结果"}</button>
      <div className="settlement-ships">{current.result.ships.map(ship => <article key={ship.after.state.instance_id}>
        <h4>{ship.after.ship_id === "ship.web.red" ? "敌方舰船" : "本方舰船"} · {serviceLabel[ship.after.state.service.status]}</h4>
        <p>船壳 {(ship.before.state.hull_integrity_fraction*100).toFixed(1)}% → {(ship.after.state.hull_integrity_fraction*100).toFixed(1)}%</p>
        <div className="propulsion-tables"><table><thead><tr><th>资源</th><th>战前</th><th>战后</th><th>变动原因</th></tr></thead><tbody>
          {resourceRows(ship).map(row => <tr key={row.key}><td>{row.name}</td><td>{row.before}</td><td>{row.after}</td><td>{row.detail}</td></tr>)}
        </tbody></table></div>
        {ship.after.state.weapons.map(w => {
          const before=ship.before.state.weapons.find(v=>v.module_id===w.module_id);
          return <p key={w.module_id}>{ship.module_names[w.module_id]??w.module_id}待发弹种：{ammunitionName(before?.ready_rounds ? before.recipe_id : null)} → {ammunitionName(w.ready_rounds ? w.recipe_id : null)}</p>;
        })}
        <p>货舱容积 {(ship.capacity_before.capacity_cm3/1e6).toFixed(1)} → {(ship.capacity_after.capacity_cm3/1e6).toFixed(1)} m³；已用 {(ship.capacity_after.used_volume_cm3/1e6).toFixed(1)} m³{ship.capacity_after.over_capacity ? " · 已超容，现有货物保留，暂不能新增装载" : ""}</p>
        {!!ship.after.state.damage_controls?.length&&<p>损管消耗已按灭火、部件维修和船壳维修分别计入上表。下方耐久为战损与修复后的净结果；结算不会额外维修。</p>}
        {!!ship.after.state.fires?.length&&<p>尚有 {ship.after.state.fires.length} 处火情，会随舰船保存，下次入战继续处理。</p>}
        <details><summary>模块耐久（战损与修复后的净结果）</summary>{ship.after.state.modules.map(m => {
          const before = ship.before.state.modules.find(v => v.module_id === m.module_id)!;
          return <p key={m.module_id}>{ship.module_names[m.module_id] ?? m.module_id}：{before.durability_points.toFixed(1)} → {m.durability_points.toFixed(1)}{m.durability_points === 0 ? " · 已损毁" : ""}</p>;
        })}</details>
        {!!ship.after.state.fuel_tanks?.length&&<details open><summary>燃料槽耐久</summary>{ship.after.state.fuel_tanks.map(t=>{
          const spec=ship.after.resources?.fuel_tanks?.find(s=>s.tank_id===t.tank_id),before=ship.before.state.fuel_tanks?.find(s=>s.tank_id===t.tank_id);
          return <p key={t.tank_id}>{spec?fuelTankName(spec,ship.module_names):t.tank_id}：{before?.durability_points.toFixed(1)} → {t.durability_points.toFixed(1)}{t.durability_points<=0?' · 已损毁':''}</p>;
        })}</details>}
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
    {onPrepare ? <section aria-label="下一场战前准备">
      <h4>下一场战前准备</h4>
      <p>保存本场结果后，返回战前准备管理各舰库存与预装弹种，再选择参加下一场交战的舰船。已有战损、余弹与未灭火情会保留。</p>
      <button disabled={busy || !canPrepare} onClick={onPrepare}>管理战后库存与下一场准备</button>
      {!canPrepare && <p>请先保存当前交战结果。</p>}
    </section> : <details open><summary>技术测试舰 · 再次交战</summary>
      {!library?.ships.length && <p>保存第一次战后结算后，可使用保留下来的舰船进入新交战。</p>}
      {library?.ships.map((s,i) => <p key={s.instance_id}>舰船 {i+1} · 船壳 {(s.hull_integrity*100).toFixed(1)}% · {serviceLabel[s.service.status]} · 保存版本 {s.revision} <button disabled={busy || !canDeploy || !s.can_deploy} onClick={() => onDeploy(s.instance_id, s.revision)}>使用舰船 {i+1} 进入新交战</button>{!s.can_deploy && s.service.status === "available" ? " · 尚有未保存结算" : ""}</p>)}
      <p>此入口仅用于技术测试舰，保留战损与资源并重新部署。自建舰请在“战前准备”中管理库存、预装弹药并进入下一场。</p>
    </details>}
  </section>;
}
