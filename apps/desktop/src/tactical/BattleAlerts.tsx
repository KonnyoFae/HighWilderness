import type { TacticalView } from './model';

export interface BattleAlert { shipId: string; page: 'ship' | 'damage' | 'fire'; text: string; priority: number }
export function battleAlerts(view: TacticalView, directId: string): BattleAlert[] {
  const side = view.geometry.ships.find(s => s.id === directId)?.side_id, alerts: BattleAlert[] = [];
  for (const ship of view.geometry.ships.filter(s => s.side_id === side)) {
    const pose = view.snapshot.ships.find(s => s.id === ship.id);
    if (!pose || pose.wreck || pose.physical_status === 'exited') continue;
    if (pose.descent) alerts.push({ shipId: ship.id, page: 'damage', priority: 0, text: `${ship.name} · ${pose.descent.paused ? '下坠冻结' : '正在下坠'}` });
    const fires = view.snapshot.gunnery?.damage_control?.fires.filter(f => f.ship_id === ship.id) ?? [];
    if (fires.length) alerts.push({ shipId: ship.id, page: 'damage', priority: 1, text: `${ship.name} · ${fires.length} 处火情` });
    const threats = view.snapshot.gunnery?.point_defense?.threats.filter(t => t.ship_id === ship.id) ?? [];
    const ids = new Set(threats.map(t => t.projectile_id));
    if (ids.size) alerts.push({ shipId: ship.id, page: 'fire', priority: 2, text: `${ship.name} · ${ids.size} 个撞舰威胁` });
  }
  return alerts.sort((a, b) => a.priority - b.priority || a.shipId.localeCompare(b.shipId));
}
export function BattleAlerts({ view, directId, onInspect }: { view: TacticalView; directId: string; onInspect: (alert: BattleAlert) => void }) {
  const alerts = battleAlerts(view, directId);
  if (!alerts.length) return null;
  return <div className="battle-alerts" aria-label="舰队警报">{alerts.slice(0, 3).map(a => <button key={`${a.shipId}:${a.page}:${a.priority}`} onClick={() => onInspect(a)}>{a.text} ›</button>)}
    {alerts.length > 3 && <details><summary>另 {alerts.length - 3} 条警报</summary>{alerts.slice(3).map(a => <button key={`${a.shipId}:${a.priority}`} onClick={() => onInspect(a)}>{a.text} ›</button>)}</details>}
  </div>;
}
