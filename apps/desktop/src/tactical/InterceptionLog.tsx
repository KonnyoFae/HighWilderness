import type {TacticalView} from './model';
import {layerName} from './layers';

export function InterceptionLog({view}:{view:TacticalView}) {
  const defense=view.snapshot.gunnery?.point_defense;
  if(!defense)return null;
  const ship=(id:string)=>view.geometry.ships.find(s=>s.id===id)?.name??id;
  return <section aria-label="近防拦截记录"><h3>近防拦截</h3>
    <p>{view.snapshot.gunnery?.observed_only?'本方近期拦截记录；威胁以当前操作舰的有效观测为准。':`弹体碰撞 ${defense.hits} 次 · 成功拦截 ${defense.intercepted} 枚`}</p>
    <details><summary>已知撞舰威胁与拦截记录</summary>
      {defense.threats.map(t=><p key={`${t.observer_ship_id}:${t.projectile_id}`}>{ship(t.observer_ship_id)}发现弹体 #{t.projectile_id}：预计 {t.remaining_s.toFixed(1)} 秒后接近{ship(t.ship_id)} · 耐久 {t.durability} · {layerName(t.height_layer)}</p>)}
      {defense.recent.slice(-12).reverse().map(e=><p key={`${e.step}:${e.round_id}`}>{ship(e.source_ship_id)} · 弹体 #{e.projectile_id} · 耐久 {e.durability_before} → {e.durability_after} · {e.intercepted?'拦截成功':'命中，弹体继续飞行'} · {layerName(e.height_layer)}</p>)}
      {!defense.recent.length&&<p>暂无弹体碰撞记录。</p>}
    </details>
  </section>;
}
