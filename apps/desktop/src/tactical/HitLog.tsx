import type {TacticalView} from './model';
import {ammunitionName} from './ammunition';
import {layerName} from './layers';

export function HitLog({view, open=false}:{view:TacticalView;open?:boolean}) {
  const damage=view.snapshot.gunnery?.damage;
  if(!damage)return null;
  return <details open={open} aria-label="甲板命中记录"><summary>毁伤记录 · 命中 {damage.hits} 次 · 殉爆 {damage.magazine_detonations ?? 0} 次</summary>
    {(damage.magazine_explosions ?? []).slice(-8).reverse().map(event=>{
      const ship=view.geometry.ships.find(s=>s.id===event.ship_id);
      const name=(id:string)=>ship?.modules.find(m=>m.id===id)?.name ?? id;
      return <article key={`${event.step}:${event.ship_id}:${event.module_id}`} className="deck-hit-record" aria-label="弹药库殉爆记录">
        <p><strong>{ship?.name ?? event.ship_id} · {name(event.module_id)}殉爆</strong> · 第 {event.deck_level} 甲板 · {layerName(event.height_layer)}</p>
        <p>损失 {event.ammunition_resources} 弹药资源 · 波及半径 {event.radius_m.toFixed(1)} 米 · 不连锁殉爆</p>
        {event.module_losses.length>0 && <p>{event.module_losses.map(row=>`${name(row.module_id)} −${row.damage_points.toFixed(1)}`).join('；')}</p>}
      </article>;
    })}
    {damage.recent.slice(-8).reverse().map(hit=>{
      const ship=view.geometry.ships.find(s=>s.id===hit.ship_id),selection=hit.deck_selection;
      return <article key={hit.projectile_id} className="deck-hit-record">
        <p><strong>{ship?.name ?? hit.ship_id} · 命中第 {hit.deck_level} 甲板</strong> · {layerName(hit.height_layer)}
          {' · '}{hit.projectile_type ? ammunitionName(hit.projectile_type)+' · ' : ''}
          {{penetrated:'击穿',stopped:'装甲阻挡',ricochet:'跳弹',module:'外部模块命中'}[hit.outcome] ?? hit.outcome}</p>
        {hit.module_ids.length>0 && <p>{hit.module_ids.map(id=>ship?.modules.find(m=>m.id===id)?.name ?? id).join('、')}：每个模块 −{hit.module_damage.toFixed(1)}</p>}
        {selection && <small>
          {selection.preferred_levels.length ? `瞄准偏好：${selection.preferred_levels.map(level=>`第 ${level} 甲板`).join('、')}。` : '未指定甲板偏好。'}
          本发候选：{selection.probabilities.map(row=>`第 ${row.deck_level} 甲板 ${(row.probability*100).toFixed(1)}%`).join('；')}。
        </small>}
      </article>;
    })}
    {!damage.recent.length && <p>尚无弹药命中。</p>}
  </details>;
}
