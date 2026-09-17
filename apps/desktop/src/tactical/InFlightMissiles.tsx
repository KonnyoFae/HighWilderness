import type {DisplayProjectile} from './model';
import type {ObservationShip} from './FireControlPanel';
import {layerName} from './layers';
const phases:Record<string,string>={boost:'助推',powered:'动力',coast:'滑行'};
const states:Record<string,string>={midcourse:'中段飞行',search:'搜索',acquiring:'锁定确认',tracking:'追踪',lost:'失锁直飞',memory:'记忆跟踪',rescan:'8 字重搜',datalink:'数据链引导'};
export function InFlightMissiles({projectiles,observation,names,friendlyIds,disabled,onRetarget}:{projectiles:DisplayProjectile[];observation?:ObservationShip;names:Record<string,string>;friendlyIds:string[];disabled:boolean;onRetarget:(id:number,target:string|number)=>void}) {
  const rows=projectiles.filter(p=>p.missile&&friendlyIds.includes(p.ship_id));
  if(!rows.length)return <p role="status">当前没有友方在途导弹。</p>;
  const linked=observation?.devices.some(d=>d.kind==='datalink'&&!d.reason&&d.durability>0);
  const contacts=observation?.contacts.filter(c=>c.valid&&c.status==='tracked')??[];
  return <section aria-label="在途导弹制导"><h3>友方在途导弹（{rows.length}）</h3>{rows.map(p=><article className="observation-card" key={p.id} data-flight={p.id}>
    <strong>{p.missile!.interceptor?'拦截弹':'导弹'} #{p.id} · {names[p.ship_id]??'友舰'}</strong>
    <p>{phases[p.missile!.phase]} · <span data-seeker-state={p.missile!.seeker_state}>{states[p.missile!.seeker_state]??p.missile!.seeker_state}</span> · {layerName(p.height_layer??'upper')}</p>
    <p>{Math.hypot(...p.velocity_mps).toFixed(0)} 米/秒 · 耐久 {p.durability} / {p.maximum_durability} · 剩余 {p.missile!.remaining_s.toFixed(1)} 秒</p>
    {p.missile!.target_id&&<p>锁定：{names[p.missile!.target_id]??(typeof p.missile!.target_id==='number'?`来袭弹体 #${p.missile!.target_id}`:p.missile!.target_id.startsWith('ew.')?'主动诱饵':'目标')}</p>}
    {p.missile!.interceptor&&<p>单次拦截伤害 {p.missile!.interception_damage} · 判定半径 {p.missile!.interception_radius_m} 米</p>}
    {p.missile!.link_sender&&<p>情报更新：{names[p.missile!.link_sender]??'友舰数据链'}</p>}
    {p.missile!.datalink&&<label>数据链改攻<select aria-label={`导弹 ${p.id} 数据链改攻`} value="" disabled={disabled||!linked}
      onChange={e=>e.target.value&&onRetarget(p.id,contacts.find(c=>String(c.id)===e.target.value)!.id)}><option value="">选择本层有效目标</option>{contacts.filter(c=>c.height_layer===p.height_layer&&(p.missile!.interceptor?c.kind!=='ship':c.kind==='ship')).map(c=><option key={c.id} value={c.id}>{names[c.id]??`来袭弹体 #${c.id}`}</option>)}</select></label>}
  </article>)}<p className="muted">记忆与重搜依赖最后有效观测；数据链可更新目标情报，不能恢复动力、寿命或改变作用层。</p></section>;
}
