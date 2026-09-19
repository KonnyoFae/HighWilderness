import type { TacticalView } from './model';
import { goodName } from './preparation';
import { fuelTankName } from './settlement';

export interface ShipStores {
  ship_id: string; capacity_cm3: number; used_volume_cm3: number; over_capacity: boolean;
  cargo: { good_id: string; quantity: number; reserved: number }[];
  magazines: { module_id: string; quantity: number; capacity: number; reserved: number; available: boolean }[];
}

export function ShipStoresPanel({ view, shipId, onMissiles, onDamage }: {
  view: TacticalView; shipId: string; onMissiles: () => void; onDamage: () => void;
}) {
  const g = view.snapshot.gunnery, stores = g?.stores?.find(s => s.ship_id === shipId);
  const names = Object.fromEntries(view.geometry.ships.find(s => s.id === shipId)?.modules.map(m => [m.id, m.name]) ?? []);
  const fuel = g?.fuel?.find(s => s.ship_id === shipId);
  const missiles = g?.missiles?.ships.find(s => s.ship_id === shipId)?.state;
  const guns = g?.weapons.filter(w => w.ship_id === shipId) ?? [];
  const damage = g?.damage_control?.devices.filter(d => d.ship_id === shipId) ?? [];
  return <section className="battle-cargo" aria-label="本舰库存">
    <dl className="tactical-stats">
      <dt>货舱占用</dt><dd>{stores ? `${(stores.used_volume_cm3 / 1e6).toFixed(1)} / ${(stores.capacity_cm3 / 1e6).toFixed(1)} m³` : '当前快照未提供'}</dd>
      <dt>炮弹待发</dt><dd>{guns.reduce((sum, w) => sum + w.ready_rounds, 0)} 发</dd>
      <dt>导弹待发 / 库存</dt><dd>{missiles?.launchers.reduce((sum, l) => sum + l.ready.length, 0) ?? 0} / {missiles?.magazines.reduce((sum, m) => sum + m.stock.length, 0) ?? 0} 枚</dd>
      <dt>燃料</dt><dd>{fuel ? fuel.total_units.toFixed(2) : '无燃料槽'}</dd>
    </dl>
    {stores?.over_capacity && <p role="status">返还材料已保留，货舱暂时超容，不能继续新增装载。</p>}
    <h4>货物材料</h4>
    <p className="muted">货物共用全舰仓容；预留份额已计入库存，暂不能用于其他作业。</p>
    {!stores?.cargo.length && <p>暂无货物材料。</p>}
    {stores?.cargo.map(c => <p key={c.good_id}>{goodName(c.good_id)} <strong>{c.quantity}</strong> · 可用 {Math.max(0, c.quantity - c.reserved)}{c.reserved > 0 ? ` · 预留 ${c.reserved}` : ''}</p>)}
    <details><summary>弹药资源库（{stores?.magazines.length ?? 0}）</summary>
      {stores?.magazines.map(m => <p key={m.module_id}>{names[m.module_id] ?? m.module_id}：{m.quantity} / {m.capacity} 点 · 预留 {m.reserved}{!m.available ? ' · 不可供弹' : ''}</p>)}
    </details>
    <details><summary>燃料槽（{fuel?.tanks.length ?? 0}）</summary>
      {fuel?.tanks.map(t => <p key={t.tank_id}>{fuelTankName(t, names)}：{t.quantity_units.toFixed(2)} / {t.capacity_units}{t.durability_points <= 0 ? ' · 已毁' : ''}</p>)}
      <p className="muted">当前推进不消耗燃料；储槽被毁会损失其中燃料。</p>
    </details>
    <details><summary>损管储备（{damage.length}）</summary>{damage.map(d => <p key={d.module_id}>{names[d.module_id] ?? d.module_id}：{d.quantity_units} / {d.capacity_units}{d.remaining_preparation_steps > 0 ? ' · 准备中' : ''}</p>)}</details>
    <div className="editor-row"><button onClick={onMissiles}>导弹装填与库存</button><button onClick={onDamage}>前往损管</button></div>
  </section>;
}
