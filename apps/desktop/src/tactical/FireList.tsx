import type {FireView} from './DamageControlPanel';

export function FireList({fires,name}:{fires:FireView[];name:(id:string)=>string}) {
  if(!fires.length)return <p>当前无火情</p>;
  return <details open className="fire-list"><summary>当前火情 · {fires.length} 处</summary>
    <ul>{fires.map(f=><li key={f.zone_id??f.module_id}>
      {f.label??name(f.module_id??'未知位置')}{f.label&&f.module_id?` · ${name(f.module_id)}`:''}
      {' · '}强度 {(f.intensity_units/1000).toFixed(2)} · 最长剩余 {(f.remaining_steps/60).toFixed(1)} 秒
    </li>)}</ul>
  </details>;
}
